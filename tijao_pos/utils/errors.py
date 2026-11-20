"""Friendly errors for the shop screen.

Every API endpoint is wrapped with ``@friendly_errors``. Whatever goes wrong,
the UI gets one short plain-English message (never a traceback), and the
work done by the call is rolled back to a savepoint so a failed sale leaves nothing behind.
"""

from __future__ import annotations

import functools
import re

import frappe

GENERIC = "Something went wrong and nothing was saved. Please try again."


class TijaoError(frappe.ValidationError):
	"""A message that is already safe and friendly to show the shop user."""


def friendly_errors(fn):
	@functools.wraps(fn)
	def wrapper(*args, **kwargs):
		from tijao_pos.utils.lines import LineError

		savepoint = "tijao_" + frappe.generate_hash(length=10)
		frappe.db.savepoint(savepoint)
		try:
			return fn(*args, **kwargs)
		except TijaoError as e:
			_fail(savepoint, str(e), TijaoError)
		except LineError as e:
			_fail(savepoint, str(e), TijaoError)
		except frappe.AuthenticationError:
			_fail(savepoint, "Please log in.", frappe.AuthenticationError)
		except frappe.PermissionError:
			_fail(savepoint, "You are not allowed to do this. Ask the owner.", frappe.PermissionError)
		except frappe.DoesNotExistError as e:
			_fail(savepoint, _clean(str(e)) or "That record was not found.", frappe.DoesNotExistError)
		except Exception as e:
			_fail(savepoint, translate(e), TijaoError, log=not isinstance(e, frappe.ValidationError))

	return wrapper


def translate(exc: Exception) -> str:
	"""Turn an ERPNext/Frappe exception into one short sentence."""
	from erpnext.stock.stock_ledger import NegativeStockError

	if (
		isinstance(exc, NegativeStockError)
		or "insufficient stock" in str(exc).lower()
		or "needed in" in str(exc).lower()
	):
		return "Not enough stock for this. Check the stock list and try again."
	if isinstance(exc, frappe.ValidationError):
		msg = _clean(str(exc)) or _last_message()
		if msg and len(msg) <= 200:
			return msg
		return "This could not be saved: " + (msg[:180] + "…" if msg else "please check the details.")
	return GENERIC


def _fail(savepoint: str, message: str, exc_class: type[Exception], log: bool = False) -> None:
	traceback = frappe.get_traceback() if log else None
	# Undo everything this call wrote (a failed sale leaves nothing behind),
	# without touching anything the caller did before us.
	frappe.db.rollback(save_point=savepoint)
	if traceback:
		frappe.log_error(title="Tijao POS error", message=traceback)
	# Drop ERPNext's own msgprints (HTML, links, jargon) so only our message reaches the UI.
	frappe.clear_messages()
	frappe.throw(message, exc_class, title="Tijao POS")


def _last_message() -> str:
	try:
		import json

		for raw in reversed(frappe.message_log or []):
			msg = raw.get("message") if isinstance(raw, dict) else json.loads(raw).get("message")
			if msg:
				return _clean(msg)
	except Exception:
		pass
	return ""


def _clean(text: str) -> str:
	"""Strip HTML tags/entities and collapse whitespace."""
	text = re.sub(r"<[^>]+>", "", text or "")
	text = text.replace("&nbsp;", " ").replace("&amp;", "&")
	return re.sub(r"\s+", " ", text).strip()

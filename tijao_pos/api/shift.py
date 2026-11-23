"""Cash-drawer shifts. Blind close: cashiers never get cash totals while a shift is open."""

import frappe

from tijao_pos.services import shift_service
from tijao_pos.utils import permissions
from tijao_pos.utils.errors import friendly_errors
from tijao_pos.utils.settings import get_settings


@frappe.whitelist(methods=["GET"])
@friendly_errors
def current():
	"""The open shift (or null). Owners also see live cash totals."""
	permissions.require_cashier()
	return shift_service.get_current(get_settings(), include_cash=permissions.is_owner())


@frappe.whitelist(methods=["POST"])
@friendly_errors
def open_shift(opening_float):
	permissions.require_cashier()
	return shift_service.open_shift(get_settings(), opening_float)


@frappe.whitelist(methods=["POST"])
@friendly_errors
def add_payout(amount, reason: str, note: str | None = None):
	permissions.require_cashier()
	return shift_service.add_payout(get_settings(), amount, reason, note)


@frappe.whitelist(methods=["POST"])
@friendly_errors
def close_shift(counted_cash, notes: str | None = None):
	permissions.require_cashier()
	return shift_service.close_shift(get_settings(), counted_cash, notes, is_owner=permissions.is_owner())


@frappe.whitelist(methods=["GET"])
@friendly_errors
def list_shifts(limit=30):
	permissions.require_owner()
	return shift_service.list_shifts(get_settings(), limit=limit)


@frappe.whitelist(methods=["GET"])
@friendly_errors
def get_shift(name: str):
	permissions.require_owner()
	return shift_service.get_shift(get_settings(), name)


@frappe.whitelist(methods=["POST"])
@friendly_errors
def review_shift(name: str, note: str | None = None):
	permissions.require_owner()
	return shift_service.review_shift(get_settings(), name, note)

"""Cash-drawer shifts.

Rules (agreed with the owner):
* The shop has ONE cash drawer, so at most one shift is open at a time.
* Nobody can sell without an open shift; every sale is linked to it.
* Pay-outs (cash taken from the drawer) are recorded on the shift.
* Blind close: the cashier enters the counted cash first; expected cash and
  over/short are only computed and shown after that. While a shift is open,
  non-owners never receive cash totals from the API.
* Totals are frozen on the shift when it closes.
"""

from __future__ import annotations

import frappe
from frappe.utils import cint, flt, get_fullname, now_datetime

from tijao_pos.utils.elevate import real_user, writes_as_system
from tijao_pos.utils.errors import TijaoError
from tijao_pos.utils.lines import to_number
from tijao_pos.utils.settings import payment_mode_key

SHIFT = "Tijao Shift"
PAYOUT_REASONS = ("Paid distributor", "Transport", "Owner took cash", "Shop supplies", "Other")


# ---------------------------------------------------------------------------
# Lookups
# ---------------------------------------------------------------------------


def open_shift_name() -> str | None:
	return frappe.db.get_value(SHIFT, {"status": "Open"}, "name", order_by="opened_at desc")


def require_open_shift() -> str:
	name = open_shift_name()
	if not name:
		raise TijaoError("No shift is open. Open a shift (count the cash in the drawer) before selling.")
	return name


def _get(name: str):
	if not name or not frappe.db.exists(SHIFT, name):
		raise TijaoError("That shift was not found.")
	return frappe.get_doc(SHIFT, name)


# ---------------------------------------------------------------------------
# Totals
# ---------------------------------------------------------------------------


def compute_totals(settings, shift) -> frappe._dict:
	"""Live totals for a shift from its linked sales and pay-outs."""
	rows = frappe.db.sql(
		"""select p.mode_of_payment, coalesce(sum(p.base_amount), 0) as amount
		from `tabSales Invoice Payment` p
		join `tabSales Invoice` si on si.name = p.parent and p.parenttype = 'Sales Invoice'
		where si.tijao_shift = %s and si.docstatus = 1 and si.is_return = 0
		group by p.mode_of_payment""",
		shift.name,
		as_dict=True,
	)
	cash = mpesa = other = 0.0
	for r in rows:
		key = payment_mode_key(settings, r.mode_of_payment)
		if key == "cash":
			cash += flt(r.amount)
		elif key == "mpesa":
			mpesa += flt(r.amount)
		else:
			other += flt(r.amount)
	counts = dict(
		frappe.db.sql(
			"""select docstatus, count(*) from `tabSales Invoice`
			where tijao_shift = %s and docstatus in (1, 2) group by docstatus""",
			shift.name,
		)
	)
	payouts = sum(flt(p.amount) for p in shift.payouts)
	expected = flt(shift.opening_float) + cash - payouts
	return frappe._dict(
		sales_count=cint(counts.get(1)),
		voided_count=cint(counts.get(2)),
		cash_sales=flt(cash, 2),
		mpesa_sales=flt(mpesa, 2),
		other_sales=flt(other, 2),
		payouts_total=flt(payouts, 2),
		expected_cash=flt(expected, 2),
	)


def to_dict(settings, shift, *, include_cash: bool) -> dict:
	"""Shift for the UI. ``include_cash=False`` hides every number that would
	let a cashier work out the expected cash before a blind close."""
	out = {
		"name": shift.name,
		"status": shift.status,
		"user": shift.user,
		"cashier_name": shift.cashier_name,
		"opened_at": str(shift.opened_at),
		"opening_float": flt(shift.opening_float),
		"is_mine": shift.user == real_user(),
		"payouts": [
			{
				"amount": flt(p.amount),
				"reason": p.reason,
				"note": p.note,
				"recorded_at": str(p.recorded_at),
				"recorded_by": get_fullname(p.recorded_by) if p.recorded_by else None,
			}
			for p in shift.payouts
		],
	}
	if shift.status == "Closed":
		out.update(
			{
				"closed_at": str(shift.closed_at),
				"closed_by": get_fullname(shift.closed_by) if shift.closed_by else None,
				"counted_cash": flt(shift.counted_cash),
				"notes": shift.notes,
				"sales_count": cint(shift.sales_count),
				"voided_count": cint(shift.voided_count),
				"cash_sales": flt(shift.cash_sales),
				"mpesa_sales": flt(shift.mpesa_sales),
				"payouts_total": flt(shift.payouts_total),
				"expected_cash": flt(shift.expected_cash),
				"cash_difference": flt(shift.cash_difference),
				"reviewed": cint(shift.reviewed),
				"reviewed_by": get_fullname(shift.reviewed_by) if shift.reviewed_by else None,
				"reviewed_at": str(shift.reviewed_at) if shift.reviewed_at else None,
				"review_note": shift.review_note,
			}
		)
	else:
		live = compute_totals(settings, shift)
		out["sales_count"] = live.sales_count  # a count, not money: fine for everyone
		if include_cash:
			out.update(
				{
					k: live[k]
					for k in ("cash_sales", "mpesa_sales", "payouts_total", "expected_cash", "voided_count")
				}
			)
	return out


# ---------------------------------------------------------------------------
# Actions
# ---------------------------------------------------------------------------


def get_current(settings, *, include_cash: bool) -> dict | None:
	name = open_shift_name()
	return to_dict(settings, _get(name), include_cash=include_cash) if name else None


@writes_as_system
def open_shift(settings, opening_float) -> dict:
	float_amt = to_number(opening_float, "Opening cash", allow_blank=False)
	if float_amt < 0:
		raise TijaoError("Opening cash can't be negative.")
	existing = open_shift_name()
	if existing:
		who = frappe.db.get_value(SHIFT, existing, "cashier_name")
		raise TijaoError(f"A shift is already open ({who}). Close it first.")
	user = real_user()
	shift = frappe.get_doc(
		{
			"doctype": SHIFT,
			"status": "Open",
			"user": user,
			"cashier_name": get_fullname(user),
			"opened_at": now_datetime(),
			"opening_float": flt(float_amt, 2),
		}
	)
	shift.insert(ignore_permissions=True)
	return to_dict(settings, shift, include_cash=False)


@writes_as_system
def add_payout(settings, amount, reason: str, note: str | None = None) -> dict:
	shift = _get(require_open_shift())
	amt = to_number(amount, "Amount", allow_blank=False)
	if amt <= 0:
		raise TijaoError("Enter how much cash was taken out.")
	if reason not in PAYOUT_REASONS:
		raise TijaoError("Choose a reason for the pay-out.")
	note = (note or "").strip()[:140] or None
	if reason == "Other" and not note:
		raise TijaoError("Write a short note saying what the cash was for.")
	shift.append(
		"payouts",
		{
			"amount": flt(amt, 2),
			"reason": reason,
			"note": note,
			"recorded_at": now_datetime(),
			"recorded_by": real_user(),
		},
	)
	shift.save(ignore_permissions=True)
	return to_dict(settings, shift, include_cash=False)


@writes_as_system
def close_shift(settings, counted_cash, notes: str | None, *, is_owner: bool) -> dict:
	"""Blind close: freeze totals and return the over/short only now."""
	shift = _get(require_open_shift())
	user = real_user()
	if shift.user != user and not is_owner:
		raise TijaoError(f"Only {shift.cashier_name} or the owner can close this shift.")
	counted = to_number(counted_cash, "Cash counted", allow_blank=False)
	if counted < 0:
		raise TijaoError("Cash counted can't be negative.")

	totals = compute_totals(settings, shift)
	shift.update(
		{
			"status": "Closed",
			"closed_at": now_datetime(),
			"closed_by": user,
			"counted_cash": flt(counted, 2),
			"notes": (notes or "").strip()[:500] or None,
			"sales_count": totals.sales_count,
			"voided_count": totals.voided_count,
			"cash_sales": totals.cash_sales,
			"mpesa_sales": totals.mpesa_sales,
			"payouts_total": totals.payouts_total,
			"expected_cash": totals.expected_cash,
			"cash_difference": flt(counted - totals.expected_cash, 2),
		}
	)
	shift.save(ignore_permissions=True)
	return to_dict(settings, shift, include_cash=True)


@writes_as_system
def review_shift(settings, name: str, note: str | None = None) -> dict:
	shift = _get(name)
	if shift.status != "Closed":
		raise TijaoError("Only a closed shift can be marked as checked.")
	shift.update(
		{
			"reviewed": 1,
			"reviewed_by": real_user(),
			"reviewed_at": now_datetime(),
			"review_note": (note or "").strip()[:500] or None,
		}
	)
	shift.save(ignore_permissions=True)
	return to_dict(settings, shift, include_cash=True)


def list_shifts(settings, limit: int = 30) -> list[dict]:
	limit = max(1, min(cint(limit) or 30, 200))
	names = frappe.get_all(SHIFT, order_by="opened_at desc", limit=limit, pluck="name")
	return [to_dict(settings, frappe.get_doc(SHIFT, n), include_cash=True) for n in names]


def get_shift(settings, name: str) -> dict:
	shift = _get(name)
	data = to_dict(settings, shift, include_cash=True)
	sales = frappe.get_all(
		"Sales Invoice",
		filters={"tijao_shift": shift.name, "docstatus": ["in", [1, 2]]},
		fields=["name", "posting_date", "posting_time", "grand_total", "rounded_total", "docstatus"],
		order_by="creation asc",
	)
	data["sales"] = [
		{
			"invoice": s.name,
			"posting_time": str(s.posting_time).split(".")[0],
			"total": flt(s.rounded_total or s.grand_total),
			"status": "voided" if s.docstatus == 2 else "paid",
		}
		for s in sales
	]
	if shift.status == "Closed":
		# Sales voided after the close change the drawer: flag it for the owner.
		live = compute_totals(settings, shift)
		data["voided_after_close"] = max(0, live.voided_count - cint(shift.voided_count))
	return data

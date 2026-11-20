"""Selling: one sale = one submitted POS Sales Invoice (is_pos=1, update_stock=1)."""

from __future__ import annotations

import re

import frappe
from frappe.utils import cint, flt, get_fullname, nowdate

from tijao_pos.services.catalog_service import get_item_units, get_selling_prices, get_stock_qty
from tijao_pos.utils.elevate import stamp_owner, writes_as_system
from tijao_pos.utils.errors import TijaoError
from tijao_pos.utils.lines import describe_qty, normalise_sale_lines
from tijao_pos.utils.settings import mode_of_payment, payment_mode_key

CLIENT_REF_FIELD = "tijao_client_ref"
_CLIENT_REF_RE = re.compile(r"^[A-Za-z0-9_-]{8,64}$")


@writes_as_system
def make_sale(
	settings,
	items: list[dict],
	payment_mode: str,
	mpesa_ref: str | None = None,
	client_ref: str | None = None,
) -> dict:
	client_ref = _validate_client_ref(client_ref)

	# Idempotency: the screen retries with the same client_ref -> same invoice.
	existing = find_by_client_ref(client_ref)
	if existing:
		return get_sale(settings, existing, duplicate=True)

	mop = mode_of_payment(settings, payment_mode)
	mpesa_ref = _validate_mpesa_ref(mpesa_ref) if payment_mode_key(settings, mop) == "mpesa" else None
	lines = normalise_sale_lines(items)
	codes = [line["item_code"] for line in lines]

	units = get_item_units(codes)
	prices = get_selling_prices(codes, settings)
	stock = get_stock_qty(codes, settings.warehouse)

	for line in lines:
		code = line["item_code"]
		u = units.get(code)
		if not u or u.disabled or not u.is_stock_item:
			raise TijaoError(f"'{code}' is not a product in this shop.")
		if not prices.get(code):
			raise TijaoError(f"{u.item_name} has no selling price. Ask the owner to set one.")
		have = flt(stock[code].actual_qty) if code in stock else 0
		if line["qty"] > have:
			left = describe_qty(have, u.sale_uom, u.bulk_uom, u.per_bulk)
			raise TijaoError(f"Not enough {u.item_name}: only {left} left.")

	si = frappe.new_doc("Sales Invoice")
	si.update(
		{
			"company": settings.company,
			"customer": settings.walk_in_customer,
			"is_pos": 1,
			"update_stock": 1,
			"pos_profile": settings.pos_profile,
			"set_warehouse": settings.warehouse,
			"selling_price_list": settings.selling_price_list,
			"posting_date": nowdate(),
			CLIENT_REF_FIELD: client_ref,
		}
	)
	for line in lines:
		u = units[line["item_code"]]
		si.append(
			"items",
			{
				"item_code": u.item_code,
				"qty": line["qty"],
				"uom": u.sale_uom,
				"conversion_factor": 1,
				"rate": prices[u.item_code],
				"warehouse": settings.warehouse,
			},
		)

	# Pulls customer/accounts/payment rows from the POS Profile (for_validate=False).
	si.set_missing_values()
	si.calculate_taxes_and_totals()
	_set_single_payment(si, mop, mpesa_ref)

	si.flags.ignore_permissions = True
	frappe.db.savepoint("tijao_sale_insert")
	try:
		si.insert()
		si.submit()
	except (frappe.UniqueValidationError, frappe.DuplicateEntryError):
		# A parallel retry with the same client_ref won the race.
		frappe.db.rollback(save_point="tijao_sale_insert")
		existing = find_by_client_ref(client_ref)
		if not existing:
			raise
		return get_sale(settings, existing, duplicate=True)

	stamp_owner(si)
	return get_sale(settings, si.name)


def find_by_client_ref(client_ref: str) -> str | None:
	return frappe.db.get_value("Sales Invoice", {CLIENT_REF_FIELD: client_ref}, "name")


def _set_single_payment(si, mop: str, mpesa_ref: str | None) -> None:
	rows = [p for p in si.get("payments") if p.mode_of_payment == mop]
	if not rows:
		raise TijaoError(f"{mop} is not set up on the POS Profile. Ask the owner to check Settings.")
	payment = rows[0]
	payment.amount = flt(si.rounded_total or si.grand_total)
	payment.reference_no = mpesa_ref
	payment.default = 1
	si.set("payments", [payment])
	si.calculate_taxes_and_totals()


def _validate_client_ref(client_ref: str | None) -> str:
	client_ref = (client_ref or "").strip()
	if not _CLIENT_REF_RE.match(client_ref):
		raise TijaoError("This sale is missing its id. Refresh the page and try again.")
	return client_ref


def _validate_mpesa_ref(ref: str | None) -> str | None:
	ref = re.sub(r"\s+", "", ref or "").upper()
	if not ref:
		return None
	if not re.fullmatch(r"[A-Z0-9]{6,20}", ref):
		raise TijaoError("The M-Pesa code should be letters and numbers only, like QK12AB34CD.")
	return ref


# ---------------------------------------------------------------------------
# Reading sales
# ---------------------------------------------------------------------------


def _get_pos_invoice(settings, invoice: str):
	if not invoice or not frappe.db.exists("Sales Invoice", invoice):
		raise TijaoError("That sale was not found.")
	doc = frappe.get_doc("Sales Invoice", invoice)
	if not doc.is_pos or doc.company != settings.company or doc.docstatus == 0:
		raise TijaoError("That sale was not found.")
	return doc


def get_sale(settings, invoice: str, duplicate: bool = False) -> dict:
	doc = _get_pos_invoice(settings, invoice)
	payment = doc.payments[0] if doc.get("payments") else None
	return {
		"invoice": doc.name,
		"client_ref": doc.get(CLIENT_REF_FIELD),
		"duplicate": duplicate,
		"status": "voided" if doc.docstatus == 2 else "paid",
		"posting_date": str(doc.posting_date),
		"posting_time": str(doc.posting_time).split(".")[0],
		"cashier": get_fullname(doc.owner),
		"currency": doc.currency,
		"lines": [
			{
				"item_code": d.item_code,
				"item_name": d.item_name,
				"qty": flt(d.qty),
				"uom": d.uom,
				"rate": flt(d.rate),
				"amount": flt(d.amount),
			}
			for d in doc.items
		],
		"net_total": flt(doc.net_total),
		"total": flt(doc.rounded_total or doc.grand_total),
		"payment": {
			"mode": payment_mode_key(settings, payment.mode_of_payment) if payment else None,
			"mode_of_payment": payment.mode_of_payment if payment else None,
			"amount": flt(payment.amount) if payment else 0,
			"mpesa_ref": payment.reference_no if payment else None,
		},
		"receipt": {
			"header": settings.receipt_header or settings.company,
			"footer": settings.receipt_footer,
		},
	}


def recent_sales(settings, limit: int = 20) -> list[dict]:
	limit = max(1, min(cint(limit) or 20, 100))
	invoices = frappe.get_all(
		"Sales Invoice",
		filters={"is_pos": 1, "company": settings.company, "docstatus": ["in", [1, 2]], "is_return": 0},
		fields=["name", "posting_date", "posting_time", "grand_total", "rounded_total", "docstatus", "owner"],
		order_by="creation desc",
		limit=limit,
	)
	if not invoices:
		return []
	names = [i.name for i in invoices]
	payments = {}
	for p in frappe.get_all(
		"Sales Invoice Payment",
		filters={"parenttype": "Sales Invoice", "parent": ["in", names]},
		fields=["parent", "mode_of_payment", "reference_no"],
		order_by="idx asc",
	):
		payments.setdefault(p.parent, p)
	counts = {
		r.parent: flt(r.qty)
		for r in frappe.get_all(
			"Sales Invoice Item",
			filters={"parenttype": "Sales Invoice", "parent": ["in", names]},
			fields=["parent", "sum(qty) as qty"],
			group_by="parent",
		)
	}
	out = []
	for i in invoices:
		p = payments.get(i.name)
		out.append(
			{
				"invoice": i.name,
				"posting_date": str(i.posting_date),
				"posting_time": str(i.posting_time).split(".")[0],
				"total": flt(i.rounded_total or i.grand_total),
				"items_count": counts.get(i.name, 0),
				"payment_mode": payment_mode_key(settings, p.mode_of_payment) if p else None,
				"mpesa_ref": p.reference_no if p else None,
				"status": "voided" if i.docstatus == 2 else "paid",
				"cashier": get_fullname(i.owner),
			}
		)
	return out


@writes_as_system
def void_sale(settings, invoice: str) -> dict:
	doc = _get_pos_invoice(settings, invoice)
	if doc.docstatus == 2:
		raise TijaoError("This sale was already voided.")
	doc.flags.ignore_permissions = True
	# ERPNext reverses the stock ledger and accounting entries on cancel.
	doc.cancel()
	return get_sale(settings, doc.name)

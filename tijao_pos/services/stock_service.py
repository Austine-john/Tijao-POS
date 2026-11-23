"""Stock: levels, deliveries (Purchase Receipt), counts and opening stock (Stock Reconciliation)."""

from __future__ import annotations

import frappe
from frappe.utils import flt, nowdate

from tijao_pos.services.catalog_service import (
	get_buying_prices_per_bulk,
	get_item_units,
	get_stock_qty,
	is_low,
	list_item_codes,
)
from tijao_pos.utils.elevate import stamp_owner, writes_as_system
from tijao_pos.utils.errors import TijaoError
from tijao_pos.utils.lines import describe_qty, normalise_bulk_lines, split_units, to_sale_units

# ---------------------------------------------------------------------------
# Levels
# ---------------------------------------------------------------------------


def get_stock_levels(settings, item_codes: list[str] | None = None) -> list[dict]:
	codes = item_codes if item_codes is not None else list_item_codes(settings)
	if not codes:
		return []
	units = get_item_units(codes)
	bins = get_stock_qty(codes, settings.warehouse)
	rows = []
	for code in codes:
		u = units.get(code)
		if not u:
			continue
		b = bins.get(code)
		qty = flt(b.actual_qty) if b else 0
		split = split_units(qty, u.per_bulk)
		rows.append(
			{
				"item_code": code,
				"item_name": u.item_name,
				"qty": qty,
				"uom": u.sale_uom,
				"bulk_uom": u.bulk_uom,
				"per_bulk": u.per_bulk,
				"bulk_qty": split.bulk,
				"loose_qty": split.loose,
				"stock_text": describe_qty(qty, u.sale_uom, u.bulk_uom, u.per_bulk),
				"stock_value": flt(b.stock_value, 2) if b else 0,
				"valuation_rate": flt(b.valuation_rate) if b else 0,
				"is_low": is_low(qty, u.per_bulk, bool(u.bulk_uom), settings),
			}
		)
	# Low items first, then alphabetical.
	rows.sort(key=lambda r: (not r["is_low"], (r["item_name"] or "").lower()))
	return rows


def get_buying_prices(settings) -> dict[str, float]:
	"""``{item_code: cost per bulk unit}`` from the buying price list."""
	return get_buying_prices_per_bulk(list_item_codes(settings), settings)


# ---------------------------------------------------------------------------
# Deliveries
# ---------------------------------------------------------------------------


@writes_as_system
def receive_stock(settings, items: list[dict], supplier: str | None = None) -> dict:
	lines = normalise_bulk_lines(items, what="delivery")
	supplier = supplier or settings.default_supplier
	if not frappe.db.exists("Supplier", supplier):
		raise TijaoError(f"Supplier '{supplier}' was not found.")

	codes = [line["item_code"] for line in lines]
	units = _require_items(codes)
	list_rates = get_buying_prices_per_bulk(codes, settings)

	pr = frappe.new_doc("Purchase Receipt")
	pr.update(
		{
			"company": settings.company,
			"supplier": supplier,
			"posting_date": nowdate(),
			"set_warehouse": settings.warehouse,
			"buying_price_list": settings.buying_price_list,
		}
	)
	for line in lines:
		u = units[line["item_code"]]
		bulk_rate = line.get("bulk_rate")
		if bulk_rate is None:
			bulk_rate = list_rates.get(u.item_code)
		if not bulk_rate:
			unit = u.bulk_uom or u.sale_uom
			raise TijaoError(f"Enter the cost per {unit.lower()} for {u.item_name}.")
		bulk_rate = flt(bulk_rate)

		# Whole crates as one row in the bulk unit (exact amount), loose units as another.
		if line["bulk_qty"]:
			uom, factor = (u.bulk_uom, u.per_bulk) if u.bulk_uom else (u.sale_uom, 1)
			_append_pr_row(pr, u.item_code, line["bulk_qty"], uom, factor, bulk_rate, settings.warehouse)
		if line["loose_qty"]:
			_append_pr_row(
				pr, u.item_code, line["loose_qty"], u.sale_uom, 1, bulk_rate / u.per_bulk, settings.warehouse
			)

	pr.flags.ignore_permissions = True
	pr.insert()
	pr.submit()
	stamp_owner(pr)
	return {
		"receipt": pr.name,
		"supplier": pr.supplier,
		"total": flt(pr.grand_total),
		"stock": get_stock_levels(settings, codes),
	}


def _append_pr_row(pr, item_code, qty, uom, factor, rate, warehouse):
	pr.append(
		"items",
		{
			"item_code": item_code,
			"qty": qty,
			"received_qty": qty,
			"uom": uom,
			"conversion_factor": factor,
			"rate": rate,
			"warehouse": warehouse,
		},
	)


# ---------------------------------------------------------------------------
# Counts / breakages: see count_service.py
# ---------------------------------------------------------------------------


def _valuation_rate(u, b, list_rates: dict) -> float:
	if b and flt(b.valuation_rate):
		return flt(b.valuation_rate)
	if list_rates.get(u.item_code):
		return flt(list_rates[u.item_code]) / u.per_bulk
	return flt(u.valuation_rate)


# ---------------------------------------------------------------------------
# Opening stock (used by the master-data import)
# ---------------------------------------------------------------------------


def items_with_stock_history(item_codes: list[str]) -> set[str]:
	if not item_codes:
		return set()
	return set(
		frappe.get_all(
			"Stock Ledger Entry",
			filters={"item_code": ["in", item_codes], "is_cancelled": 0},
			pluck="item_code",
			distinct=True,
		)
	)


@writes_as_system
def post_opening_stock(settings, rows: list[dict]) -> str | None:
	"""rows: ``[{item_code, qty, valuation_rate}]`` in sale units. Skips items with history."""
	history = items_with_stock_history([r["item_code"] for r in rows])
	sr_items = [
		{
			"item_code": r["item_code"],
			"warehouse": settings.warehouse,
			"qty": r["qty"],
			"valuation_rate": r["valuation_rate"],
		}
		for r in rows
		if r["qty"] > 0 and r["item_code"] not in history
	]
	if not sr_items:
		return None
	sr = _make_reconciliation(
		settings, "Opening Stock", sr_items, expense_account=settings.opening_stock_account
	)
	return sr.name


def _make_reconciliation(settings, purpose: str, items: list[dict], expense_account: str | None):
	sr = frappe.new_doc("Stock Reconciliation")
	sr.update({"company": settings.company, "purpose": purpose, "posting_date": nowdate()})
	if expense_account:
		sr.expense_account = expense_account
	for row in items:
		sr.append("items", row)
	sr.flags.ignore_permissions = True
	sr.insert()
	sr.submit()
	stamp_owner(sr)
	return sr


def _require_items(codes: list[str]) -> dict:
	units = get_item_units(codes)
	for code in codes:
		u = units.get(code)
		if not u or u.disabled or not u.is_stock_item:
			raise TijaoError(f"'{code}' is not a product in this shop.")
	return units

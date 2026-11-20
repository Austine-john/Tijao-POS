"""Master-data import from the Excel template.

* Every row is validated and *all* errors are reported before anything is written.
* ``dry_run`` writes nothing and returns the plan.
* A real import is idempotent: it creates or updates UOMs, items and prices,
  and posts opening stock only for items with no stock history.
"""

from __future__ import annotations

import frappe
from frappe.utils import flt

from tijao_pos.services import stock_service
from tijao_pos.utils.elevate import writes_as_system
from tijao_pos.utils.errors import TijaoError
from tijao_pos.utils.template import (
	ITEM_SHEET,
	ItemRow,
	ParsedTemplate,
	RowError,
	parse_template,
	read_workbook,
)


def load_file_url(file_url: str) -> bytes:
	if not file_url:
		raise TijaoError("Upload the Excel file first.")
	name = frappe.db.get_value("File", {"file_url": file_url}, "name")
	if not name:
		raise TijaoError("That file was not found. Please upload it again.")
	return frappe.get_doc("File", name).get_content()


def run_import(settings, content, dry_run: bool = True, column_map: dict | None = None) -> dict:
	sheets = read_workbook(content)
	parsed = parse_template(sheets, known_uoms=frappe.get_all("UOM", pluck="name"), column_map=column_map)
	if parsed.ok:
		_check_against_site(parsed)

	plan = _plan(parsed) if parsed.ok else []
	result = {
		"ok": parsed.ok,
		"dry_run": bool(dry_run),
		"errors": [e.as_dict() for e in parsed.errors],
		"warnings": [w.as_dict() for w in parsed.warnings],
		"items": plan,
		"summary": _summarise(plan),
	}
	if not parsed.ok or dry_run:
		return result

	_write(settings, parsed)
	result["summary"]["opening_stock_entry"] = _post_opening(settings, parsed, plan)
	return result


def _check_against_site(parsed: ParsedTemplate) -> None:
	"""Rules that need the database. Errors here also block the whole import."""
	existing = {
		i.name: i
		for i in frappe.get_all(
			"Item",
			filters={"name": ["in", [r.item_code for r in parsed.items]]},
			fields=["name", "stock_uom"],
		)
	}
	history = stock_service.items_with_stock_history(list(existing))
	for row in parsed.items:
		ex = existing.get(row.item_code)
		if ex and ex.stock_uom != row.sale_uom and row.item_code in history:
			parsed.errors.append(
				RowError(
					ITEM_SHEET,
					row.row,
					"sale_uom",
					f"{row.item_code} is already sold in {ex.stock_uom}. The sale unit can't change now.",
				)
			)


def _plan(parsed: ParsedTemplate) -> list[dict]:
	codes = [r.item_code for r in parsed.items]
	existing = set(frappe.get_all("Item", filters={"name": ["in", codes]}, pluck="name"))
	history = stock_service.items_with_stock_history(codes)
	plan = []
	for r in parsed.items:
		if r.opening_qty <= 0:
			opening = "none"
		elif r.item_code in history:
			opening = "skip_has_stock"
		else:
			opening = "post"
		plan.append(
			{**r.as_dict(), "action": "update" if r.item_code in existing else "create", "opening": opening}
		)
	return plan


def _summarise(plan: list[dict]) -> dict:
	return {
		"items_total": len(plan),
		"create": sum(1 for p in plan if p["action"] == "create"),
		"update": sum(1 for p in plan if p["action"] == "update"),
		"opening_to_post": sum(1 for p in plan if p["opening"] == "post"),
		"opening_skipped": sum(1 for p in plan if p["opening"] == "skip_has_stock"),
	}


# ---------------------------------------------------------------------------
# Writing
# ---------------------------------------------------------------------------


@writes_as_system
def _write(settings, parsed: ParsedTemplate) -> None:
	for u in parsed.uoms:
		_upsert_uom(u.uom_name, u.whole_number_only)
	for r in parsed.items:
		for uom in filter(None, (r.sale_uom, r.bulk_uom)):
			if not frappe.db.exists("UOM", uom):
				_upsert_uom(uom, True)
		if r.brand and not frappe.db.exists("Brand", r.brand):
			frappe.get_doc({"doctype": "Brand", "brand": r.brand}).insert(ignore_permissions=True)
		_upsert_item(settings, r)
		_set_price(r.item_code, settings.selling_price_list, r.sale_uom, r.selling_price)
		if r.buying_price_per_bulk:
			_set_price(
				r.item_code, settings.buying_price_list, r.bulk_uom or r.sale_uom, r.buying_price_per_bulk
			)


def _upsert_uom(name: str, whole: bool) -> None:
	if frappe.db.exists("UOM", name):
		if frappe.db.get_value("UOM", name, "must_be_whole_number") != int(whole):
			frappe.db.set_value("UOM", name, "must_be_whole_number", int(whole))
		return
	frappe.get_doc({"doctype": "UOM", "uom_name": name, "must_be_whole_number": int(whole)}).insert(
		ignore_permissions=True
	)


def _upsert_item(settings, r: ItemRow) -> None:
	if frappe.db.exists("Item", r.item_code):
		item = frappe.get_doc("Item", r.item_code)
		# Only allowed when the item has no stock history (checked in _check_against_site).
		if item.stock_uom != r.sale_uom:
			item.stock_uom = r.sale_uom
	else:
		item = frappe.new_doc("Item")
		item.update(
			{
				"item_code": r.item_code,
				"item_group": settings.item_group,
				"stock_uom": r.sale_uom,
				"is_stock_item": 1,
				"include_item_in_manufacturing": 0,
				"item_defaults": [{"company": settings.company, "default_warehouse": settings.warehouse}],
			}
		)
	item.item_name = r.item_name
	item.description = r.item_name
	item.brand = r.brand
	item.disabled = 0
	item.sales_uom = r.sale_uom
	item.purchase_uom = r.bulk_uom or None
	if r.buying_price_per_bulk:
		item.valuation_rate = r.buying_price_per_unit

	# UOM table: sale unit = 1, bulk unit = units_per_bulk. Keep any other rows she added.
	rows = {d.uom: d for d in item.get("uoms") or []}
	if r.sale_uom not in rows:
		item.append("uoms", {"uom": r.sale_uom, "conversion_factor": 1})
	if r.bulk_uom:
		if r.bulk_uom in rows:
			rows[r.bulk_uom].conversion_factor = r.units_per_bulk
		else:
			item.append("uoms", {"uom": r.bulk_uom, "conversion_factor": r.units_per_bulk})

	item.flags.ignore_permissions = True
	item.save()


def _set_price(item_code: str, price_list: str, uom: str, rate: float) -> None:
	name = frappe.db.get_value("Item Price", {"item_code": item_code, "price_list": price_list, "uom": uom})
	if name:
		if flt(frappe.db.get_value("Item Price", name, "price_list_rate")) != flt(rate):
			doc = frappe.get_doc("Item Price", name)
			doc.price_list_rate = rate
			doc.save(ignore_permissions=True)
		return
	frappe.get_doc(
		{
			"doctype": "Item Price",
			"item_code": item_code,
			"price_list": price_list,
			"uom": uom,
			"price_list_rate": rate,
		}
	).insert(ignore_permissions=True)


def _post_opening(settings, parsed: ParsedTemplate, plan: list[dict]) -> str | None:
	to_post = {p["item_code"] for p in plan if p["opening"] == "post"}
	rows = [
		{"item_code": r.item_code, "qty": r.opening_qty, "valuation_rate": r.buying_price_per_unit}
		for r in parsed.items
		if r.item_code in to_post
	]
	return stock_service.post_opening_stock(settings, rows)

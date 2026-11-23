"""Products as the shop screen sees them: name, price per sale unit, stock left."""

from __future__ import annotations

import frappe
from frappe.utils import flt

from tijao_pos.utils.lines import describe_qty, split_units


def is_low(qty: float, per_bulk: float, has_bulk: bool, settings) -> bool:
	"""Low = fewer than N crates (items with a bulk unit) or N loose units (items without)."""
	if has_bulk:
		return flt(qty) < flt(settings.low_stock_bulk_qty) * flt(per_bulk)
	return flt(qty) < flt(settings.low_stock_loose_qty)


def item_group_names(item_group: str) -> list[str]:
	"""The configured item group and all its children."""
	lft, rgt = frappe.get_cached_value("Item Group", item_group, ["lft", "rgt"])
	return frappe.get_all("Item Group", filters={"lft": [">=", lft], "rgt": ["<=", rgt]}, pluck="name")


def get_item_units(item_codes: list[str]) -> dict[str, frappe._dict]:
	"""``{item_code: {item_name, sale_uom, bulk_uom, per_bulk, ...}}``.

	sale_uom = Item.stock_uom; bulk_uom = Item.purchase_uom with its factor from the
	Item's UOM Conversion Detail table. per_bulk is 1 when there is no bulk unit.
	"""
	if not item_codes:
		return {}
	items = frappe.get_all(
		"Item",
		filters={"name": ["in", item_codes]},
		fields=[
			"name",
			"item_name",
			"stock_uom",
			"purchase_uom",
			"is_stock_item",
			"disabled",
			"valuation_rate",
		],
	)
	factors = frappe.get_all(
		"UOM Conversion Detail",
		filters={"parenttype": "Item", "parent": ["in", item_codes]},
		fields=["parent", "uom", "conversion_factor"],
	)
	factor_map = {(f.parent, f.uom): flt(f.conversion_factor) for f in factors}
	out = {}
	for it in items:
		bulk = it.purchase_uom if it.purchase_uom and it.purchase_uom != it.stock_uom else None
		per = factor_map.get((it.name, bulk)) if bulk else None
		if not per or per <= 1:
			bulk, per = None, 1
		out[it.name] = frappe._dict(
			item_code=it.name,
			item_name=it.item_name,
			sale_uom=it.stock_uom,
			bulk_uom=bulk,
			per_bulk=per,
			is_stock_item=it.is_stock_item,
			disabled=it.disabled,
			valuation_rate=flt(it.valuation_rate),
		)
	return out


def get_selling_prices(item_codes: list[str], settings) -> dict[str, float]:
	"""Price per sale unit from the selling price list (UOM-specific row wins)."""
	return _prices(item_codes, settings.selling_price_list, "sale")


def get_buying_prices_per_bulk(item_codes: list[str], settings) -> dict[str, float]:
	"""Cost per bulk unit from the buying price list."""
	return _prices(item_codes, settings.buying_price_list, "bulk")


def _prices(item_codes: list[str], price_list: str, unit: str) -> dict[str, float]:
	if not item_codes:
		return {}
	units = get_item_units(item_codes)
	rows = frappe.get_all(
		"Item Price",
		filters={"price_list": price_list, "item_code": ["in", item_codes]},
		fields=["item_code", "uom", "price_list_rate"],
		order_by="modified desc",
	)
	out: dict[str, float] = {}
	fallback: dict[str, float] = {}
	for r in rows:
		u = units.get(r.item_code)
		if not u:
			continue
		wanted = u.sale_uom if unit == "sale" else (u.bulk_uom or u.sale_uom)
		if r.uom == wanted:
			out.setdefault(r.item_code, flt(r.price_list_rate))
		elif not r.uom or r.uom == u.sale_uom:
			# Price per sale unit; convert to bulk if needed (same rule ERPNext uses).
			factor = u.per_bulk if unit == "bulk" else 1
			fallback.setdefault(r.item_code, flt(r.price_list_rate) * factor)
	for code, rate in fallback.items():
		out.setdefault(code, rate)
	return out


def get_stock_qty(item_codes: list[str], warehouse: str) -> dict[str, frappe._dict]:
	if not item_codes:
		return {}
	bins = frappe.get_all(
		"Bin",
		filters={"warehouse": warehouse, "item_code": ["in", item_codes]},
		fields=["item_code", "actual_qty", "stock_value", "valuation_rate"],
	)
	return {b.item_code: b for b in bins}


def list_item_codes(settings) -> list[str]:
	return frappe.get_all(
		"Item",
		filters={
			"disabled": 0,
			"is_stock_item": 1,
			"has_variants": 0,
			"item_group": ["in", item_group_names(settings.item_group)],
		},
		pluck="name",
		order_by="item_name asc",
	)


def get_products(settings) -> list[dict]:
	codes = list_item_codes(settings)
	if not codes:
		return []
	units = get_item_units(codes)
	prices = get_selling_prices(codes, settings)
	stock = get_stock_qty(codes, settings.warehouse)
	images = dict(
		frappe.get_all("Item", filters={"name": ["in", codes]}, fields=["name", "image"], as_list=True)
	)
	brands = dict(
		frappe.get_all("Item", filters={"name": ["in", codes]}, fields=["name", "brand"], as_list=True)
	)

	products = []
	for code in codes:
		u = units[code]
		qty = flt(stock[code].actual_qty) if code in stock else 0
		split = split_units(qty, u.per_bulk)
		products.append(
			{
				"item_code": code,
				"item_name": u.item_name,
				"brand": brands.get(code),
				"price": prices.get(code),
				"in_stock": qty,
				"uom": u.sale_uom,
				"bulk_uom": u.bulk_uom,
				"per_bulk": u.per_bulk,
				"bulk_qty": split.bulk,
				"loose_qty": split.loose,
				"stock_text": describe_qty(qty, u.sale_uom, u.bulk_uom, u.per_bulk),
				"is_low": is_low(qty, u.per_bulk, bool(u.bulk_uom), settings),
				"image": images.get(code),
			}
		)
	return products

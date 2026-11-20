"""Master-data Excel template: read, map columns, validate.

Pure (no frappe import) so it can be unit tested with plain pytest.

Contract (do not change without asking the owner):
  Sheet ``UOMs``  : uom_name, whole_number_only
  Sheet ``Items`` : item_code, item_name, brand, sale_uom, bulk_uom, units_per_bulk,
                    selling_price, buying_price_per_bulk, opening_bulk_qty, opening_loose_qty

A workbook with different headers is supported through a *column-mapping layer*:
built-in aliases (``DEFAULT_ALIASES``) plus an optional ``column_map`` argument
``{canonical_name: "Their Header"}``. The canonical contract never changes.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import asdict, dataclass, field
from typing import Any

from tijao_pos.utils.lines import LineError, to_number, to_whole

UOM_SHEET = "UOMs"
ITEM_SHEET = "Items"

UOM_COLUMNS = ["uom_name", "whole_number_only"]
ITEM_COLUMNS = [
	"item_code",
	"item_name",
	"brand",
	"sale_uom",
	"bulk_uom",
	"units_per_bulk",
	"selling_price",
	"buying_price_per_bulk",
	"opening_bulk_qty",
	"opening_loose_qty",
]
REQUIRED_ITEM_COLUMNS = ["item_name", "sale_uom", "selling_price"]

# Common alternative headers -> canonical name. Compared after normalise_header().
DEFAULT_ALIASES: dict[str, list[str]] = {
	"uom_name": ["uom", "unit", "unit_name"],
	"whole_number_only": ["whole_number", "whole_numbers_only", "must_be_whole_number"],
	"item_code": ["code", "sku", "product_code"],
	"item_name": ["name", "product", "product_name", "item"],
	"sale_uom": ["sale_unit", "selling_unit", "unit", "uom"],
	"bulk_uom": ["bulk_unit", "buying_unit", "purchase_unit", "pack_unit"],
	"units_per_bulk": ["per_bulk", "pack_size", "units_per_crate", "qty_per_bulk"],
	"selling_price": ["price", "sale_price", "retail_price", "selling_price_per_unit"],
	"buying_price_per_bulk": ["buying_price", "cost_per_bulk", "cost_price", "cost_per_crate"],
	"opening_bulk_qty": ["opening_crates", "opening_bulk", "crates_in_stock"],
	"opening_loose_qty": ["opening_loose", "opening_units", "loose_in_stock"],
}

YES = {"yes", "y", "true", "1", "x"}
NO = {"no", "n", "false", "0"}


@dataclass
class RowError:
	sheet: str
	row: int  # Excel row number (header is row 1); 0 = whole sheet
	column: str
	message: str

	def as_dict(self) -> dict:
		return asdict(self)


@dataclass
class UomRow:
	row: int
	uom_name: str
	whole_number_only: bool = True


@dataclass
class ItemRow:
	row: int
	item_code: str
	item_name: str
	brand: str | None
	sale_uom: str
	bulk_uom: str | None
	units_per_bulk: int
	selling_price: float
	buying_price_per_bulk: float
	opening_bulk_qty: int
	opening_loose_qty: int

	@property
	def opening_qty(self) -> int:
		"""Opening stock in sale units (what ERPNext stores)."""
		return self.opening_bulk_qty * (self.units_per_bulk if self.bulk_uom else 1) + self.opening_loose_qty

	@property
	def buying_price_per_unit(self) -> float:
		return self.buying_price_per_bulk / (self.units_per_bulk if self.bulk_uom else 1)

	def as_dict(self) -> dict:
		d = asdict(self)
		d["opening_qty"] = self.opening_qty
		return d


@dataclass
class ParsedTemplate:
	uoms: list[UomRow] = field(default_factory=list)
	items: list[ItemRow] = field(default_factory=list)
	errors: list[RowError] = field(default_factory=list)
	warnings: list[RowError] = field(default_factory=list)

	@property
	def ok(self) -> bool:
		return not self.errors


# ---------------------------------------------------------------------------
# Reading
# ---------------------------------------------------------------------------


def normalise_header(value: Any) -> str:
	"""'Units per Bulk ' -> 'units_per_bulk'."""
	return re.sub(r"[^a-z0-9]+", "_", str(value or "").strip().lower()).strip("_")


def read_workbook(source: Any) -> dict[str, list[list[Any]]]:
	"""Load an .xlsx (path, bytes or file-like) into ``{sheet_title: rows}``."""
	import io

	from openpyxl import load_workbook

	if isinstance(source, bytes | bytearray):
		source = io.BytesIO(source)
	try:
		wb = load_workbook(source, read_only=True, data_only=True)
	except Exception as e:  # openpyxl raises many types for a bad file
		raise LineError("This file could not be opened. Please upload the Excel (.xlsx) template.") from e
	try:
		return {ws.title: [list(r) for r in ws.iter_rows(values_only=True)] for ws in wb.worksheets}
	finally:
		wb.close()


def _find_sheet(sheets: dict[str, list], name: str) -> list[list[Any]] | None:
	for title, rows in sheets.items():
		if normalise_header(title) == normalise_header(name):
			return rows
	return None


def map_columns(
	header: Iterable[Any],
	columns: list[str],
	column_map: dict[str, str] | None = None,
) -> dict[str, int]:
	"""Return ``{canonical: column_index}`` for the columns present in ``header``.

	Priority: explicit ``column_map`` > exact canonical name > built-in alias.
	"""
	norm = [normalise_header(h) for h in header]
	found: dict[str, int] = {}
	explicit = {k: normalise_header(v) for k, v in (column_map or {}).items()}
	for canonical in columns:
		candidates = []
		if canonical in explicit:
			candidates.append(explicit[canonical])
		candidates.append(canonical)
		candidates.extend(DEFAULT_ALIASES.get(canonical, []))
		for cand in candidates:
			if cand in norm and norm.index(cand) not in found.values():
				found[canonical] = norm.index(cand)
				break
	return found


def _rows_as_dicts(rows: list[list[Any]], mapping: dict[str, int]) -> list[tuple[int, dict]]:
	out = []
	for offset, raw in enumerate(rows[1:], start=2):
		values = {k: (raw[i] if i < len(raw) else None) for k, i in mapping.items()}
		if all(_blank(v) for v in values.values()):
			continue
		out.append((offset, values))
	return out


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


def parse_template(
	sheets: dict[str, list[list[Any]]],
	known_uoms: Iterable[str] = (),
	column_map: dict[str, str] | None = None,
) -> ParsedTemplate:
	"""Validate every row and collect *all* errors (nothing is written here).

	``known_uoms``: UOM names that already exist in ERPNext.
	"""
	result = ParsedTemplate()
	uom_lookup: dict[str, str] = {u.lower(): u for u in known_uoms}

	# --- UOMs (optional sheet) ------------------------------------------------
	uom_rows = _find_sheet(sheets, UOM_SHEET)
	if uom_rows:
		mapping = map_columns(uom_rows[0], UOM_COLUMNS, column_map)
		if "uom_name" not in mapping:
			result.errors.append(RowError(UOM_SHEET, 1, "uom_name", "Column 'uom_name' is missing."))
		else:
			seen: set[str] = set()
			for rownum, values in _rows_as_dicts(uom_rows, mapping):
				name = _text(values.get("uom_name"))
				if not name:
					result.errors.append(RowError(UOM_SHEET, rownum, "uom_name", "Unit name is empty."))
					continue
				if name.lower() in seen:
					result.errors.append(
						RowError(UOM_SHEET, rownum, "uom_name", f"'{name}' is listed twice.")
					)
					continue
				seen.add(name.lower())
				whole = _yes_no(values.get("whole_number_only"), default=True)
				if whole is None:
					result.errors.append(RowError(UOM_SHEET, rownum, "whole_number_only", "Use yes or no."))
					continue
				canonical = uom_lookup.get(name.lower(), name)
				uom_lookup[name.lower()] = canonical
				result.uoms.append(UomRow(rownum, canonical, whole))

	# --- Items ------------------------------------------------------------------
	item_rows = _find_sheet(sheets, ITEM_SHEET)
	if not item_rows:
		result.errors.append(RowError(ITEM_SHEET, 0, "", "The 'Items' sheet is missing."))
		return result

	mapping = map_columns(item_rows[0], ITEM_COLUMNS, column_map)
	missing = [c for c in REQUIRED_ITEM_COLUMNS if c not in mapping]
	if missing:
		for col in missing:
			result.errors.append(RowError(ITEM_SHEET, 1, col, f"Column '{col}' is missing."))
		return result

	codes: dict[str, int] = {}
	for rownum, values in _rows_as_dicts(item_rows, mapping):
		item, errors, warnings = _parse_item(rownum, values, uom_lookup)
		result.errors.extend(errors)
		result.warnings.extend(warnings)
		if item is None:
			continue
		key = item.item_code.lower()
		if key in codes:
			result.errors.append(
				RowError(
					ITEM_SHEET,
					rownum,
					"item_code",
					f"Item code '{item.item_code}' is the same as row {codes[key]}.",
				)
			)
			continue
		codes[key] = rownum
		result.items.append(item)

	if not result.items and not result.errors:
		result.errors.append(RowError(ITEM_SHEET, 0, "", "The 'Items' sheet has no products."))
	return result


def _parse_item(
	rownum: int, values: dict, uom_lookup: dict[str, str]
) -> tuple[ItemRow | None, list[RowError], list[RowError]]:
	errors: list[RowError] = []
	warnings: list[RowError] = []

	def err(col: str, msg: str):
		errors.append(RowError(ITEM_SHEET, rownum, col, msg))

	def num(col: str, whole: bool = False, required: bool = False):
		try:
			if whole:
				return to_whole(values.get(col), _label(col), allow_blank=not required)
			n = to_number(values.get(col), _label(col), allow_blank=not required)
			if n < 0:
				raise LineError(f"{_label(col)} cannot be negative.")
			return n
		except LineError as e:
			err(col, str(e))
			return None

	name = _text(values.get("item_name"))
	if not name:
		err("item_name", "Product name is empty.")
	elif name.upper().startswith("SAMPLE"):
		err("item_name", "This is a sample row. Replace it with a real product or delete it.")

	code = _text(values.get("item_code")) or (make_item_code(name) if name else "")
	if name and not code:
		err("item_code", "Could not make an item code from the name. Please fill item_code.")

	sale_uom = _resolve_uom(values.get("sale_uom"), uom_lookup)
	if not _text(values.get("sale_uom")):
		err("sale_uom", "Sale unit is empty (e.g. Bottle).")
	elif sale_uom is None:
		err("sale_uom", f"Unit '{_text(values.get('sale_uom'))}' is not in the UOMs sheet.")

	bulk_text = _text(values.get("bulk_uom"))
	bulk_uom = _resolve_uom(bulk_text, uom_lookup) if bulk_text else None
	if bulk_text and bulk_uom is None:
		err("bulk_uom", f"Unit '{bulk_text}' is not in the UOMs sheet.")
	if bulk_uom and sale_uom and bulk_uom.lower() == sale_uom.lower():
		err("bulk_uom", "Bulk unit must be different from the sale unit. Leave it blank instead.")
		bulk_uom = None

	per_bulk = num("units_per_bulk", whole=True)
	if bulk_text:
		if per_bulk is not None and per_bulk < 2:
			err("units_per_bulk", "How many sale units are in one bulk unit? It must be 2 or more.")
	else:
		if per_bulk not in (None, 0, 1):
			err("units_per_bulk", "Units per bulk is filled but there is no bulk unit.")
		per_bulk = 1

	price = num("selling_price", required=True)
	if price is not None and price <= 0:
		err("selling_price", "Selling price must be more than 0.")
	cost = num("buying_price_per_bulk")
	open_bulk = num("opening_bulk_qty", whole=True)
	open_loose = num("opening_loose_qty", whole=True)

	if errors:
		return None, errors, warnings

	item = ItemRow(
		row=rownum,
		item_code=code,
		item_name=name,
		brand=_text(values.get("brand")) or None,
		sale_uom=sale_uom,
		bulk_uom=bulk_uom,
		units_per_bulk=int(per_bulk or 1),
		selling_price=float(price),
		buying_price_per_bulk=float(cost or 0),
		opening_bulk_qty=int(open_bulk or 0),
		opening_loose_qty=int(open_loose or 0),
	)
	if item.opening_qty > 0 and item.buying_price_per_bulk <= 0:
		errors.append(
			RowError(
				ITEM_SHEET,
				rownum,
				"buying_price_per_bulk",
				"Opening stock needs a buying price so profit can be worked out.",
			)
		)
		return None, errors, warnings
	if item.buying_price_per_bulk and item.buying_price_per_unit > item.selling_price:
		warnings.append(
			RowError(ITEM_SHEET, rownum, "selling_price", "Selling price is lower than the buying price.")
		)
	return item, errors, warnings


def make_item_code(name: str) -> str:
	"""'Coca-Cola 500ml' -> 'COCA-COLA-500ML'."""
	return re.sub(r"[^A-Z0-9]+", "-", (name or "").upper()).strip("-")[:140]


def _resolve_uom(value: Any, lookup: dict[str, str]) -> str | None:
	text = _text(value)
	return lookup.get(text.lower()) if text else None


def _text(value: Any) -> str:
	if value is None:
		return ""
	if isinstance(value, float) and value.is_integer():
		value = int(value)
	return re.sub(r"\s+", " ", str(value)).strip()


def _blank(value: Any) -> bool:
	return value is None or (isinstance(value, str) and not value.strip())


def _yes_no(value: Any, default: bool) -> bool | None:
	if _blank(value):
		return default
	if isinstance(value, bool):
		return value
	text = _text(value).lower()
	if text in YES:
		return True
	if text in NO:
		return False
	return None


def _label(col: str) -> str:
	return {
		"units_per_bulk": "Units per bulk",
		"selling_price": "Selling price",
		"buying_price_per_bulk": "Buying price",
		"opening_bulk_qty": "Opening bulk qty",
		"opening_loose_qty": "Opening loose qty",
	}.get(col, col)

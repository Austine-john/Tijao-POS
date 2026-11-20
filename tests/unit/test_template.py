import io

import pytest
from openpyxl import Workbook

from tijao_pos.utils.lines import LineError
from tijao_pos.utils.template import (
	ITEM_COLUMNS,
	make_item_code,
	map_columns,
	normalise_header,
	parse_template,
	read_workbook,
)

UOMS = [["uom_name", "whole_number_only"], ["Bottle", "yes"], ["Crate", "yes"], ["Carton", "yes"]]


def item(**kw):
	row = dict(
		item_code=None,
		item_name="Coca-Cola 500ml",
		brand="Coca-Cola",
		sale_uom="Bottle",
		bulk_uom="Crate",
		units_per_bulk=24,
		selling_price=60,
		buying_price_per_bulk=1080,
		opening_bulk_qty=3,
		opening_loose_qty=7,
	)
	row.update(kw)
	return [row[c] for c in ITEM_COLUMNS]


def sheets(*rows, uoms=UOMS, header=ITEM_COLUMNS):
	return {"UOMs": uoms, "Items": [list(header), *rows]}


def test_valid_row():
	p = parse_template(sheets(item()))
	assert p.ok, p.errors
	r = p.items[0]
	assert r.item_code == "COCA-COLA-500ML"
	assert r.opening_qty == 79
	assert r.buying_price_per_unit == 45
	assert [u.uom_name for u in p.uoms] == ["Bottle", "Crate", "Carton"]


def test_item_without_bulk():
	p = parse_template(
		sheets(
			item(
				bulk_uom=None,
				units_per_bulk=None,
				buying_price_per_bulk=40,
				opening_bulk_qty=0,
				opening_loose_qty=5,
			)
		)
	)
	assert p.ok, p.errors
	assert p.items[0].units_per_bulk == 1
	assert p.items[0].opening_qty == 5
	assert p.items[0].buying_price_per_unit == 40


def test_uom_case_insensitive_and_known_uoms():
	p = parse_template(sheets(item(sale_uom="bottle", bulk_uom="Box"), uoms=UOMS[:2]), known_uoms=["Box"])
	assert p.ok, p.errors
	assert (p.items[0].sale_uom, p.items[0].bulk_uom) == ("Bottle", "Box")


def test_collects_all_errors():
	p = parse_template(
		sheets(
			item(item_name="SAMPLE Coca-Cola 500ml", selling_price=0),
			item(item_name="Fanta", sale_uom="Glass"),
			item(item_name="Sprite", units_per_bulk=1),
			item(item_name="Water", opening_bulk_qty=-1),
			item(item_name="Juice", buying_price_per_bulk=0),
			item(item_name="Soda", bulk_uom=None, units_per_bulk=12),
		)
	)
	assert not p.ok
	msgs = {(e.row, e.column) for e in p.errors}
	assert (2, "item_name") in msgs and (2, "selling_price") in msgs
	assert (3, "sale_uom") in msgs
	assert (4, "units_per_bulk") in msgs
	assert (5, "opening_bulk_qty") in msgs
	assert (6, "buying_price_per_bulk") in msgs
	assert (7, "units_per_bulk") in msgs
	assert p.items == [] or all(i.row not in (2, 3, 4, 5, 6, 7) for i in p.items)


def test_duplicate_codes():
	p = parse_template(sheets(item(), item(item_name="coca cola 500ML")))
	assert any("same as row 2" in e.message for e in p.errors)


def test_blank_rows_skipped_and_empty_sheet_error():
	p = parse_template(sheets([None] * 10))
	assert any("no products" in e.message for e in p.errors)


def test_missing_items_sheet_and_columns():
	assert not parse_template({"UOMs": UOMS}).ok
	p = parse_template({"Items": [["item_name", "brand"], ["Coke", None]]})
	assert {e.column for e in p.errors} == {"sale_uom", "selling_price"}


def test_bulk_same_as_sale_unit():
	p = parse_template(sheets(item(bulk_uom="Bottle")))
	assert any(e.column == "bulk_uom" for e in p.errors)


def test_warning_when_selling_below_cost():
	p = parse_template(sheets(item(selling_price=40)))
	assert p.ok
	assert p.warnings and p.warnings[0].column == "selling_price"


def test_bad_whole_number_flag():
	p = parse_template(
		sheets(item(), uoms=[["uom_name", "whole_number_only"], ["Bottle", "maybe"], ["Crate", ""]])
	)
	assert any(e.sheet == "UOMs" and e.column == "whole_number_only" for e in p.errors)


class TestColumnMapping:
	def test_normalise_header(self):
		assert normalise_header(" Units per Bulk ") == "units_per_bulk"

	def test_aliases(self):
		header = ["Product Name", "Unit", "Price", "Buying unit", "Pack size"]
		m = map_columns(header, ITEM_COLUMNS)
		assert m == {"item_name": 0, "sale_uom": 1, "selling_price": 2, "bulk_uom": 3, "units_per_bulk": 4}

	def test_explicit_map_wins(self):
		header = ["Bidhaa", "Kipimo", "Bei"]
		p = parse_template(
			{"UOMs": UOMS, "Items": [header, ["Coke", "Bottle", 60]]},
			column_map={"item_name": "Bidhaa", "sale_uom": "Kipimo", "selling_price": "Bei"},
		)
		assert p.ok, p.errors
		assert p.items[0].item_name == "Coke"


def test_make_item_code():
	assert make_item_code("Coca-Cola 500ml") == "COCA-COLA-500ML"
	assert make_item_code("  Fanta   Orange (300ml) ") == "FANTA-ORANGE-300ML"


def test_read_workbook_roundtrip():
	wb = Workbook()
	ws = wb.active
	ws.title = "Items"
	ws.append(ITEM_COLUMNS)
	ws.append(item())
	u = wb.create_sheet("UOMs")
	for r in UOMS:
		u.append(r)
	buf = io.BytesIO()
	wb.save(buf)
	p = parse_template(read_workbook(buf.getvalue()))
	assert p.ok, p.errors
	assert p.items[0].opening_qty == 79


def test_read_workbook_bad_file():
	with pytest.raises(LineError, match="could not be opened"):
		read_workbook(b"not an excel file")

import pytest

from tijao_pos.utils.lines import (
	LineError,
	describe_qty,
	normalise_bulk_lines,
	normalise_sale_lines,
	plural,
	split_units,
	to_sale_units,
	to_whole,
)


class TestUnits:
	def test_split_crates_and_bottles(self):
		s = split_units(79, 24)
		assert (s.bulk, s.loose) == (3, 7)

	def test_split_exact_crates(self):
		assert split_units(48, 24).as_dict() == {"bulk_qty": 2, "loose_qty": 0}

	@pytest.mark.parametrize("per", [None, 0, 1])
	def test_split_without_bulk_is_all_loose(self, per):
		assert split_units(5, per).as_dict() == {"bulk_qty": 0, "loose_qty": 5}

	def test_split_zero_and_negative(self):
		assert split_units(0, 24).as_dict() == {"bulk_qty": 0, "loose_qty": 0}
		assert split_units(-3, 24).as_dict() == {"bulk_qty": 0, "loose_qty": -3}

	def test_to_sale_units_roundtrip(self):
		for qty in (0, 1, 23, 24, 25, 79, 240):
			s = split_units(qty, 24)
			assert to_sale_units(s.bulk, s.loose, 24) == qty

	def test_to_sale_units_without_bulk(self):
		assert to_sale_units(3, 2, None) == 5

	def test_describe(self):
		assert describe_qty(79, "Bottle", "Crate", 24) == "3 crates + 7 bottles"
		assert describe_qty(24, "Bottle", "Crate", 24) == "1 crate"
		assert describe_qty(1, "Bottle", "Crate", 24) == "1 bottle"
		assert describe_qty(0, "Bottle", "Crate", 24) == "0 bottles"
		assert describe_qty(5, "Bottle") == "5 bottles"
		assert describe_qty(30, "Bottle", "Box", 12) == "2 boxes + 6 bottles"

	def test_plural(self):
		assert plural("Carton", 2) == "cartons"
		assert plural("Box", 3) == "boxes"
		assert plural("Nos", 4) == "nos"
		assert plural("Crate", 1) == "crate"


class TestSaleLines:
	def test_merges_duplicates_in_order(self):
		out = normalise_sale_lines(
			[
				{"item_code": "COKE", "qty": 1},
				{"item_code": "FANTA", "qty": 2},
				{"item_code": "COKE", "qty": "3"},
			]
		)
		assert out == [{"item_code": "COKE", "qty": 4}, {"item_code": "FANTA", "qty": 2}]

	@pytest.mark.parametrize(
		"items,msg",
		[
			(None, "empty"),
			([], "empty"),
			("COKE", "list"),
			([{"qty": 1}], "no product"),
			([{"item_code": "COKE", "qty": 0}], "at least 1"),
			([{"item_code": "COKE", "qty": -1}], "negative"),
			([{"item_code": "COKE", "qty": 1.5}], "whole number"),
			([{"item_code": "COKE", "qty": "abc"}], "number"),
			([{"item_code": "COKE"}], "required"),
			([{"item_code": "COKE", "qty": True}], "number"),
		],
	)
	def test_errors(self, items, msg):
		with pytest.raises(LineError, match=msg):
			normalise_sale_lines(items)


class TestBulkLines:
	def test_delivery_line_with_rate(self):
		out = normalise_bulk_lines(
			[{"item_code": "COKE", "bulk_qty": 2, "loose_qty": 5, "bulk_rate": "1,200"}], what="delivery"
		)
		assert out == [{"item_code": "COKE", "bulk_qty": 2, "loose_qty": 5, "bulk_rate": 1200.0}]

	def test_blank_rate_is_omitted(self):
		out = normalise_bulk_lines([{"item_code": "COKE", "bulk_qty": 1, "bulk_rate": ""}], what="delivery")
		assert "bulk_rate" not in out[0]

	def test_zero_delivery_rejected_but_zero_count_allowed(self):
		with pytest.raises(LineError, match="Enter a quantity"):
			normalise_bulk_lines([{"item_code": "COKE"}], what="delivery")
		assert (
			normalise_bulk_lines([{"item_code": "COKE"}], what="count", allow_zero=True)[0]["bulk_qty"] == 0
		)

	def test_duplicates_rejected(self):
		with pytest.raises(LineError, match="twice"):
			normalise_bulk_lines(
				[{"item_code": "A", "bulk_qty": 1}, {"item_code": "A", "bulk_qty": 1}], what="delivery"
			)

	def test_negative_rate(self):
		with pytest.raises(LineError, match="negative"):
			normalise_bulk_lines([{"item_code": "A", "bulk_qty": 1, "bulk_rate": -5}], what="delivery")


def test_to_whole():
	assert to_whole("3", "x") == 3
	assert to_whole(3.0, "x") == 3
	assert to_whole(None, "x") == 0
	with pytest.raises(LineError):
		to_whole("", "x", allow_blank=False)

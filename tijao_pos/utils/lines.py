"""Pure helpers for cart lines and unit maths.

No frappe import here on purpose, so this module runs under plain pytest.
Errors are raised as ``LineError`` with a short, human message; the API layer
turns them into user-facing errors.
"""

from __future__ import annotations

import math
from collections import OrderedDict
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any


class LineError(ValueError):
	"""Bad input from the screen (wrong qty, missing item...)."""


# ---------------------------------------------------------------------------
# Numbers
# ---------------------------------------------------------------------------


def to_number(value: Any, field: str, *, allow_blank: bool = True, default: float = 0.0) -> float:
	"""Parse a number from JSON/Excel input. Blank -> default."""
	if value is None or (isinstance(value, str) and not value.strip()):
		if allow_blank:
			return default
		raise LineError(f"{field} is required.")
	if isinstance(value, bool):
		raise LineError(f"{field} must be a number.")
	try:
		number = float(str(value).replace(",", "").strip()) if isinstance(value, str) else float(value)
	except (TypeError, ValueError):
		raise LineError(f"{field} must be a number, not '{value}'.") from None
	if math.isnan(number) or math.isinf(number):
		raise LineError(f"{field} must be a number.")
	return number


def to_whole(value: Any, field: str, *, allow_blank: bool = True, default: int = 0) -> int:
	"""Parse a whole, non-negative number (bottles and crates are never fractional)."""
	number = to_number(value, field, allow_blank=allow_blank, default=default)
	if number < 0:
		raise LineError(f"{field} cannot be negative.")
	if abs(number - round(number)) > 1e-9:
		raise LineError(f"{field} must be a whole number.")
	return int(round(number))


# ---------------------------------------------------------------------------
# Units
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Split:
	bulk: int
	loose: float

	def as_dict(self) -> dict:
		return {"bulk_qty": self.bulk, "loose_qty": self.loose}


def split_units(qty: float, per_bulk: float | None) -> Split:
	"""79 bottles at 24/crate -> Split(bulk=3, loose=7).

	Items without a bulk unit (per_bulk None/0/1) are all loose.
	"""
	qty = float(qty or 0)
	per = float(per_bulk or 0)
	if per <= 1 or qty <= 0:
		return Split(0, _clean(qty))
	bulk = int(qty // per)
	loose = _clean(qty - bulk * per)
	return Split(bulk, loose)


def to_sale_units(bulk_qty: float, loose_qty: float, per_bulk: float | None) -> float:
	"""3 crates + 7 bottles at 24/crate -> 79."""
	per = float(per_bulk or 0) if (per_bulk or 0) > 1 else 1.0
	return _clean(float(bulk_qty or 0) * per + float(loose_qty or 0))


def plural(uom: str, count: float) -> str:
	"""'Crate' -> 'crate' / 'crates'. Simple English rules; good enough for unit names."""
	word = (uom or "").strip().lower()
	if not word or count == 1:
		return word
	if word.endswith("s"):
		return word
	if word.endswith(("x", "ch", "sh", "z")):
		return word + "es"
	if word.endswith("y") and len(word) > 1 and word[-2] not in "aeiou":
		return word[:-1] + "ies"
	return word + "s"


def describe_qty(
	qty: float, sale_uom: str, bulk_uom: str | None = None, per_bulk: float | None = None
) -> str:
	"""Human text for stock: '3 crates + 7 bottles', '2 crates', '5 bottles', '0 bottles'."""
	if bulk_uom and (per_bulk or 0) > 1:
		split = split_units(qty, per_bulk)
		parts = []
		if split.bulk:
			parts.append(f"{split.bulk} {plural(bulk_uom, split.bulk)}")
		if split.loose or not parts:
			parts.append(f"{_fmt(split.loose)} {plural(sale_uom, split.loose)}")
		return " + ".join(parts)
	return f"{_fmt(qty)} {plural(sale_uom, qty)}"


# ---------------------------------------------------------------------------
# Lines from the screen
# ---------------------------------------------------------------------------


def normalise_sale_lines(items: Iterable[dict] | None) -> list[dict]:
	"""Validate ``[{item_code, qty}]`` and merge duplicates (same item tapped twice).

	Returns ``[{"item_code": str, "qty": int}]`` in first-seen order.
	"""
	if not items:
		raise LineError("The cart is empty.")
	if not isinstance(items, list | tuple):
		raise LineError("Items must be a list.")
	merged: OrderedDict[str, int] = OrderedDict()
	for row in items:
		if not isinstance(row, dict):
			raise LineError("Each item must have an item_code and qty.")
		code = str(row.get("item_code") or "").strip()
		if not code:
			raise LineError("An item in the cart has no product.")
		qty = to_whole(row.get("qty"), f"Quantity for {code}", allow_blank=False)
		if qty <= 0:
			raise LineError(f"Quantity for {code} must be at least 1.")
		merged[code] = merged.get(code, 0) + qty
	return [{"item_code": code, "qty": qty} for code, qty in merged.items()]


def normalise_bulk_lines(rows: Iterable[dict] | None, *, what: str, allow_zero: bool = False) -> list[dict]:
	"""Validate ``[{item_code, bulk_qty, loose_qty?, bulk_rate?}]`` for deliveries/counts.

	``allow_zero`` is True for stock counts (an empty shelf is a valid count).
	Duplicate items are rejected rather than merged: it's ambiguous for a count.
	"""
	if not rows:
		raise LineError(f"Add at least one product to the {what}.")
	if not isinstance(rows, list | tuple):
		raise LineError("Items must be a list.")
	seen: set[str] = set()
	out: list[dict] = []
	for row in rows:
		if not isinstance(row, dict):
			raise LineError("Each line must have an item_code.")
		code = str(row.get("item_code") or "").strip()
		if not code:
			raise LineError(f"A line in the {what} has no product.")
		if code in seen:
			raise LineError(f"{code} is listed twice in the {what}.")
		seen.add(code)
		bulk = to_whole(row.get("bulk_qty"), f"Crates/cartons for {code}")
		loose = to_whole(row.get("loose_qty"), f"Loose units for {code}")
		if not allow_zero and bulk == 0 and loose == 0:
			raise LineError(f"Enter a quantity for {code}.")
		line = {"item_code": code, "bulk_qty": bulk, "loose_qty": loose}
		if "bulk_rate" in row:
			rate = to_number(row.get("bulk_rate"), f"Cost for {code}", default=-1)
			if rate != -1:
				if rate < 0:
					raise LineError(f"Cost for {code} cannot be negative.")
				line["bulk_rate"] = rate
		out.append(line)
	return out


def _clean(x: float) -> float | int:
	"""Drop float noise and return an int when the number is whole."""
	x = round(float(x), 6)
	return int(x) if x == int(x) else x


def _fmt(x: float) -> str:
	x = _clean(x)
	return f"{x:,}" if isinstance(x, int) else f"{x:,.2f}".rstrip("0").rstrip(".")

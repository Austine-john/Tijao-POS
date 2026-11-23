"""Stock counts with owner approval (maker-checker), built on ERPNext Stock Reconciliation.

Flow
1. Anyone (cashier or owner) counts what is on the shelf -> ``save_count``.
   Only products whose count differs from the system are kept. They are saved as a
   DRAFT Stock Reconciliation tagged ``tijao_status = Pending`` with a reason.
   Nothing changes in stock yet.
2. The owner approves (``approve_count``) or rejects (``reject_count``).

Why approval re-bases to *now* instead of back-dating:
sales and deliveries keep happening between the count and the approval. Posting
the draft at count time would force ERPNext to re-post every later transaction.
Instead each row keeps what was counted (``tijao_counted_qty``) and what the system
said at that moment (``tijao_system_qty``). At approval:

    new qty = counted + (stock now - stock at count time)

which is exactly the shelf as counted, carried forward by whatever moved since.
"""

from __future__ import annotations

import frappe
from frappe.utils import cint, flt, get_fullname, now_datetime, nowdate, nowtime

from tijao_pos.services.catalog_service import get_buying_prices_per_bulk, get_item_units, get_stock_qty
from tijao_pos.services.stock_service import _require_items, _valuation_rate, get_stock_levels
from tijao_pos.utils.elevate import real_user, stamp_owner, writes_as_system
from tijao_pos.utils.errors import TijaoError
from tijao_pos.utils.lines import describe_qty, normalise_bulk_lines, to_sale_units

SR = "Stock Reconciliation"
REASONS = ("Routine count", "Broken", "Missing", "Found extra", "Expired", "Other")


@writes_as_system
def save_count(settings, counts: list[dict], reason: str, notes: str | None = None) -> dict:
	if reason not in REASONS:
		raise TijaoError("Choose a reason for this count.")
	notes = (notes or "").strip()[:500] or None
	if reason == "Other" and not notes:
		raise TijaoError("Write a short note explaining the count.")

	lines = normalise_bulk_lines(counts, what="stock count", allow_zero=True)
	codes = [line["item_code"] for line in lines]
	units = _require_items(codes)
	bins = get_stock_qty(codes, settings.warehouse)
	list_rates = get_buying_prices_per_bulk(codes, settings)

	rows = []
	for line in lines:
		u = units[line["item_code"]]
		counted = to_sale_units(line["bulk_qty"], line["loose_qty"], u.per_bulk)
		b = bins.get(u.item_code)
		system = flt(b.actual_qty) if b else 0
		if counted == system:
			continue
		rate = _valuation_rate(u, b, list_rates)
		if counted and not rate:
			raise TijaoError(
				f"{u.item_name} has no buying price yet. Record a delivery or set a price first."
			)
		rows.append(
			{
				"item_code": u.item_code,
				"warehouse": settings.warehouse,
				"qty": counted,
				"valuation_rate": rate,
				"tijao_counted_qty": counted,
				"tijao_system_qty": system,
			}
		)

	if not rows:
		return {"count": None, "message": "Everything you counted matches the system. Nothing to approve."}

	sr = frappe.new_doc(SR)
	sr.update(
		{
			"company": settings.company,
			"purpose": "Stock Reconciliation",
			"posting_date": nowdate(),
			"posting_time": nowtime(),
			"set_posting_time": 1,
			"tijao_status": "Pending",
			"tijao_reason": reason,
			"tijao_notes": notes,
			"tijao_counted_at": now_datetime(),
		}
	)
	for row in rows:
		sr.append("items", row)
	sr.flags.ignore_permissions = True
	sr.insert()
	stamp_owner(sr)
	return {
		"count": get_count(settings, sr.name),
		"message": "Count saved. The owner will check and approve it.",
	}


@writes_as_system
def approve_count(settings, name: str, note: str | None = None) -> dict:
	sr = _get_pending(name)
	codes = [r.item_code for r in sr.items]
	bins = get_stock_qty(codes, settings.warehouse)
	units = get_item_units(codes)

	keep = []
	for row in sr.items:
		now_qty = flt(bins[row.item_code].actual_qty) if row.item_code in bins else 0
		moved = now_qty - flt(row.tijao_system_qty)
		new_qty = flt(row.tijao_counted_qty) + moved
		if new_qty < 0:
			u = units.get(row.item_code)
			raise TijaoError(
				f"{u.item_name if u else row.item_code}: more was sold since the count than was counted. "
				"Please count it again."
			)
		if new_qty != now_qty:
			row.qty = new_qty
			keep.append(row)

	sr.tijao_approved_by = real_user()
	sr.tijao_review_note = (note or "").strip()[:500] or None

	if not keep:
		# Everything already matches now (e.g. a delivery covered the gap). Nothing to post.
		sr.tijao_status = "Approved"
		sr.flags.ignore_permissions = True
		sr.save()
		return {
			"count": get_count(settings, sr.name),
			"message": "Stock already matches. Nothing needed changing.",
		}

	sr.set("items", keep)
	sr.posting_date = nowdate()
	sr.posting_time = nowtime()
	sr.set_posting_time = 1
	sr.tijao_status = "Approved"
	sr.flags.ignore_permissions = True
	sr.save()
	sr.submit()
	return {
		"count": get_count(settings, sr.name),
		"message": "Approved. Stock now matches the count.",
		"stock": get_stock_levels(settings, [r.item_code for r in keep]),
	}


@writes_as_system
def reject_count(settings, name: str, note: str | None = None) -> dict:
	sr = _get_pending(name)
	sr.tijao_status = "Rejected"
	sr.tijao_approved_by = real_user()
	sr.tijao_review_note = (note or "").strip()[:500] or None
	sr.flags.ignore_permissions = True
	sr.save()
	return {"count": get_count(settings, sr.name), "message": "Count rejected. Stock was not changed."}


def _get_pending(name: str):
	sr = _get_tijao_count(name)
	if sr.tijao_status != "Pending" or sr.docstatus != 0:
		raise TijaoError(f"This count was already {(sr.tijao_status or 'processed').lower()}.")
	return sr


def _get_tijao_count(name: str):
	if not name or not frappe.db.exists(SR, name):
		raise TijaoError("That count was not found.")
	sr = frappe.get_doc(SR, name)
	if not sr.get("tijao_status"):
		raise TijaoError("That count was not found.")
	return sr


# ---------------------------------------------------------------------------
# Reading
# ---------------------------------------------------------------------------


def get_count(settings, name: str) -> dict:
	sr = _get_tijao_count(name)
	units = get_item_units([r.item_code for r in sr.items])
	rows = []
	for r in sr.items:
		u = units.get(r.item_code)
		counted = flt(r.tijao_counted_qty if r.get("tijao_counted_qty") is not None else r.qty)
		system = flt(r.tijao_system_qty if r.get("tijao_system_qty") is not None else r.current_qty)
		diff = counted - system
		rows.append(
			{
				"item_code": r.item_code,
				"item_name": u.item_name if u else r.item_code,
				"system_qty": system,
				"counted_qty": counted,
				"difference": diff,
				"value": flt(diff * flt(r.valuation_rate), 2),
				"system_text": describe_qty(system, u.sale_uom, u.bulk_uom, u.per_bulk) if u else str(system),
				"counted_text": describe_qty(counted, u.sale_uom, u.bulk_uom, u.per_bulk)
				if u
				else str(counted),
			}
		)
	return {
		"name": sr.name,
		"status": sr.tijao_status,
		"reason": sr.tijao_reason,
		"notes": sr.tijao_notes,
		"counted_at": str(sr.tijao_counted_at) if sr.tijao_counted_at else None,
		"counted_by": get_fullname(sr.owner),
		"reviewed_by": get_fullname(sr.tijao_approved_by) if sr.tijao_approved_by else None,
		"review_note": sr.tijao_review_note,
		"applied": sr.docstatus == 1,
		"rows": rows,
		"items_changed": len(rows),
		"total_difference": sum(r["difference"] for r in rows),
		"total_value": flt(sum(r["value"] for r in rows), 2),
	}


def list_counts(settings, status: str | None = None, limit: int = 30) -> list[dict]:
	limit = max(1, min(cint(limit) or 30, 200))
	filters = {
		"company": settings.company,
		"tijao_status": ["in", ["Pending", "Approved", "Rejected"]],
		"docstatus": ["<", 2],
	}
	if status in ("Pending", "Approved", "Rejected"):
		filters["tijao_status"] = status
	names = frappe.get_all(
		SR, filters=filters, order_by="tijao_counted_at desc, creation desc", limit=limit, pluck="name"
	)
	out = []
	for n in names:
		c = get_count(settings, n)
		c.pop("rows")
		out.append(c)
	return out


def pending_count() -> int:
	return frappe.db.count(SR, {"tijao_status": "Pending", "docstatus": 0})

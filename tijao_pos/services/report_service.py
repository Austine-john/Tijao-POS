"""Performance summary: sales, money in by payment mode, cost of goods sold, profit."""

from __future__ import annotations

from datetime import timedelta

import frappe
from frappe.utils import add_days, date_diff, flt, get_first_day, getdate, nowdate

from tijao_pos.utils.errors import TijaoError
from tijao_pos.utils.settings import payment_mode_key

PERIODS = ("today", "week", "month", "custom")
MAX_DAYS = 366


def resolve_period(period: str | None, from_date=None, to_date=None):
	today = getdate(nowdate())
	period = (period or ("custom" if from_date or to_date else "today")).lower()
	if period == "today":
		return period, today, today
	if period == "week":
		# Week starts on Monday regardless of system settings: that's how the shop thinks.
		return period, today - timedelta(days=today.weekday()), today
	if period == "month":
		return period, getdate(get_first_day(today)), today
	if period == "custom":
		if not from_date or not to_date:
			raise TijaoError("Choose both a start and an end date.")
		try:
			start, end = getdate(from_date), getdate(to_date)
		except Exception:
			raise TijaoError("Those dates are not valid.")
		if start > end:
			raise TijaoError("The start date must be before the end date.")
		if date_diff(end, start) >= MAX_DAYS:
			raise TijaoError("Choose a period of one year or less.")
		return period, start, end
	raise TijaoError("Choose today, this week, this month, or custom dates.")


def get_summary(settings, period: str | None = None, from_date=None, to_date=None) -> dict:
	period, start, end = resolve_period(period, from_date, to_date)
	args = {"company": settings.company, "start": start, "end": end}
	pos_filter = """si.docstatus = 1 and si.is_pos = 1 and si.is_return = 0
		and si.company = %(company)s and si.posting_date between %(start)s and %(end)s"""

	totals = frappe.db.sql(
		f"""select count(*) as sales_count,
			coalesce(sum(si.base_net_total), 0) as net_sales,
			coalesce(sum(if(si.base_rounded_total, si.base_rounded_total, si.base_grand_total)), 0) as total
		from `tabSales Invoice` si where {pos_filter}""",
		args,
		as_dict=True,
	)[0]

	payments = frappe.db.sql(
		f"""select p.mode_of_payment, coalesce(sum(p.base_amount), 0) as amount, count(distinct si.name) as count
		from `tabSales Invoice Payment` p
		join `tabSales Invoice` si on si.name = p.parent and p.parenttype = 'Sales Invoice'
		where {pos_filter}
		group by p.mode_of_payment""",
		args,
		as_dict=True,
	)

	# COGS = value that left the stock ledger for these POS invoices.
	cogs_by_item = frappe.db.sql(
		f"""select sle.item_code, sle.posting_date, coalesce(sum(-sle.stock_value_difference), 0) as cogs
		from `tabStock Ledger Entry` sle
		join `tabSales Invoice` si on si.name = sle.voucher_no and sle.voucher_type = 'Sales Invoice'
		where sle.is_cancelled = 0 and {pos_filter}
		group by sle.item_code, sle.posting_date""",
		args,
		as_dict=True,
	)

	items = frappe.db.sql(
		f"""select sii.item_code, max(sii.item_name) as item_name,
			coalesce(sum(sii.stock_qty), 0) as qty, coalesce(sum(sii.base_net_amount), 0) as net_sales
		from `tabSales Invoice Item` sii
		join `tabSales Invoice` si on si.name = sii.parent and sii.parenttype = 'Sales Invoice'
		where {pos_filter}
		group by sii.item_code""",
		args,
		as_dict=True,
	)

	daily_sales = frappe.db.sql(
		f"""select si.posting_date, count(*) as count, coalesce(sum(si.base_net_total), 0) as net_sales
		from `tabSales Invoice` si where {pos_filter}
		group by si.posting_date""",
		args,
		as_dict=True,
	)

	voided = frappe.db.sql(
		"""select count(*) from `tabSales Invoice` si where si.docstatus = 2 and si.is_pos = 1
		and si.company = %(company)s and si.posting_date between %(start)s and %(end)s""",
		args,
	)[0][0]

	cogs_item: dict[str, float] = {}
	cogs_day: dict = {}
	for r in cogs_by_item:
		cogs_item[r.item_code] = cogs_item.get(r.item_code, 0) + flt(r.cogs)
		cogs_day[getdate(r.posting_date)] = cogs_day.get(getdate(r.posting_date), 0) + flt(r.cogs)

	net_sales = flt(totals.net_sales, 2)
	cogs = flt(sum(cogs_item.values()), 2)
	profit = flt(net_sales - cogs, 2)

	by_mode = {"cash": 0.0, "mpesa": 0.0, "other": 0.0}
	for p in payments:
		key = payment_mode_key(settings, p.mode_of_payment) or "other"
		by_mode[key] = flt(by_mode[key] + flt(p.amount), 2)

	top = sorted(items, key=lambda r: flt(r.net_sales), reverse=True)[:10]
	sales_by_day = {getdate(r.posting_date): r for r in daily_sales}
	series = []
	day = start
	while day <= end:
		row = sales_by_day.get(day)
		day_sales = flt(row.net_sales, 2) if row else 0
		series.append(
			{
				"date": str(day),
				"sales_count": int(row["count"]) if row else 0,
				"net_sales": day_sales,
				"profit": flt(day_sales - cogs_day.get(day, 0), 2),
			}
		)
		day = getdate(add_days(day, 1))

	return {
		"period": period,
		"from_date": str(start),
		"to_date": str(end),
		"currency": settings.currency,
		"sales_count": int(totals.sales_count),
		"net_sales": net_sales,
		"total": flt(totals.total, 2),
		"payments": by_mode,
		"cogs": cogs,
		"gross_profit": profit,
		"margin_pct": flt(profit / net_sales * 100, 1) if net_sales else 0,
		"voided_count": int(voided),
		"top_items": [
			{
				"item_code": r.item_code,
				"item_name": r.item_name,
				"qty": flt(r.qty),
				"net_sales": flt(r.net_sales, 2),
				"profit": flt(flt(r.net_sales) - cogs_item.get(r.item_code, 0), 2),
			}
			for r in top
		],
		"daily": series,
	}

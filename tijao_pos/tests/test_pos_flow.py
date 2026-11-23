"""End-to-end flow against a real (test) site.

    bench --site test_site set-config allow_tests true
    bench --site test_site run-tests --app tijao_pos

NEVER run this on the shop's live site: ERPNext's own ``before_tests`` hook
deletes every Item Price on the target site.

Tests run in order (test_01, test_02, ...) and build on each other; everything is
rolled back when the class finishes.
"""

from __future__ import annotations

import io
import uuid
from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import flt
from openpyxl import Workbook

from tijao_pos.api import auth, catalog, dashboard, sales, shift, stock
from tijao_pos.services import import_service
from tijao_pos.setup.install import bootstrap
from tijao_pos.utils.errors import TijaoError
from tijao_pos.utils.permissions import CASHIER, OWNER
from tijao_pos.utils.settings import get_settings
from tijao_pos.utils.template import ITEM_COLUMNS

COKE, FANTA, WATER = "TJT-COKE-500ML", "TJT-FANTA-300ML", "TJT-WATER-500ML"
CASHIER_USER = "tijao-cashier@example.com"
OWNER_USER = "tijao-owner@example.com"


def workbook(coke_price=60) -> bytes:
	wb = Workbook()
	ws = wb.active
	ws.title = "UOMs"
	for row in (["uom_name", "whole_number_only"], ["Bottle", "yes"], ["Crate", "yes"]):
		ws.append(row)
	items = wb.create_sheet("Items")
	items.append(ITEM_COLUMNS)
	# code, name, brand, sale_uom, bulk_uom, per_bulk, sell, buy/bulk, open bulk, open loose
	items.append([COKE, "Test Coke 500ml", "TJT Cola", "Bottle", "Crate", 24, coke_price, 1080, 1, 0])
	items.append([FANTA, "Test Fanta 300ml", None, "Bottle", "Crate", 24, 50, 960, 0, 0])
	items.append([WATER, "Test Water 500ml", None, "Bottle", None, None, 30, 20, 0, 10])
	buf = io.BytesIO()
	wb.save(buf)
	return buf.getvalue()


def ref() -> str:
	return uuid.uuid4().hex


class TestTijaoPOSFlow(FrappeTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		frappe.set_user("Administrator")
		bootstrap()
		frappe.clear_document_cache("Tijao POS Settings", "Tijao POS Settings")
		cls.settings = get_settings()
		for email, role in ((CASHIER_USER, CASHIER), (OWNER_USER, OWNER)):
			if not frappe.db.exists("User", email):
				user = frappe.get_doc(
					{"doctype": "User", "email": email, "first_name": role, "send_welcome_email": 0}
				)
				user.insert(ignore_permissions=True)
			user = frappe.get_doc("User", email)
			user.set("roles", [])
			user.append("roles", {"role": role})
			user.save(ignore_permissions=True)
		cls.ctx = {}

	def tearDown(self):
		frappe.set_user("Administrator")

	# --- helpers ------------------------------------------------------------

	def qty(self, item_code) -> float:
		return flt(
			frappe.db.get_value(
				"Bin", {"item_code": item_code, "warehouse": self.settings.warehouse}, "actual_qty"
			)
		)

	def as_user(self, user):
		frappe.set_user(user)

	# --- import -------------------------------------------------------------

	def test_01_import_dry_run_writes_nothing(self):
		res = import_service.run_import(self.settings, workbook(), dry_run=True)
		self.assertTrue(res["ok"], res["errors"])
		self.assertEqual(res["summary"]["create"], 3)
		self.assertEqual(res["summary"]["opening_to_post"], 2)
		self.assertFalse(frappe.db.exists("Item", COKE))

	def test_02_import_creates_items_prices_and_opening_stock(self):
		res = import_service.run_import(self.settings, workbook(), dry_run=False)
		self.assertTrue(res["ok"], res["errors"])
		self.assertTrue(res["summary"]["opening_stock_entry"])

		item = frappe.get_doc("Item", COKE)
		self.assertEqual(item.stock_uom, "Bottle")
		self.assertEqual(item.purchase_uom, "Crate")
		self.assertEqual({d.uom: d.conversion_factor for d in item.uoms}, {"Bottle": 1, "Crate": 24})

		self.assertEqual(self.qty(COKE), 24)
		self.assertEqual(self.qty(WATER), 10)
		self.assertEqual(self.qty(FANTA), 0)

		products = {p["item_code"]: p for p in catalog.get_products()}
		self.assertEqual(products[COKE]["price"], 60)
		self.assertEqual(products[COKE]["stock_text"], "1 crate")
		self.assertEqual(products[WATER]["bulk_uom"], None)

	def test_03_import_is_idempotent(self):
		res = import_service.run_import(self.settings, workbook(coke_price=65), dry_run=False)
		self.assertTrue(res["ok"], res["errors"])
		self.assertEqual(res["summary"]["update"], 3)
		self.assertEqual(res["summary"]["opening_to_post"], 0)
		self.assertIsNone(res["summary"]["opening_stock_entry"])
		self.assertEqual(self.qty(COKE), 24)  # not double counted
		self.assertEqual({p["item_code"]: p["price"] for p in catalog.get_products()}[COKE], 65)

	def test_04_import_reports_errors_and_writes_nothing(self):
		wb = Workbook()
		ws = wb.active
		ws.title = "Items"
		ws.append(ITEM_COLUMNS)
		ws.append(["TJT-BAD", "Bad", None, "Glass", None, None, 0, 0, 0, 0])
		buf = io.BytesIO()
		wb.save(buf)
		res = import_service.run_import(self.settings, buf.getvalue(), dry_run=False)
		self.assertFalse(res["ok"])
		self.assertGreaterEqual(len(res["errors"]), 2)
		self.assertFalse(frappe.db.exists("Item", "TJT-BAD"))

	# --- receiving ------------------------------------------------------------

	def test_05_receive_stock(self):
		self.as_user(OWNER_USER)
		res = stock.receive_stock(
			items=[
				{"item_code": COKE, "bulk_qty": 2, "loose_qty": 6, "bulk_rate": 1200},
				{"item_code": FANTA, "bulk_qty": 1},  # rate from buying price list (960)
			]
		)
		pr = frappe.get_doc("Purchase Receipt", res["receipt"])
		self.assertEqual(pr.docstatus, 1)
		self.assertEqual(flt(pr.grand_total), 2 * 1200 + 6 * 50 + 960)
		self.assertEqual(self.qty(COKE), 24 + 54)
		self.assertEqual(self.qty(FANTA), 24)
		levels = {r["item_code"]: r for r in stock.get_stock_levels()}
		self.assertEqual(levels[COKE]["stock_text"], "3 crates + 6 bottles")

	# --- selling -------------------------------------------------------------

	def test_05b_no_shift_no_selling(self):
		self.as_user(CASHIER_USER)
		self.assertIsNone(shift.current())
		with self.assertRaises(TijaoError) as cm:
			sales.make_sale(items=[{"item_code": COKE, "qty": 1}], payment_mode="cash", client_ref=ref())
		self.assertIn("No shift is open", str(cm.exception))

	def test_05c_open_shift_and_payout(self):
		self.as_user(CASHIER_USER)
		opened = shift.open_shift(opening_float=1000)
		self.assertEqual(opened["status"], "Open")
		self.assertTrue(opened["is_mine"])
		with self.assertRaises(TijaoError):
			shift.open_shift(opening_float=500)  # one drawer -> one open shift
		with self.assertRaises(TijaoError):
			shift.add_payout(amount=50, reason="Other")  # "Other" needs a note
		res = shift.add_payout(amount=100, reason="Transport", note="Boda to depot")
		self.assertEqual(res["payouts"][0]["amount"], 100)
		self.ctx["shift"] = opened["name"]

	def test_06_sell_cash_and_mpesa(self):
		self.as_user(CASHIER_USER)
		cash = sales.make_sale(
			items=[
				{"item_code": COKE, "qty": 1},
				{"item_code": WATER, "qty": 2},
				{"item_code": COKE, "qty": 2},
			],
			payment_mode="cash",
			client_ref=ref(),
		)
		self.assertEqual(cash["status"], "paid")
		self.assertEqual(cash["total"], 3 * 65 + 2 * 30)
		self.assertEqual(cash["payment"]["mode"], "cash")
		self.assertEqual(len(cash["lines"]), 2)  # duplicate taps merged

		ref_m = ref()
		mpesa = sales.make_sale(
			items=frappe.as_json([{"item_code": FANTA, "qty": 1}]),  # JSON string, as the UI sends
			payment_mode="mpesa",
			mpesa_ref=" qk12ab34cd ",
			client_ref=ref_m,
		)
		self.assertEqual(mpesa["payment"]["mpesa_ref"], "QK12AB34CD")
		self.assertEqual(mpesa["payment"]["amount"], 50)

		si = frappe.get_doc("Sales Invoice", mpesa["invoice"])
		self.assertEqual((si.docstatus, si.is_pos, si.update_stock), (1, 1, 1))
		self.assertEqual(flt(si.outstanding_amount), 0)
		self.assertEqual([p.mode_of_payment for p in si.payments], [self.settings.mpesa_mode_of_payment])

		self.assertEqual(self.qty(COKE), 75)
		self.assertEqual(self.qty(WATER), 8)
		self.assertEqual(self.qty(FANTA), 23)
		self.ctx.update(cash=cash["invoice"], mpesa=mpesa["invoice"], mpesa_ref=ref_m)

	def test_07_duplicate_client_ref_returns_same_invoice(self):
		self.as_user(CASHIER_USER)
		before = frappe.db.count("Sales Invoice")
		again = sales.make_sale(
			items=[{"item_code": FANTA, "qty": 1}], payment_mode="mpesa", client_ref=self.ctx["mpesa_ref"]
		)
		self.assertEqual(again["invoice"], self.ctx["mpesa"])
		self.assertTrue(again["duplicate"])
		self.assertEqual(frappe.db.count("Sales Invoice"), before)
		self.assertEqual(self.qty(FANTA), 23)

	def test_08_receipt_and_recent_sales(self):
		self.as_user(CASHIER_USER)
		sale = sales.get_sale(invoice=self.ctx["cash"])
		self.assertEqual(sale["lines"][0]["item_code"], COKE)
		self.assertEqual(sale["lines"][0]["qty"], 3)
		recent = sales.recent_sales(limit=5)
		self.assertEqual(recent[0]["invoice"], self.ctx["mpesa"])
		self.assertEqual(recent[0]["payment_mode"], "mpesa")

	# --- summary -------------------------------------------------------------

	def test_09_summary_numbers(self):
		self.as_user(OWNER_USER)
		s = dashboard.get_summary(period="today")
		# FIFO cost: COKE 3 from opening @1080/24=45, WATER 2 @20, FANTA 1 @960/24=40
		expected_cogs = 3 * 45 + 2 * 20 + 1 * 40
		expected_sales = 3 * 65 + 2 * 30 + 50
		self.assertEqual(s["sales_count"], 2)
		self.assertEqual(s["net_sales"], expected_sales)
		self.assertEqual(s["payments"]["cash"], 3 * 65 + 2 * 30)
		self.assertEqual(s["payments"]["mpesa"], 50)
		self.assertAlmostEqual(s["cogs"], expected_cogs, places=2)
		self.assertAlmostEqual(s["gross_profit"], expected_sales - expected_cogs, places=2)
		self.assertAlmostEqual(
			s["margin_pct"], round((expected_sales - expected_cogs) / expected_sales * 100, 1)
		)
		self.assertEqual(s["top_items"][0]["item_code"], COKE)
		self.assertEqual(len(s["daily"]), 1)
		self.assertEqual(s["daily"][0]["net_sales"], expected_sales)

		week = dashboard.get_summary(period="week")
		self.assertEqual(week["net_sales"], expected_sales)

	# --- void ----------------------------------------------------------------

	def test_10_void_restores_stock(self):
		self.as_user(OWNER_USER)
		res = sales.void_sale(invoice=self.ctx["mpesa"])
		self.assertEqual(res["status"], "voided")
		self.assertEqual(self.qty(FANTA), 24)
		s = dashboard.get_summary(period="today")
		self.assertEqual(s["sales_count"], 1)
		self.assertEqual(s["voided_count"], 1)
		self.assertEqual(s["payments"]["mpesa"], 0)
		with self.assertRaises(TijaoError):
			sales.void_sale(invoice=self.ctx["mpesa"])

	# --- permissions ------------------------------------------------------------

	def test_11_cashier_blocked_from_owner_endpoints(self):
		self.as_user(CASHIER_USER)
		self.assertTrue(catalog.get_products())
		self.assertTrue(stock.get_stock_levels())
		me = auth.me()
		self.assertFalse(me["is_owner"])
		self.assertTrue(me["is_cashier"])
		for call in (
			lambda: sales.void_sale(invoice=self.ctx["cash"]),
			lambda: stock.receive_stock(items=[{"item_code": COKE, "bulk_qty": 1}]),
			lambda: stock.approve_stock_count(name="x"),
			lambda: stock.reject_stock_count(name="x"),
			lambda: stock.get_buying_prices(),
			lambda: shift.list_shifts(),
			lambda: shift.review_shift(name="x"),
			lambda: dashboard.get_summary(period="today"),
			lambda: catalog.import_master_data(file_url="/files/x.xlsx"),
		):
			with self.assertRaises(frappe.PermissionError):
				call()
		self.assertEqual(frappe.db.get_value("Sales Invoice", self.ctx["cash"], "docstatus"), 1)

	def test_12_guest_blocked(self):
		frappe.set_user("Guest")
		with self.assertRaises(frappe.AuthenticationError):
			auth.me()
		with self.assertRaises(frappe.AuthenticationError):
			catalog.get_products()

	# --- failures leave nothing behind -----------------------------------------

	def test_13_overselling_fails_cleanly(self):
		self.as_user(CASHIER_USER)
		before = frappe.db.count("Sales Invoice")
		with self.assertRaises(TijaoError) as cm:
			sales.make_sale(items=[{"item_code": WATER, "qty": 999}], payment_mode="cash", client_ref=ref())
		self.assertIn("Not enough", str(cm.exception))
		self.assertEqual(frappe.db.count("Sales Invoice"), before)

	def test_14_erpnext_failure_mid_sale_rolls_back(self):
		"""Skip our own stock pre-check so ERPNext itself raises during submit."""
		self.as_user(CASHIER_USER)
		client_ref = ref()
		sle_before = frappe.db.count("Stock Ledger Entry")
		fake = {WATER: frappe._dict(item_code=WATER, actual_qty=10_000)}
		with patch("tijao_pos.services.sales_service.get_stock_qty", return_value=fake):
			with self.assertRaises(TijaoError) as cm:
				sales.make_sale(
					items=[{"item_code": WATER, "qty": 999}], payment_mode="cash", client_ref=client_ref
				)
		self.assertIn("Not enough stock", str(cm.exception))
		self.assertFalse(frappe.db.exists("Sales Invoice", {"tijao_client_ref": client_ref}))
		self.assertEqual(frappe.db.count("Stock Ledger Entry"), sle_before)
		self.assertEqual(self.qty(WATER), 8)

	def test_15_bad_input_gets_friendly_errors(self):
		self.as_user(CASHIER_USER)
		cases = [
			(dict(items=[], payment_mode="cash", client_ref=ref()), "empty"),
			(
				dict(items=[{"item_code": COKE, "qty": 1}], payment_mode="card", client_ref=ref()),
				"Cash or M-Pesa",
			),
			(
				dict(items=[{"item_code": COKE, "qty": 1}], payment_mode="cash", client_ref="x"),
				"missing its id",
			),
			(
				dict(items=[{"item_code": "NOPE", "qty": 1}], payment_mode="cash", client_ref=ref()),
				"not a product",
			),
			(
				dict(
					items=[{"item_code": COKE, "qty": 1}],
					payment_mode="mpesa",
					mpesa_ref="??",
					client_ref=ref(),
				),
				"M-Pesa code",
			),
		]
		for kwargs, msg in cases:
			with self.assertRaises(TijaoError) as cm:
				sales.make_sale(**kwargs)
			self.assertIn(msg, str(cm.exception))

	# --- counts --------------------------------------------------------------

	def test_16_stock_count_needs_owner_approval(self):
		# COKE is 75 (3 crates + 3). The cashier counts 3 crates exactly (3 broken).
		self.as_user(CASHIER_USER)
		with self.assertRaises(TijaoError):
			stock.save_stock_count(counts=[{"item_code": COKE, "bulk_qty": 3}], reason="Nonsense")
		saved = stock.save_stock_count(
			counts=[{"item_code": COKE, "bulk_qty": 3, "loose_qty": 0}], reason="Broken", notes="3 broke"
		)
		count = saved["count"]
		self.assertEqual(count["status"], "Pending")
		self.assertEqual(count["rows"][0]["difference"], -3)
		rate = flt(
			frappe.db.get_value(
				"Bin", {"item_code": COKE, "warehouse": self.settings.warehouse}, "valuation_rate"
			)
		)
		self.assertAlmostEqual(count["total_value"], round(-3 * rate, 2), places=2)  # valued at current cost
		self.assertEqual(self.qty(COKE), 75)  # nothing changes until approved
		with self.assertRaises(frappe.PermissionError):
			stock.approve_stock_count(name=count["name"])

		# A sale happens between the count and the approval.
		sales.make_sale(items=[{"item_code": COKE, "qty": 1}], payment_mode="cash", client_ref=ref())
		self.assertEqual(self.qty(COKE), 74)

		self.as_user(OWNER_USER)
		pending = stock.list_stock_counts(status="Pending")
		self.assertIn(count["name"], [c["name"] for c in pending])
		res = stock.approve_stock_count(name=count["name"], note="ok")
		self.assertEqual(res["count"]["status"], "Approved")
		self.assertTrue(res["count"]["applied"])
		# Counted 72 at count time, 1 sold since -> 71 now.
		self.assertEqual(self.qty(COKE), 71)
		with self.assertRaises(TijaoError):
			stock.approve_stock_count(name=count["name"])

	def test_17_stock_count_reject_and_nothing_to_count(self):
		self.as_user(CASHIER_USER)
		same = stock.save_stock_count(
			counts=[{"item_code": WATER, "loose_qty": self.qty(WATER)}], reason="Routine count"
		)
		self.assertIsNone(same["count"])
		saved = stock.save_stock_count(counts=[{"item_code": WATER, "loose_qty": 1}], reason="Missing")
		self.as_user(OWNER_USER)
		res = stock.reject_stock_count(name=saved["count"]["name"], note="Recount please")
		self.assertEqual(res["count"]["status"], "Rejected")
		self.assertEqual(self.qty(WATER), 8)

	def test_18_blind_close(self):
		# Cashier never sees cash totals while the shift is open; the owner does.
		self.as_user(CASHIER_USER)
		cur = shift.current()
		self.assertNotIn("expected_cash", cur)
		self.assertNotIn("cash_sales", cur)
		self.as_user(OWNER_USER)
		live = shift.current()
		# Cash sales in this shift: test_06 (3x65 + 2x30) + test_16 (1x65). M-Pesa sale was voided.
		cash = 3 * 65 + 2 * 30 + 65
		self.assertEqual(live["cash_sales"], cash)
		self.assertEqual(live["mpesa_sales"], 0)
		self.assertEqual(live["expected_cash"], 1000 + cash - 100)

		self.as_user(CASHIER_USER)
		closed = shift.close_shift(counted_cash=1000 + cash - 100 - 20, notes="Short 20?")
		self.assertEqual(closed["status"], "Closed")
		self.assertEqual(closed["expected_cash"], 1000 + cash - 100)
		self.assertEqual(closed["cash_difference"], -20)
		self.assertEqual(closed["sales_count"], 2)
		self.assertEqual(closed["voided_count"], 1)
		self.assertEqual(
			frappe.db.get_value("Sales Invoice", self.ctx["cash"], "tijao_shift"), self.ctx["shift"]
		)

		# Shift closed -> till locked again.
		with self.assertRaises(TijaoError):
			sales.make_sale(items=[{"item_code": WATER, "qty": 1}], payment_mode="cash", client_ref=ref())
		with self.assertRaises(frappe.PermissionError):
			shift.list_shifts()

	def test_19_owner_reviews_shift(self):
		self.as_user(OWNER_USER)
		rows = shift.list_shifts()
		self.assertEqual(rows[0]["name"], self.ctx["shift"])
		detail = shift.get_shift(name=self.ctx["shift"])
		self.assertEqual(len(detail["sales"]), 3)  # 2 paid + 1 voided
		res = shift.review_shift(name=self.ctx["shift"], note="Talked to cashier")
		self.assertEqual(res["reviewed"], 1)

"""Install hooks, idempotent bootstrap and command-line import.

Usage:
    bench --site <site> execute tijao_pos.setup.install.bootstrap
    bench --site <site> execute tijao_pos.setup.install.bootstrap --kwargs "{'company': 'My Shop'}"
    bench --site <site> execute tijao_pos.setup.install.import_from_path \
        --kwargs "{'path': '/path/master_data.xlsx', 'dry_run': 1}"

Bootstrap only fills *blank* settings, so re-running it never overrides what the owner changed.
"""

from __future__ import annotations

import frappe
from frappe.utils import cint

from tijao_pos.utils.permissions import CASHIER, OWNER
from tijao_pos.utils.settings import SETTINGS_DOCTYPE

# Default names created by bootstrap. They are written into Tijao POS Settings once;
# from then on the app only ever reads names from Settings.
DEFAULTS = {
	"warehouse": "Shop",
	"item_group": "Drinks",
	"walk_in_customer": "Walk-in Customer",
	"default_supplier": "Default Supplier",
	"pos_profile": "Tijao POS",
	"cash_mode_of_payment": "Cash",
	"mpesa_mode_of_payment": "M-Pesa",
	"mpesa_account": "M-Pesa",
	"selling_price_list": "Standard Selling",
	"buying_price_list": "Standard Buying",
	"time_zone": "Africa/Nairobi",
}


def after_install():
	ensure_roles()
	company = _default_company()
	if company:
		bootstrap(company)


def after_migrate():
	ensure_roles()


def ensure_roles() -> None:
	for role in (CASHIER, OWNER):
		if not frappe.db.exists("Role", role):
			frappe.get_doc({"doctype": "Role", "role_name": role, "desk_access": 0}).insert(
				ignore_permissions=True
			)
		elif cint(frappe.db.get_value("Role", role, "desk_access")):
			frappe.db.set_value("Role", role, "desk_access", 0)


def bootstrap(company: str | None = None) -> dict:
	"""Create everything the POS needs for ``company`` and fill blank settings. Safe to re-run."""
	ensure_roles()
	company = company or _default_company()
	if not company or not frappe.db.exists("Company", company):
		frappe.throw(f"Company '{company}' not found. Finish the ERPNext setup wizard first.")

	abbr, currency, cash_account, write_off, cost_center = frappe.get_cached_value(
		"Company",
		company,
		["abbr", "default_currency", "default_cash_account", "write_off_account", "cost_center"],
	)
	settings = frappe.get_single(SETTINGS_DOCTYPE)
	if settings.company and settings.company != company:
		frappe.throw(f"Tijao POS is already set up for {settings.company}.")

	_system_settings()

	warehouse = settings.warehouse or _ensure_warehouse(company, abbr)
	item_group = settings.item_group or _ensure_item_group(DEFAULTS["item_group"])
	customer = settings.walk_in_customer or _ensure_customer(DEFAULTS["walk_in_customer"])
	supplier = settings.default_supplier or _ensure_supplier(DEFAULTS["default_supplier"])
	selling = settings.selling_price_list or _ensure_price_list(
		DEFAULTS["selling_price_list"], currency, selling=1
	)
	buying = settings.buying_price_list or _ensure_price_list(
		DEFAULTS["buying_price_list"], currency, buying=1
	)

	cash_mop = settings.cash_mode_of_payment or _ensure_mode_of_payment(
		DEFAULTS["cash_mode_of_payment"], "Cash", company, cash_account
	)
	mpesa_mop = settings.mpesa_mode_of_payment or _ensure_mode_of_payment(
		DEFAULTS["mpesa_mode_of_payment"], "Phone", company, _ensure_mpesa_account(company, abbr, currency)
	)
	opening_account = settings.opening_stock_account or _temporary_opening_account(company)

	pos_profile = settings.pos_profile or _ensure_pos_profile(
		DEFAULTS["pos_profile"],
		company=company,
		currency=currency,
		warehouse=warehouse,
		customer=customer,
		selling_price_list=selling,
		cash_mop=cash_mop,
		mpesa_mop=mpesa_mop,
		cash_account=cash_account,
		write_off_account=write_off,
		cost_center=cost_center,
	)

	values = {
		"company": company,
		"warehouse": warehouse,
		"item_group": item_group,
		"walk_in_customer": customer,
		"default_supplier": supplier,
		"selling_price_list": selling,
		"buying_price_list": buying,
		"cash_mode_of_payment": cash_mop,
		"mpesa_mode_of_payment": mpesa_mop,
		"opening_stock_account": opening_account,
		"pos_profile": pos_profile,
		"receipt_header": settings.receipt_header or company,
	}
	for field, value in values.items():
		if not settings.get(field):
			settings.set(field, value)
	settings.flags.ignore_permissions = True
	settings.save()
	frappe.clear_document_cache(SETTINGS_DOCTYPE, SETTINGS_DOCTYPE)
	return values


def import_from_path(path: str, dry_run: int = 1, column_map: dict | None = None) -> dict:
	"""Run the master-data import from a file on disk (for bench execute / scripts)."""
	from tijao_pos.services import import_service
	from tijao_pos.utils.settings import get_settings

	with open(path, "rb") as f:
		content = f.read()
	result = import_service.run_import(
		get_settings(), content, dry_run=bool(cint(dry_run)), column_map=column_map
	)
	if result["ok"] and not result["dry_run"]:
		frappe.db.commit()
	return result


# ---------------------------------------------------------------------------
# helpers (each is idempotent)
# ---------------------------------------------------------------------------


def _default_company() -> str | None:
	return frappe.db.get_single_value("Global Defaults", "default_company") or frappe.db.get_value(
		"Company", {}
	)


def _system_settings() -> None:
	if not frappe.db.get_single_value("System Settings", "time_zone"):
		frappe.db.set_single_value("System Settings", "time_zone", DEFAULTS["time_zone"])
	# Never sell stock we don't have.
	if cint(frappe.db.get_single_value("Stock Settings", "allow_negative_stock")):
		frappe.db.set_single_value("Stock Settings", "allow_negative_stock", 0)


def _ensure_warehouse(company: str, abbr: str) -> str:
	name = f"{DEFAULTS['warehouse']} - {abbr}"
	if not frappe.db.exists("Warehouse", name):
		parent = frappe.db.get_value(
			"Warehouse", {"company": company, "is_group": 1, "parent_warehouse": ["in", ["", None]]}
		)
		frappe.get_doc(
			{
				"doctype": "Warehouse",
				"warehouse_name": DEFAULTS["warehouse"],
				"company": company,
				"parent_warehouse": parent,
			}
		).insert(ignore_permissions=True)
	return name


def _ensure_item_group(name: str) -> str:
	if not frappe.db.exists("Item Group", name):
		root = frappe.db.get_value("Item Group", {"is_group": 1, "parent_item_group": ["in", ["", None]]})
		frappe.get_doc(
			{"doctype": "Item Group", "item_group_name": name, "parent_item_group": root, "is_group": 0}
		).insert(ignore_permissions=True)
	return name


def _ensure_customer(name: str) -> str:
	existing = frappe.db.get_value("Customer", {"customer_name": name})
	if existing:
		return existing
	group = frappe.db.get_single_value("Selling Settings", "customer_group") or frappe.db.get_value(
		"Customer Group", {"is_group": 0}
	)
	territory = frappe.db.get_single_value("Selling Settings", "territory") or frappe.db.get_value(
		"Territory", {"is_group": 0}
	)
	doc = frappe.get_doc(
		{
			"doctype": "Customer",
			"customer_name": name,
			"customer_type": "Individual",
			"customer_group": group,
			"territory": territory,
		}
	).insert(ignore_permissions=True)
	return doc.name


def _ensure_supplier(name: str) -> str:
	existing = frappe.db.get_value("Supplier", {"supplier_name": name})
	if existing:
		return existing
	group = frappe.db.get_single_value("Buying Settings", "supplier_group") or frappe.db.get_value(
		"Supplier Group", {"is_group": 0}
	)
	doc = frappe.get_doc({"doctype": "Supplier", "supplier_name": name, "supplier_group": group}).insert(
		ignore_permissions=True
	)
	return doc.name


def _ensure_price_list(name: str, currency: str, selling: int = 0, buying: int = 0) -> str:
	if not frappe.db.exists("Price List", name):
		frappe.get_doc(
			{
				"doctype": "Price List",
				"price_list_name": name,
				"currency": currency,
				"selling": selling,
				"buying": buying,
				"enabled": 1,
			}
		).insert(ignore_permissions=True)
	return name


def _ensure_mpesa_account(company: str, abbr: str, currency: str) -> str:
	name = f"{DEFAULTS['mpesa_account']} - {abbr}"
	if not frappe.db.exists("Account", name):
		parent = frappe.db.get_value(
			"Account", {"company": company, "account_type": "Bank", "is_group": 1}, order_by="lft asc"
		)
		frappe.get_doc(
			{
				"doctype": "Account",
				"account_name": DEFAULTS["mpesa_account"],
				"company": company,
				"parent_account": parent,
				"account_type": "Bank",
				"account_currency": currency,
				"is_group": 0,
			}
		).insert(ignore_permissions=True)
	return name


def _ensure_mode_of_payment(name: str, mop_type: str, company: str, account: str) -> str:
	if frappe.db.exists("Mode of Payment", name):
		mop = frappe.get_doc("Mode of Payment", name)
	else:
		mop = frappe.get_doc({"doctype": "Mode of Payment", "mode_of_payment": name, "type": mop_type})
	mop.enabled = 1
	if not any(a.company == company for a in mop.accounts):
		mop.append("accounts", {"company": company, "default_account": account})
	mop.flags.ignore_permissions = True
	mop.save()
	return mop.name


def _temporary_opening_account(company: str) -> str | None:
	return frappe.db.get_value(
		"Account", {"company": company, "account_type": "Temporary", "is_group": 0}, order_by="lft asc"
	)


def _ensure_pos_profile(name: str, **kw) -> str:
	if frappe.db.exists("POS Profile", name):
		return name
	doc = frappe.get_doc(
		{
			"doctype": "POS Profile",
			"company": kw["company"],
			"currency": kw["currency"],
			"warehouse": kw["warehouse"],
			"customer": kw["customer"],
			"selling_price_list": kw["selling_price_list"],
			"write_off_account": kw["write_off_account"],
			"write_off_cost_center": kw["cost_center"],
			"cost_center": kw["cost_center"],
			"account_for_change_amount": kw["cash_account"],
			"write_off_limit": 1,
			"ignore_pricing_rule": 1,
			"payments": [
				{"mode_of_payment": kw["cash_mop"], "default": 1},
				{"mode_of_payment": kw["mpesa_mop"], "default": 0},
			],
		}
	)
	doc.insert(ignore_permissions=True, set_name=name)
	return doc.name

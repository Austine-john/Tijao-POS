"""Stock levels, deliveries and counts."""

import frappe

from tijao_pos.services import count_service, stock_service
from tijao_pos.utils import permissions
from tijao_pos.utils.errors import friendly_errors
from tijao_pos.utils.settings import get_settings


@frappe.whitelist(methods=["GET"])
@friendly_errors
def get_stock_levels():
	permissions.require_cashier()
	return stock_service.get_stock_levels(get_settings())


@frappe.whitelist(methods=["POST"])
@friendly_errors
def receive_stock(items, supplier: str | None = None):
	permissions.require_owner()
	return stock_service.receive_stock(get_settings(), frappe.parse_json(items), supplier=supplier)


@frappe.whitelist(methods=["GET"])
@friendly_errors
def get_buying_prices():
	"""Cost per crate/carton (or per unit when there is none). Owner only: cashiers don't see costs."""
	permissions.require_owner()
	return stock_service.get_buying_prices(get_settings())


# --- Stock counts (cashier counts, owner approves) -----------------------------


@frappe.whitelist(methods=["POST"])
@friendly_errors
def save_stock_count(counts, reason: str, notes: str | None = None):
	"""Anyone on the till can count; nothing changes in stock until the owner approves."""
	permissions.require_cashier()
	return count_service.save_count(get_settings(), frappe.parse_json(counts), reason, notes)


@frappe.whitelist(methods=["GET"])
@friendly_errors
def list_stock_counts(status: str | None = None, limit=30):
	permissions.require_cashier()
	return count_service.list_counts(get_settings(), status=status, limit=limit)


@frappe.whitelist(methods=["GET"])
@friendly_errors
def get_stock_count(name: str):
	permissions.require_cashier()
	return count_service.get_count(get_settings(), name)


@frappe.whitelist(methods=["POST"])
@friendly_errors
def approve_stock_count(name: str, note: str | None = None):
	permissions.require_owner()
	return count_service.approve_count(get_settings(), name, note)


@frappe.whitelist(methods=["POST"])
@friendly_errors
def reject_stock_count(name: str, note: str | None = None):
	permissions.require_owner()
	return count_service.reject_count(get_settings(), name, note)

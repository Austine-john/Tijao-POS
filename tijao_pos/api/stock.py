"""Stock levels, deliveries and counts."""

import frappe

from tijao_pos.services import stock_service
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


@frappe.whitelist(methods=["POST"])
@friendly_errors
def record_stock_count(counts):
	permissions.require_owner()
	return stock_service.record_stock_count(get_settings(), frappe.parse_json(counts))

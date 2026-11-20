"""Selling, receipts and voids."""

import frappe

from tijao_pos.services import sales_service
from tijao_pos.utils import permissions
from tijao_pos.utils.errors import friendly_errors
from tijao_pos.utils.settings import get_settings


@frappe.whitelist(methods=["POST"])
@friendly_errors
def make_sale(items, payment_mode: str, client_ref: str, mpesa_ref: str | None = None):
	permissions.require_cashier()
	return sales_service.make_sale(
		get_settings(),
		items=frappe.parse_json(items),
		payment_mode=payment_mode,
		mpesa_ref=mpesa_ref,
		client_ref=client_ref,
	)


@frappe.whitelist(methods=["GET"])
@friendly_errors
def recent_sales(limit=20):
	permissions.require_cashier()
	return sales_service.recent_sales(get_settings(), limit=limit)


@frappe.whitelist(methods=["GET"])
@friendly_errors
def get_sale(invoice: str):
	permissions.require_cashier()
	return sales_service.get_sale(get_settings(), invoice)


@frappe.whitelist(methods=["POST"])
@friendly_errors
def void_sale(invoice: str):
	permissions.require_owner()
	return sales_service.void_sale(get_settings(), invoice)

"""Products and master-data import."""

import frappe
from frappe.utils import sbool

from tijao_pos.services import catalog_service, import_service
from tijao_pos.utils import permissions
from tijao_pos.utils.errors import friendly_errors
from tijao_pos.utils.settings import get_settings


@frappe.whitelist(methods=["GET"])
@friendly_errors
def get_products():
	permissions.require_cashier()
	return catalog_service.get_products(get_settings())


@frappe.whitelist(methods=["POST"])
@friendly_errors
def import_master_data(file_url: str, dry_run=1, column_map=None):
	permissions.require_owner()
	column_map = frappe.parse_json(column_map) if column_map else None
	if column_map is not None and not isinstance(column_map, dict):
		raise frappe.ValidationError("column_map must be an object.")
	content = import_service.load_file_url(file_url)
	return import_service.run_import(get_settings(), content, dry_run=sbool(dry_run), column_map=column_map)

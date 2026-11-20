"""Owner's performance summary."""

import frappe

from tijao_pos.services import report_service
from tijao_pos.utils import permissions
from tijao_pos.utils.errors import friendly_errors
from tijao_pos.utils.settings import get_settings


@frappe.whitelist(methods=["GET"])
@friendly_errors
def get_summary(period: str | None = None, from_date: str | None = None, to_date: str | None = None):
	permissions.require_owner()
	return report_service.get_summary(get_settings(), period=period, from_date=from_date, to_date=to_date)

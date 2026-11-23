"""Who is logged in. The UI calls this right after login and on page load."""

import frappe

from tijao_pos.utils import permissions
from tijao_pos.utils.errors import friendly_errors
from tijao_pos.utils.settings import get_settings


@frappe.whitelist(methods=["GET"])
@friendly_errors
def me():
	if frappe.session.user == "Guest":
		raise frappe.AuthenticationError
	settings = get_settings(validate=False)
	roles = frappe.get_roles()
	return {
		"user": frappe.session.user,
		"full_name": frappe.utils.get_fullname(frappe.session.user),
		"roles": [r for r in roles if r in (permissions.CASHIER, permissions.OWNER, permissions.ADMIN)],
		"is_owner": permissions.is_owner(),
		"is_cashier": permissions.is_cashier(),
		"company": settings.company,
		"currency": settings.currency,
		"shop_name": settings.receipt_header or settings.company,
		"receipt_footer": settings.receipt_footer,
		# Session-bound token the UI must send as X-Frappe-CSRF-Token on every POST.
		"csrf_token": frappe.sessions.get_csrf_token(),
	}

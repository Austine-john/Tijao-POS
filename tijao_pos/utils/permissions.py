"""Role gates. Only the API layer calls these; services never check roles."""

from __future__ import annotations

import frappe

CASHIER = "Tijao Cashier"
OWNER = "Tijao Owner"
# System Manager can do everything so the developer can test from a desk login.
ADMIN = "System Manager"

CASHIER_ROLES = (CASHIER, OWNER, ADMIN)
OWNER_ROLES = (OWNER, ADMIN)


def require_cashier() -> None:
	_require(CASHIER_ROLES)


def require_owner() -> None:
	_require(OWNER_ROLES)


def is_owner(user: str | None = None) -> bool:
	return bool(set(frappe.get_roles(user)) & set(OWNER_ROLES))


def is_cashier(user: str | None = None) -> bool:
	return bool(set(frappe.get_roles(user)) & set(CASHIER_ROLES))


def _require(roles: tuple[str, ...]) -> None:
	if frappe.session.user == "Guest":
		raise frappe.AuthenticationError("Please log in.")
	# Same contract as frappe.only_for (PermissionError -> HTTP 403), but frappe.only_for
	# silently allows everything when frappe.flags.in_test is set, so the role gate
	# could never be tested. This check is never bypassed.
	if set(roles).isdisjoint(frappe.get_roles()):
		raise frappe.PermissionError("Not permitted")

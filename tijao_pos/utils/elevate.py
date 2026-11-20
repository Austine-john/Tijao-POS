"""Run ERPNext writes as a system user.

Why: shop users are website users with only the Tijao roles. Services write
with ``ignore_permissions=True``, but ERPNext also calls
``frappe.has_permission(..., throw=True)`` *inside* its own code (e.g. the
Stock Reconciliation balance lookup, POS Profile read), which ignores that flag.

So services that write run inside ``as_system()``. Security still comes from the
API role gate, which runs *before* any service is called. The real user is
written back as the document ``owner`` so receipts show who sold.
"""

from __future__ import annotations

import functools
from contextlib import contextmanager

import frappe

SYSTEM_USER = "Administrator"


def real_user() -> str:
	return getattr(frappe.local, "tijao_real_user", None) or frappe.session.user


@contextmanager
def as_system():
	if frappe.session.user == SYSTEM_USER or getattr(frappe.local, "tijao_real_user", None):
		yield real_user()
		return
	session = frappe.local.session
	saved = (
		session.user,
		getattr(frappe.local, "user_perms", None),
		getattr(frappe.local, "role_permissions", None),
	)
	frappe.local.tijao_real_user = session.user
	session.user = SYSTEM_USER
	frappe.local.user_perms = None
	frappe.local.role_permissions = {}
	try:
		yield frappe.local.tijao_real_user
	finally:
		session.user, frappe.local.user_perms, frappe.local.role_permissions = saved
		if frappe.local.role_permissions is None:
			frappe.local.role_permissions = {}
		frappe.local.tijao_real_user = None


def writes_as_system(fn):
	@functools.wraps(fn)
	def wrapper(*args, **kwargs):
		with as_system():
			return fn(*args, **kwargs)

	return wrapper


def stamp_owner(doc) -> None:
	"""Record the real shop user as owner/modified_by of a document we just wrote."""
	user = real_user()
	if user == doc.owner:
		return
	frappe.db.set_value(doc.doctype, doc.name, {"owner": user, "modified_by": user}, update_modified=False)
	doc.owner = doc.modified_by = user

"""Single source of configuration: the ``Tijao POS Settings`` single DocType.

Nothing else in the app hardcodes a company, warehouse, price list or UOM name.
"""

from __future__ import annotations

import frappe

from tijao_pos.utils.errors import TijaoError

SETTINGS_DOCTYPE = "Tijao POS Settings"

REQUIRED = (
	"company",
	"warehouse",
	"pos_profile",
	"walk_in_customer",
	"default_supplier",
	"selling_price_list",
	"buying_price_list",
	"item_group",
	"cash_mode_of_payment",
	"mpesa_mode_of_payment",
	"opening_stock_account",
)


def get_settings(validate: bool = True) -> frappe._dict:
	"""Settings as a plain dict. Raises a friendly error if setup is incomplete."""
	doc = frappe.get_cached_doc(SETTINGS_DOCTYPE)
	values = frappe._dict({f.fieldname: doc.get(f.fieldname) for f in doc.meta.fields})
	if validate:
		missing = [f for f in REQUIRED if not values.get(f)]
		if missing:
			raise TijaoError(
				"The shop is not set up yet. Ask the owner to finish Tijao POS Settings "
				f"(missing: {', '.join(m.replace('_', ' ') for m in missing)})."
			)
	values.currency = (
		frappe.get_cached_value("Company", values.company, "default_currency") if values.company else None
	)
	return values


def mode_of_payment(settings: frappe._dict, payment_mode: str) -> str:
	"""'cash' / 'mpesa' (what the UI sends) -> Mode of Payment name from settings."""
	key = (payment_mode or "").strip().lower().replace("-", "")
	if key == "cash":
		return settings.cash_mode_of_payment
	if key == "mpesa":
		return settings.mpesa_mode_of_payment
	raise TijaoError("Choose Cash or M-Pesa.")


def payment_mode_key(settings: frappe._dict, mode_of_payment_name: str | None) -> str | None:
	"""Mode of Payment name -> 'cash' / 'mpesa' / None."""
	if mode_of_payment_name == settings.cash_mode_of_payment:
		return "cash"
	if mode_of_payment_name == settings.mpesa_mode_of_payment:
		return "mpesa"
	return None

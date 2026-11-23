# Copyright (c) 2026, Tijao and contributors
# For license information, please see license.txt

from frappe.model.document import Document


class TijaoShift(Document):
	"""One cash-drawer shift. Opened/closed only through tijao_pos.api.shift
	(see services/shift_service.py); totals are frozen when the shift closes."""

# Copyright (c) 2026, Tijao and contributors
# For license information, please see license.txt

from frappe.model.document import Document


class TijaoPOSSettings(Document):
	"""Single config DocType. Every company/warehouse/price-list/UOM name the app
	uses is read from here (see tijao_pos.utils.settings)."""

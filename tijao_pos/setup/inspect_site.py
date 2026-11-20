"""Dev helper: print the POS setup state (bench execute tijao_pos.setup.inspect_site.run)."""

import json

import frappe


def run():
	s = frappe.get_single("Tijao POS Settings").as_dict()
	out = {
		k: s.get(k)
		for k in s
		if not k.startswith("_")
		and k not in ("doctype", "name", "owner", "modified_by", "creation", "modified", "idx", "docstatus")
	}
	out["roles"] = frappe.get_all(
		"Role", filters={"name": ["like", "Tijao%"]}, fields=["name", "desk_access"]
	)
	out["custom_field"] = frappe.db.get_value(
		"Custom Field", "Sales Invoice-tijao_client_ref", ["fieldname", "unique"], as_dict=True
	)
	out["mop_accounts"] = frappe.get_all(
		"Mode of Payment Account", fields=["parent", "company", "default_account"]
	)
	out["pos_profile_payments"] = frappe.get_all(
		"POS Payment Method", filters={"parent": s.get("pos_profile")}, fields=["mode_of_payment", "default"]
	)
	out["unique_index"] = frappe.db.sql(
		"show index from `tabSales Invoice` where Column_name='tijao_client_ref'", as_dict=True
	)
	print(json.dumps(out, indent=1, default=str))

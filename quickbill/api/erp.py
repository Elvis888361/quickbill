import frappe


@frappe.whitelist()
def get_erps():
	"""Get list of connected ERP instances.

	Returns the current ERPNext site as the ERP entry.

	Returns:
		list of ERPs matching the erps schema
	"""
	site_name = frappe.local.site
	site_url = frappe.utils.get_url()

	return [
		{
			"id": 1,
			"name": site_name,
			"url": site_url,
			"selected": True,
		}
	]

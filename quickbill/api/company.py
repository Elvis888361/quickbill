import frappe


@frappe.whitelist()
def get_companies():
	"""Get list of companies the current user has access to.

	Returns:
		list of companies matching the Company schema
	"""
	user = frappe.session.user

	# Get companies user has permission for
	allowed = frappe.get_all(
		"User Permission",
		filters={"user": user, "allow": "Company"},
		pluck="for_value",
	)

	if allowed:
		companies = frappe.get_all(
			"Company",
			filters={"name": ["in", allowed]},
			fields=["name", "company_name"],
			order_by="name asc",
		)
	else:
		companies = frappe.get_all(
			"Company",
			fields=["name", "company_name"],
			order_by="name asc",
		)

	default_company = frappe.defaults.get_user_default("Company") or (
		companies[0].name if companies else None
	)

	result = []
	for company in companies:
		address = _get_company_address(company.name)
		result.append(
			{
				"name": company.name,
				"selected": company.name == default_company,
				"address": address,
			}
		)

	return result


def _get_company_address(company_name):
	"""Get the primary address for a company."""
	address_name = frappe.db.get_value(
		"Dynamic Link",
		{"link_doctype": "Company", "link_name": company_name, "parenttype": "Address"},
		"parent",
	)

	if not address_name:
		return ""

	address = frappe.get_doc("Address", address_name)
	parts = [
		address.address_line1 or "",
		address.address_line2 or "",
		address.city or "",
		address.state or "",
		address.country or "",
	]
	return ", ".join(p for p in parts if p)

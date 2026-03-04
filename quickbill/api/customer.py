import frappe


@frappe.whitelist()
def get_customers(company=None, search=None, limit_page_length=20, limit_start=0):
	"""Get list of customers with balance info.

	Args:
		company: Filter by company
		search: Search term for customer name, ID, phone, or email
		limit_page_length: Number of records per page (default 20)
		limit_start: Offset for pagination (default 0)

	Returns:
		list of customers matching the Customer schema
	"""
	filters = {"disabled": 0}

	or_filters = {}
	if search:
		or_filters = {
			"customer_name": ["like", f"%{search}%"],
			"name": ["like", f"%{search}%"],
		}

	customers = frappe.get_all(
		"Customer",
		filters=filters,
		or_filters=or_filters if or_filters else None,
		fields=["name", "customer_name", "customer_group"],
		limit_page_length=int(limit_page_length),
		limit_start=int(limit_start),
		order_by="customer_name asc",
	)

	result = []
	for customer in customers:
		contact_info = _get_contact_info(customer.name)
		advance_balance = _get_advance_balance(customer.name, company)
		due_balance = _get_due_balance(customer.name, company)

		result.append(
			{
				"name": customer.customer_name,
				"id": customer.name,
				"advance_balance": advance_balance,
				"due_balance": due_balance,
				"customer_group": customer.customer_group or "",
				"phone_number": contact_info.get("phone", ""),
				"email": contact_info.get("email", ""),
			}
		)

	return result


def _get_contact_info(customer_name):
	"""Get primary contact phone and email for a customer."""
	contact = frappe.db.sql(
		"""
		SELECT c.email_id, c.phone, c.mobile_no
		FROM `tabContact` c
		JOIN `tabDynamic Link` dl ON dl.parent = c.name
		WHERE dl.link_doctype = 'Customer'
			AND dl.link_name = %s
			AND c.is_primary_contact = 1
		LIMIT 1
		""",
		customer_name,
		as_dict=True,
	)

	if contact:
		return {
			"phone": contact[0].mobile_no or contact[0].phone or "",
			"email": contact[0].email_id or "",
		}

	return {"phone": "", "email": ""}


def _get_advance_balance(customer_name, company=None):
	"""Get unallocated advance payment balance for a customer."""
	conditions = "party_type = 'Customer' AND party = %s AND docstatus = 1 AND unallocated_amount > 0"
	values = [customer_name]
	if company:
		conditions += " AND company = %s"
		values.append(company)

	total = frappe.db.sql(
		f"SELECT COALESCE(SUM(unallocated_amount), 0) FROM `tabPayment Entry` WHERE {conditions}",
		values,
	)
	return float(total[0][0]) if total else 0.0


def _get_due_balance(customer_name, company=None):
	"""Get total outstanding balance from unpaid Sales Invoices."""
	conditions = "customer = %s AND docstatus = 1 AND outstanding_amount > 0"
	values = [customer_name]
	if company:
		conditions += " AND company = %s"
		values.append(company)

	total = frappe.db.sql(
		f"SELECT COALESCE(SUM(outstanding_amount), 0) FROM `tabSales Invoice` WHERE {conditions}",
		values,
	)
	return float(total[0][0]) if total else 0.0

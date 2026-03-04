import frappe
from frappe.utils import today, add_days


@frappe.whitelist()
def get_invoices(company=None, customer=None, sales_person=None, status=None, limit_page_length=20, limit_start=0):
	"""Get list of sales invoices.

	Args:
		company: Filter by company
		customer: Filter by customer
		sales_person: Filter by sales person
		status: Filter by status (Draft, Submitted, Paid, Unpaid, Overdue, Cancelled)
		limit_page_length: Number of records per page (default 20)
		limit_start: Offset for pagination (default 0)

	Returns:
		list of invoices matching the sales_invoice schema
	"""
	filters = {}
	if company:
		filters["company"] = company
	if customer:
		filters["customer"] = customer
	if status:
		if status == "Unpaid":
			filters["docstatus"] = 1
			filters["outstanding_amount"] = [">", 0]
		elif status == "Paid":
			filters["docstatus"] = 1
			filters["outstanding_amount"] = 0
		elif status == "Overdue":
			filters["docstatus"] = 1
			filters["outstanding_amount"] = [">", 0]
			filters["due_date"] = ["<", today()]
		elif status == "Draft":
			filters["docstatus"] = 0
		elif status == "Cancelled":
			filters["docstatus"] = 2
		else:
			filters["docstatus"] = 1
	else:
		filters["docstatus"] = ["!=", 2]

	if sales_person:
		# Use SQL for sales team filter
		return _get_invoices_by_sales_person(sales_person, filters, int(limit_page_length), int(limit_start))

	invoices = frappe.get_all(
		"Sales Invoice",
		filters=filters,
		fields=[
			"name", "customer", "customer_name", "company", "posting_date", "due_date",
			"grand_total", "outstanding_amount", "status",
		],
		limit_page_length=int(limit_page_length),
		limit_start=int(limit_start),
		order_by="posting_date desc",
	)

	return [_format_invoice(inv) for inv in invoices]


def _get_invoices_by_sales_person(sales_person, filters, limit, offset):
	"""Get invoices filtered by sales person via Sales Team child table."""
	conditions = []
	values = [sales_person]

	for field, value in filters.items():
		if isinstance(value, list):
			conditions.append(f"si.{field} {value[0]} %s")
			values.append(value[1])
		else:
			conditions.append(f"si.{field} = %s")
			values.append(value)

	where_clause = " AND ".join(conditions) if conditions else "1=1"

	invoices = frappe.db.sql(
		f"""
		SELECT DISTINCT si.name, si.customer, si.customer_name, si.company, si.posting_date,
			si.due_date, si.grand_total, si.outstanding_amount, si.status
		FROM `tabSales Invoice` si
		JOIN `tabSales Team` st ON st.parent = si.name AND st.parenttype = 'Sales Invoice'
		WHERE st.sales_person = %s AND {where_clause}
		ORDER BY si.posting_date DESC
		LIMIT %s OFFSET %s
		""",
		values + [limit, offset],
		as_dict=True,
	)

	return [_format_invoice(inv) for inv in invoices]


def _format_invoice(inv):
	"""Format a Sales Invoice record to match the schema."""
	# Get sales person from Sales Team
	sales_person = frappe.db.get_value(
		"Sales Team",
		{"parent": inv.name, "parenttype": "Sales Invoice"},
		"sales_person",
	) or ""

	# Get items
	items = _get_invoice_items(inv.name)

	# Get payment methods used
	payments = _get_invoice_payments(inv.name)

	total_paid = float(inv.grand_total or 0) - float(inv.outstanding_amount or 0)

	return {
		"customer": inv.customer_name or inv.customer,
		"company": inv.get("company") or "",
		"items": items,
		"sales_person": sales_person,
		"payments": payments,
		"date": str(inv.posting_date) if inv.posting_date else "",
		"due_date": str(inv.due_date) if inv.due_date else "",
		"local_id": 0,
		"name_in_erp": inv.name,
		"sync_status": "synced",
		"total_paid": total_paid,
		"invoice_total": float(inv.grand_total or 0),
		"outstanding": float(inv.outstanding_amount or 0),
	}


def _get_invoice_items(invoice_name):
	"""Get items for a sales invoice."""
	items = frappe.get_all(
		"Sales Invoice Item",
		filters={"parent": invoice_name},
		fields=["item_name", "item_code", "qty", "uom", "rate", "amount"],
		order_by="idx asc",
	)

	return [
		{
			"invoice_local_id": 0,
			"item_name": item.item_name or "",
			"item_code": item.item_code or "",
			"qty": float(item.qty or 0),
			"uom": item.uom or "",
			"rate": float(item.rate or 0),
			"amount": float(item.amount or 0),
		}
		for item in items
	]


def _get_invoice_payments(invoice_name):
	"""Get payment modes used in a sales invoice (from Sales Invoice Payment child table)."""
	payments = frappe.get_all(
		"Sales Invoice Payment",
		filters={"parent": invoice_name},
		fields=["mode_of_payment", "amount"],
	)

	if payments:
		return [{"name": p.mode_of_payment, "default": False} for p in payments]

	# Check Payment Entry references
	pe_modes = frappe.db.sql(
		"""
		SELECT DISTINCT pe.mode_of_payment
		FROM `tabPayment Entry` pe
		JOIN `tabPayment Entry Reference` per ON per.parent = pe.name
		WHERE per.reference_doctype = 'Sales Invoice'
			AND per.reference_name = %s
			AND pe.docstatus = 1
		""",
		invoice_name,
		as_dict=True,
	)

	return [{"name": p.mode_of_payment, "default": False} for p in pe_modes]


@frappe.whitelist()
def create_invoice(data):
	"""Create a new Sales Invoice.

	Args:
		data: dict or JSON string with invoice data matching the sales_invoice schema:
			- customer (required): customer name or ID
			- items (required): list of invoice items
			- sales_person: sales person name
			- payments: list of payment methods with amounts
			- date: posting date (default: today)
			- due_date: payment due date (default: date + 30 days)
			- local_id: client-side ID for sync tracking
			- company: company name

	Returns:
		dict with created invoice details
	"""
	if isinstance(data, str):
		data = frappe.parse_json(data)

	_validate_invoice_data(data)

	posting_date = data.get("date") or today()
	due_date = data.get("due_date") or add_days(posting_date, 30)

	# Resolve customer - accept either customer_name or customer ID
	customer = _resolve_customer(data["customer"])

	invoice = frappe.new_doc("Sales Invoice")
	invoice.customer = customer
	invoice.posting_date = posting_date
	invoice.due_date = due_date
	invoice.set_posting_time = 1

	if data.get("company"):
		invoice.company = data["company"]

	# Set price list
	selling_price_list = frappe.db.get_single_value("Selling Settings", "selling_price_list")
	if not selling_price_list:
		selling_price_list = frappe.get_all(
			"Price List", filters={"selling": 1, "enabled": 1}, pluck="name", limit=1
		)
		selling_price_list = selling_price_list[0] if selling_price_list else None

	if selling_price_list:
		invoice.selling_price_list = selling_price_list

	# Add items
	for item_data in data["items"]:
		invoice.append(
			"items",
			{
				"item_code": item_data.get("item_code"),
				"item_name": item_data.get("item_name"),
				"qty": float(item_data.get("qty", 1)),
				"uom": item_data.get("uom"),
				"rate": float(item_data.get("rate", 0)),
			},
		)

	# Add sales person
	if data.get("sales_person"):
		invoice.append(
			"sales_team",
			{
				"sales_person": data["sales_person"],
				"allocated_percentage": 100,
			},
		)

	# Add payments (for POS-style invoices)
	if data.get("payments"):
		invoice.is_pos = 1
		for payment in data["payments"]:
			if isinstance(payment, dict) and payment.get("name"):
				invoice.append(
					"payments",
					{
						"mode_of_payment": payment["name"],
						"amount": float(payment.get("amount", 0)),
					},
				)

	invoice.insert()
	invoice.submit()

	return {
		"customer": invoice.customer_name or invoice.customer,
		"company": invoice.company or "",
		"items": _get_invoice_items(invoice.name),
		"sales_person": data.get("sales_person", ""),
		"payments": [{"name": p.mode_of_payment, "default": False} for p in invoice.payments] if invoice.payments else [],
		"date": str(invoice.posting_date),
		"due_date": str(invoice.due_date),
		"local_id": data.get("local_id", 0),
		"name_in_erp": invoice.name,
		"sync_status": "synced",
		"total_paid": float(invoice.paid_amount or 0),
		"invoice_total": float(invoice.grand_total or 0),
		"outstanding": float(invoice.outstanding_amount or 0),
	}


def _validate_invoice_data(data):
	"""Validate required fields for invoice creation."""
	if not data.get("customer"):
		frappe.throw("Customer is required")

	if not data.get("items") or not isinstance(data["items"], list) or len(data["items"]) == 0:
		frappe.throw("At least one item is required")

	for idx, item in enumerate(data["items"], 1):
		if not item.get("item_code") and not item.get("item_name"):
			frappe.throw(f"Item code or item name is required for item row {idx}")
		if float(item.get("qty", 0)) <= 0:
			frappe.throw(f"Quantity must be greater than 0 for item row {idx}")


def _resolve_customer(customer_identifier):
	"""Resolve customer name/ID to a valid Customer doctype name."""
	# Check if it's a direct Customer ID
	if frappe.db.exists("Customer", customer_identifier):
		return customer_identifier

	# Try to find by customer_name
	customer = frappe.db.get_value("Customer", {"customer_name": customer_identifier}, "name")
	if customer:
		return customer

	frappe.throw(f"Customer '{customer_identifier}' not found")

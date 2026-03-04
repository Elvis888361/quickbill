import frappe
from frappe.utils import today


@frappe.whitelist()
def get_payments(company=None, customer=None, status=None, limit_page_length=20, limit_start=0):
	"""Get list of payment entries.

	Args:
		company: Filter by company
		customer: Filter by customer
		status: Filter by status (Draft, Submitted, Cancelled)
		limit_page_length: Number of records per page (default 20)
		limit_start: Offset for pagination (default 0)

	Returns:
		list of payments
	"""
	filters = {"payment_type": "Receive", "party_type": "Customer"}

	if company:
		filters["company"] = company
	if customer:
		filters["party"] = customer

	if status:
		if status == "Draft":
			filters["docstatus"] = 0
		elif status == "Cancelled":
			filters["docstatus"] = 2
		else:
			filters["docstatus"] = 1
	else:
		filters["docstatus"] = ["!=", 2]

	payments = frappe.get_all(
		"Payment Entry",
		filters=filters,
		fields=[
			"name", "party", "party_name", "posting_date", "paid_amount",
			"mode_of_payment", "reference_no", "reference_date", "company",
			"unallocated_amount", "status",
		],
		limit_page_length=int(limit_page_length),
		limit_start=int(limit_start),
		order_by="posting_date desc",
	)

	result = []
	for payment in payments:
		references = _get_payment_references(payment.name)
		result.append(
			{
				"name_in_erp": payment.name,
				"customer": payment.party_name or payment.party,
				"customer_id": payment.party,
				"date": str(payment.posting_date) if payment.posting_date else "",
				"amount": float(payment.paid_amount or 0),
				"mode_of_payment": payment.mode_of_payment or "",
				"reference_no": payment.reference_no or "",
				"reference_date": str(payment.reference_date) if payment.reference_date else "",
				"company": payment.company or "",
				"unallocated_amount": float(payment.unallocated_amount or 0),
				"status": payment.status or "",
				"sync_status": "synced",
				"references": references,
			}
		)

	return result


def _get_payment_references(payment_name):
	"""Get invoice references for a payment entry."""
	refs = frappe.get_all(
		"Payment Entry Reference",
		filters={"parent": payment_name},
		fields=["reference_doctype", "reference_name", "allocated_amount", "outstanding_amount"],
		order_by="idx asc",
	)

	return [
		{
			"type": ref.reference_doctype,
			"name": ref.reference_name,
			"allocated_amount": float(ref.allocated_amount or 0),
			"outstanding_amount": float(ref.outstanding_amount or 0),
		}
		for ref in refs
	]


@frappe.whitelist()
def create_payment(data):
	"""Create a new Payment Entry (Receive from Customer).

	Args:
		data: dict or JSON string with payment data:
			- customer (required): customer name or ID
			- amount (required): payment amount
			- mode_of_payment (required): payment method name
			- date: posting date (default: today)
			- company: company name
			- reference_no: cheque/transaction reference number
			- reference_date: cheque/transaction date
			- references: list of invoice references to allocate against:
				- invoice: Sales Invoice name
				- allocated_amount: amount to allocate

	Returns:
		dict with created payment details
	"""
	if isinstance(data, str):
		data = frappe.parse_json(data)

	_validate_payment_data(data)

	customer = _resolve_customer(data["customer"])
	posting_date = data.get("date") or today()
	company = data.get("company") or frappe.defaults.get_user_default("Company")

	if not company:
		frappe.throw("Company is required")

	# Get company default accounts
	company_doc = frappe.get_doc("Company", company)
	default_currency = company_doc.default_currency

	# Get mode of payment account
	mop_account = _get_mode_of_payment_account(data["mode_of_payment"], company)

	payment = frappe.new_doc("Payment Entry")
	payment.payment_type = "Receive"
	payment.party_type = "Customer"
	payment.party = customer
	payment.posting_date = posting_date
	payment.company = company
	payment.mode_of_payment = data["mode_of_payment"]
	payment.paid_amount = float(data["amount"])
	payment.received_amount = float(data["amount"])
	payment.paid_to = mop_account
	payment.paid_to_account_currency = default_currency
	payment.paid_from = company_doc.default_receivable_account
	payment.paid_from_account_currency = default_currency

	if data.get("reference_no"):
		payment.reference_no = data["reference_no"]
	if data.get("reference_date"):
		payment.reference_date = data["reference_date"]

	# Allocate against invoices if references provided
	if data.get("references"):
		for ref in data["references"]:
			invoice_name = ref.get("invoice")
			if not invoice_name:
				continue

			outstanding = frappe.db.get_value("Sales Invoice", invoice_name, "outstanding_amount") or 0
			allocated = float(ref.get("allocated_amount", 0)) or min(float(data["amount"]), float(outstanding))

			payment.append(
				"references",
				{
					"reference_doctype": "Sales Invoice",
					"reference_name": invoice_name,
					"allocated_amount": allocated,
					"outstanding_amount": float(outstanding),
				},
			)

	payment.insert()
	payment.submit()

	references = _get_payment_references(payment.name)

	return {
		"name_in_erp": payment.name,
		"customer": payment.party_name or payment.party,
		"customer_id": payment.party,
		"date": str(payment.posting_date),
		"amount": float(payment.paid_amount or 0),
		"mode_of_payment": payment.mode_of_payment or "",
		"reference_no": payment.reference_no or "",
		"reference_date": str(payment.reference_date) if payment.reference_date else "",
		"company": payment.company or "",
		"unallocated_amount": float(payment.unallocated_amount or 0),
		"status": payment.status or "",
		"sync_status": "synced",
		"references": references,
	}


def _validate_payment_data(data):
	"""Validate required fields for payment creation."""
	if not data.get("customer"):
		frappe.throw("Customer is required")
	if not data.get("amount") or float(data.get("amount", 0)) <= 0:
		frappe.throw("Amount must be greater than 0")
	if not data.get("mode_of_payment"):
		frappe.throw("Mode of payment is required")


def _resolve_customer(customer_identifier):
	"""Resolve customer name/ID to a valid Customer doctype name."""
	if frappe.db.exists("Customer", customer_identifier):
		return customer_identifier

	customer = frappe.db.get_value("Customer", {"customer_name": customer_identifier}, "name")
	if customer:
		return customer

	frappe.throw(f"Customer '{customer_identifier}' not found")


def _get_mode_of_payment_account(mode_of_payment, company):
	"""Get the default account for a mode of payment in a company."""
	account = frappe.db.get_value(
		"Mode of Payment Account",
		{"parent": mode_of_payment, "company": company},
		"default_account",
	)

	if account:
		return account

	# Fallback to company's default bank account
	return frappe.db.get_value("Company", company, "default_bank_account")

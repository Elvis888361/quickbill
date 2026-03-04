import frappe
from frappe.utils import today


def _check_expense_claim_available():
	"""Check if Expense Claim doctype is available (requires HRMS)."""
	if not frappe.db.table_exists("Expense Claim"):
		frappe.throw("Expense Claim module is not available. Please install HRMS app.", frappe.DoesNotExistError)


@frappe.whitelist()
def get_expenses(company=None, employee=None, status=None, limit_page_length=20, limit_start=0):
	"""Get list of expense claims.

	Args:
		company: Filter by company
		employee: Filter by employee ID
		status: Filter by approval_status (Draft, Approved, Rejected, Cancelled)
		limit_page_length: Number of records per page (default 20)
		limit_start: Offset for pagination (default 0)

	Returns:
		list of expenses
	"""
	_check_expense_claim_available()

	filters = {}

	if company:
		filters["company"] = company

	if employee:
		filters["employee"] = employee
	else:
		# Default to current user's employee
		emp = frappe.db.get_value("Employee", {"user_id": frappe.session.user}, "name")
		if emp:
			filters["employee"] = emp

	if status:
		if status == "Draft":
			filters["docstatus"] = 0
		elif status == "Cancelled":
			filters["docstatus"] = 2
		elif status in ("Approved", "Rejected"):
			filters["docstatus"] = 1
			filters["approval_status"] = status
		else:
			filters["docstatus"] = 1
	else:
		filters["docstatus"] = ["!=", 2]

	expenses = frappe.get_all(
		"Expense Claim",
		filters=filters,
		fields=[
			"name", "employee", "employee_name", "posting_date", "company",
			"total_claimed_amount", "total_sanctioned_amount", "status",
			"approval_status",
		],
		limit_page_length=int(limit_page_length),
		limit_start=int(limit_start),
		order_by="posting_date desc",
	)

	result = []
	for expense in expenses:
		items = _get_expense_items(expense.name)
		result.append(
			{
				"name_in_erp": expense.name,
				"employee": expense.employee,
				"employee_name": expense.employee_name or "",
				"date": str(expense.posting_date) if expense.posting_date else "",
				"company": expense.company or "",
				"total_amount": float(expense.total_claimed_amount or 0),
				"sanctioned_amount": float(expense.total_sanctioned_amount or 0),
				"status": expense.approval_status or expense.status or "",
				"sync_status": "synced",
				"items": items,
			}
		)

	return result


def _get_expense_items(expense_name):
	"""Get line items for an expense claim."""
	items = frappe.get_all(
		"Expense Claim Detail",
		filters={"parent": expense_name},
		fields=["expense_type", "description", "amount", "sanctioned_amount"],
		order_by="idx asc",
	)

	return [
		{
			"category": item.expense_type or "",
			"description": item.description or "",
			"amount": float(item.amount or 0),
			"sanctioned_amount": float(item.sanctioned_amount or 0),
		}
		for item in items
	]


@frappe.whitelist()
def create_expense(data):
	"""Create a new Expense Claim.

	Args:
		data: dict or JSON string with expense data:
			- items (required): list of expense items, each with:
				- category (required): expense type/category name
				- amount (required): claimed amount
				- description: description of the expense
			- date: posting date (default: today)
			- company: company name
			- employee: employee ID (default: current user's employee)

	Returns:
		dict with created expense details
	"""
	_check_expense_claim_available()

	if isinstance(data, str):
		data = frappe.parse_json(data)

	_validate_expense_data(data)

	employee = data.get("employee")
	if not employee:
		employee = frappe.db.get_value("Employee", {"user_id": frappe.session.user}, "name")
		if not employee:
			frappe.throw("No employee record found for the current user")

	expense = frappe.new_doc("Expense Claim")
	expense.employee = employee
	expense.posting_date = data.get("date") or today()

	if data.get("company"):
		expense.company = data["company"]
	else:
		expense.company = frappe.db.get_value("Employee", employee, "company")

	expense.expense_approver = _get_expense_approver(employee)

	for item_data in data["items"]:
		expense.append(
			"expenses",
			{
				"expense_type": item_data["category"],
				"amount": float(item_data["amount"]),
				"description": item_data.get("description", ""),
				"sanctioned_amount": float(item_data["amount"]),
			},
		)

	expense.insert()
	expense.submit()

	items = _get_expense_items(expense.name)

	return {
		"name_in_erp": expense.name,
		"employee": expense.employee,
		"employee_name": expense.employee_name or "",
		"date": str(expense.posting_date),
		"company": expense.company or "",
		"total_amount": float(expense.total_claimed_amount or 0),
		"sanctioned_amount": float(expense.total_sanctioned_amount or 0),
		"status": expense.approval_status or "",
		"sync_status": "synced",
		"items": items,
	}


def _validate_expense_data(data):
	"""Validate required fields for expense creation."""
	if not data.get("items") or not isinstance(data["items"], list) or len(data["items"]) == 0:
		frappe.throw("At least one expense item is required")

	for idx, item in enumerate(data["items"], 1):
		if not item.get("category"):
			frappe.throw(f"Expense category is required for item row {idx}")
		if float(item.get("amount", 0)) <= 0:
			frappe.throw(f"Amount must be greater than 0 for item row {idx}")


def _get_expense_approver(employee):
	"""Get the expense approver for an employee."""
	approver = frappe.db.get_value("Employee", employee, "expense_approver")
	if approver:
		return approver

	department = frappe.db.get_value("Employee", employee, "department")
	if department:
		approver = frappe.db.get_value("Department Approver", {"parent": department, "parentfield": "expense_approvers"}, "approver")
		if approver:
			return approver

	return None

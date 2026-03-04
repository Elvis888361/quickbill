import frappe
from frappe.auth import LoginManager
from frappe import _

@frappe.whitelist(allow_guest=True)
def get_login_details(username_or_email=None, password=None, company=None):
	"""Authenticate then return login details payload (same schema as before)."""

	if not username_or_email or not password:
		frappe.throw(_("username_or_email and password are required"), frappe.MandatoryError)

	# Keep current session user so we can restore it after auth attempt
	original_user = frappe.session.user

	try:
		# Authenticate
		lm = LoginManager()
		lm.authenticate(user=username_or_email, pwd=password)
		lm.post_login()  # sets up session, user, etc.

		# Ensure our code uses the authenticated user
		auth_user = frappe.session.user
		frappe.set_user(auth_user)

		# Token (create if missing)
		user_token = _get_or_create_user_api_token(auth_user)

		# ---- your original logic (slightly refactored to use auth_user) ----
		user_doc = frappe.get_doc("User", auth_user)

		employee_id = ""
		sales_person = ""
		total_commission_balance = 0.0

		employee = frappe.db.get_value(
			"Employee",
			{"user_id": auth_user},
			["name", "employee_name"],
			as_dict=True,
		)
		if employee:
			employee_id = employee.name
			sales_person_name = frappe.db.get_value("Sales Person", {"employee": employee.name}, "name")
			if sales_person_name:
				sales_person = sales_person_name
				total_commission_balance = _get_commission_balance(sales_person_name, company)

		companies = _get_user_companies(auth_user)
		target_company = company or (companies[0] if companies else None)

		payment_methods = _get_payment_methods(target_company)
		expense_categories = _get_expense_categories()
		default_currency = _get_default_currency(target_company)

		total_sales = _get_total_sales(auth_user, sales_person, target_company)
		total_outstanding = _get_total_outstanding(auth_user, sales_person, target_company)
		total_expenses = _get_total_expenses(employee_id, target_company)
		total_paid = _get_total_paid(auth_user, sales_person, target_company)

		return {
			"ok": True,
			"message": "Login successful",
			"user_token": user_token,
			"data": {
				"email": auth_user,
				"full_name": user_doc.full_name or "",
				"companies": companies,
				"payment_methods": payment_methods,
				"default_currency": default_currency,
				"total_sales": total_sales,
				"total_outstanding": total_outstanding,
				"total_expenses": total_expenses,
				"employee_id": employee_id,
				"total_commission_balance": total_commission_balance,
				"total_paid": total_paid,
				"sales_person": sales_person,
				"expense_categories": expense_categories,
			},
		}

	except frappe.AuthenticationError:
		# Don’t leak which part failed; keep it generic
		frappe.local.response["http_status_code"] = 401
		return {"ok": False, "message": "Invalid username/email or password"}

	finally:
		# Restore original user context (important if called while already logged in as someone else)
		try:
			frappe.set_user(original_user)
		except Exception:
			pass

def _get_or_create_user_api_token(user: str) -> str:
	"""
	Returns token string in format: token:apikey:secret
	Creates api_key/api_secret if missing.
	"""
	api_key, api_secret = frappe.db.get_value("User", user, ["api_key", "api_secret"]) or (None, None)

	if not api_key or not api_secret:
		# Generate and save
		user_doc = frappe.get_doc("User", user)

		if not user_doc.api_key:
			user_doc.api_key = frappe.generate_hash(length=15)

		if not user_doc.api_secret:
			# api_secret is stored encrypted; use set_password
			user_doc.set_password("api_secret", frappe.generate_hash(length=32))

		user_doc.save(ignore_permissions=True)
		frappe.db.commit()

		api_key, api_secret = frappe.db.get_value("User", user, ["api_key", "api_secret"])

	return f"token:{api_key}:{api_secret}"


def _get_user_companies(user):
	"""Get list of companies the user has access to."""
	allowed = frappe.get_all(
		"User Permission",
		filters={"user": user, "allow": "Company"},
		pluck="for_value",
	)

	if allowed:
		return allowed

	# If no user permission restrictions, return all companies
	return frappe.get_all("Company", pluck="name", order_by="name asc")


def _get_payment_methods(company=None):
	"""Get available payment methods (Mode of Payment)."""
	modes = frappe.get_all("Mode of Payment", filters={"enabled": 1}, fields=["name"], order_by="name asc")

	default_mode = None
	if company:
		try:
			default_mode = frappe.db.get_value("Company", company, "default_mode_of_payment")
		except Exception:
			default_mode = None

	return [{"name": m.name, "default": m.name == default_mode} for m in modes]


def _get_expense_categories():
	"""Get list of expense claim types."""
	if not frappe.db.table_exists("Expense Claim Type"):
		return []
	types = frappe.get_all("Expense Claim Type", pluck="name", order_by="name asc")
	return [{"name": t} for t in types]


def _get_default_currency(company=None):
	"""Get the default currency for the company or global default."""
	if company:
		currency = frappe.db.get_value("Company", company, "default_currency")
		if currency:
			return currency

	return frappe.defaults.get_global_default("currency") or "USD"


def _get_total_sales(user, sales_person=None, company=None):
	"""Get total sales invoice amount for the user/sales person."""
	if sales_person:
		total = frappe.db.sql(
			"""
			SELECT COALESCE(SUM(si.grand_total), 0)
			FROM `tabSales Invoice` si
			JOIN `tabSales Team` st ON st.parent = si.name AND st.parenttype = 'Sales Invoice'
			WHERE si.docstatus = 1
				AND st.sales_person = %s
				{company_filter}
			""".format(company_filter="AND si.company = %s" if company else ""),
			(sales_person, company) if company else (sales_person,),
		)
		return float(total[0][0]) if total else 0.0

	# Fallback: invoices owned by user
	conditions = "docstatus = 1 AND owner = %s"
	values = [user]
	if company:
		conditions += " AND company = %s"
		values.append(company)
	total = frappe.db.sql(
		f"SELECT COALESCE(SUM(grand_total), 0) FROM `tabSales Invoice` WHERE {conditions}",
		values,
	)
	return float(total[0][0]) if total else 0.0


def _get_total_outstanding(user, sales_person=None, company=None):
	"""Get total outstanding amount across invoices."""
	if sales_person:
		total = frappe.db.sql(
			"""
			SELECT COALESCE(SUM(si.outstanding_amount), 0)
			FROM `tabSales Invoice` si
			JOIN `tabSales Team` st ON st.parent = si.name AND st.parenttype = 'Sales Invoice'
			WHERE si.docstatus = 1
				AND st.sales_person = %s
				AND si.outstanding_amount > 0
				{company_filter}
			""".format(company_filter="AND si.company = %s" if company else ""),
			(sales_person, company) if company else (sales_person,),
		)
		return float(total[0][0]) if total else 0.0

	conditions = "docstatus = 1 AND owner = %s AND outstanding_amount > 0"
	values = [user]
	if company:
		conditions += " AND company = %s"
		values.append(company)
	total = frappe.db.sql(
		f"SELECT COALESCE(SUM(outstanding_amount), 0) FROM `tabSales Invoice` WHERE {conditions}",
		values,
	)
	return float(total[0][0]) if total else 0.0


def _get_total_expenses(employee_id, company=None):
	"""Get total approved expense claims for the employee."""
	if not employee_id:
		return 0.0

	conditions = "employee = %s AND docstatus = 1"
	values = [employee_id]
	if company:
		conditions += " AND company = %s"
		values.append(company)

	if not frappe.db.table_exists("Expense Claim"):
		return 0.0

	total = frappe.db.sql(
		f"SELECT COALESCE(SUM(total_claimed_amount), 0) FROM `tabExpense Claim` WHERE {conditions}",
		values,
	)
	return float(total[0][0]) if total else 0.0


def _get_total_paid(user, sales_person=None, company=None):
	"""Get total payments received."""
	if sales_person:
		total = frappe.db.sql(
			"""
			SELECT COALESCE(SUM(si.grand_total - si.outstanding_amount), 0)
			FROM `tabSales Invoice` si
			JOIN `tabSales Team` st ON st.parent = si.name AND st.parenttype = 'Sales Invoice'
			WHERE si.docstatus = 1
				AND st.sales_person = %s
				{company_filter}
			""".format(company_filter="AND si.company = %s" if company else ""),
			(sales_person, company) if company else (sales_person,),
		)
		return float(total[0][0]) if total else 0.0

	conditions = "docstatus = 1 AND owner = %s"
	values = [user]
	if company:
		conditions += " AND company = %s"
		values.append(company)
	grand = frappe.db.sql(
		f"SELECT COALESCE(SUM(grand_total), 0) FROM `tabSales Invoice` WHERE {conditions}",
		values,
	)
	outstanding = frappe.db.sql(
		f"SELECT COALESCE(SUM(outstanding_amount), 0) FROM `tabSales Invoice` WHERE {conditions}",
		values,
	)
	return float(grand[0][0]) - float(outstanding[0][0])


def _get_commission_balance(sales_person, company=None):
	"""Get total commission balance for a sales person."""
	query = """
		SELECT COALESCE(SUM(st.incentives), 0)
		FROM `tabSales Team` st
		JOIN `tabSales Invoice` si ON st.parent = si.name AND st.parenttype = 'Sales Invoice'
		WHERE si.docstatus = 1
			AND st.sales_person = %s
			{company_filter}
	""".format(company_filter="AND si.company = %s" if company else "")

	total = frappe.db.sql(
		query,
		(sales_person, company) if company else (sales_person,),
	)
	return float(total[0][0]) if total else 0.0

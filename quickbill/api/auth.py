import frappe
from frappe import _


@frappe.whitelist(allow_guest=True, methods=["POST"])
def forgot_password(email):
	"""Send a password reset email to the user.

	Args:
		email (required): The user's email address

	Returns:
		dict with status and message
	"""
	if not email:
		frappe.throw(_("Email is required"))

	if not frappe.db.exists("User", email):
		# Return generic message to avoid user enumeration
		return {
			"status": "ok",
			"message": "If the email is registered, a password reset link has been sent.",
		}

	user = frappe.get_doc("User", email)

	if user.name == "Administrator":
		return {
			"status": "error",
			"message": "Password reset is not allowed for this account.",
		}

	if not user.enabled:
		return {
			"status": "error",
			"message": "This account is disabled. Please contact your administrator.",
		}

	try:
		user.validate_reset_password()
		user.reset_password(send_email=True)
		return {
			"status": "ok",
			"message": "Password reset instructions have been sent to your email.",
		}
	except frappe.ValidationError as e:
		return {
			"status": "error",
			"message": str(e),
		}

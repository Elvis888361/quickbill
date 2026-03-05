import frappe


@frappe.whitelist()
def get_items(company=None, search=None, limit_page_length=20, limit_start=0):
	"""Get list of items with stock and pricing info.

	Args:
		company: Filter by company (for stock/price context)
		search: Search term to filter items by name or code
		limit_page_length: Number of records per page (default 20)
		limit_start: Offset for pagination (default 0)

	Returns:
		list of items matching the Item schema
	"""
	filters = {"disabled": 0, "is_sales_item": 1}

	or_filters = {}
	if search:
		or_filters = {
			"item_name": ["like", f"%{search}%"],
			"item_code": ["like", f"%{search}%"],
		}

	items = frappe.get_all(
		"Item",
		filters=filters,
		or_filters=or_filters if or_filters else None,
		fields=["item_name", "item_code", "stock_uom"],
		limit_page_length=int(limit_page_length),
		limit_start=int(limit_start),
		order_by="item_name asc",
	)

	result = []
	for item in items:
		selling_price = _get_selling_price(item.item_code, company)
		current_stock = _get_current_stock(item.item_code, company)

		result.append(
			{
				"name": item.item_name,
				"code": item.item_code,
				"current_stock": current_stock,
				"uom": item.stock_uom,
				"selling_price": selling_price,
			}
		)

	return result


def _get_selling_price(item_code, company=None):
	"""Get the selling price for an item from Item Price."""
	filters = {
		"item_code": item_code,
		"selling": 1,
	}
#	if company:
#		#filters["company"] = company

	price = frappe.get_all(
		"Item Price",
		filters=filters,
		fields=["price_list_rate"],
		order_by="creation desc",
		limit=1,
	)

	if price:
		return str(price[0].price_list_rate)

	# Fallback to standard_rate on Item
	standard_rate = frappe.db.get_value("Item", item_code, "standard_rate")
	return str(standard_rate or 0)


def _get_current_stock(item_code, company=None):
	"""Get current stock for an item from Bin."""
	conditions = "item_code = %s"
	values = [item_code]

	if company:
		warehouses = frappe.get_all("Warehouse", filters={"company": company}, pluck="name")
		if warehouses:
			placeholders = ", ".join(["%s"] * len(warehouses))
			conditions += f" AND warehouse IN ({placeholders})"
			values.extend(warehouses)

	total = frappe.db.sql(
		f"SELECT COALESCE(SUM(actual_qty), 0) FROM `tabBin` WHERE {conditions}",
		values,
	)
	return float(total[0][0]) if total else 0.0

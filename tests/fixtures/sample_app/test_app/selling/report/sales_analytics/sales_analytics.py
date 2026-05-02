"""Sales Analytics report logic."""
import frappe


def execute(filters=None):
    columns = [
        {"fieldname": "customer", "label": "Customer", "fieldtype": "Link", "options": "Customer"},
        {"fieldname": "total", "label": "Total", "fieldtype": "Currency"},
    ]
    data = frappe.db.sql(
        """SELECT customer, SUM(grand_total) as total
        FROM `tabSales Order`
        GROUP BY customer""",
        as_dict=True,
    )
    return columns, data

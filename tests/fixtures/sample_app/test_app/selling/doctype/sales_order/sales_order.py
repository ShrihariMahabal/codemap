"""Sales Order controller."""
import frappe
from frappe.model.document import Document


class SalesOrder(Document):
    def validate(self):
        self.validate_customer()

    def validate_customer(self):
        if not self.customer:
            frappe.throw("Customer is required")

    @frappe.whitelist()
    def on_submit(self):
        frappe.msgprint("Sales Order submitted")

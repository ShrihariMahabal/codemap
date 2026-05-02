"""Base selling controller shared across selling DocTypes."""
import frappe
from frappe.model.document import Document


class SellingController(Document):
    def validate_selling_rate(self):
        for item in self.items:
            if item.rate <= 0:
                frappe.throw(f"Rate must be positive for {item.item_code}")

// Copyright (c) 2024, Test and Contributors
// License: GNU General Public License v3. See license.txt

cur_frm.cscript.tax_table = "Sales Taxes and Charges";

frappe.ui.form.on("Sales Order", {
	setup: function(frm) {
		frm.set_query("customer", function() {
			return { filters: { is_frozen: 0 } };
		});
	},
	refresh(frm) {
		if (frm.doc.docstatus === 1) {
			frappe.call({
				method: "test_app.selling.doctype.sales_order.sales_order.get_stock",
				callback: function(r) {}
			});
		}
	},
	validate(frm) {
		validate_customer(frm);
	}
});

function validate_customer(frm) {
	if (!frm.doc.customer) {
		frappe.throw(__("Please select a customer"));
	}
}

/**
 * Module-level docstring for testing.
 */

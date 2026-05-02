// Sales Order client script
frappe.ui.form.on("Sales Order", {
    refresh: function(frm) {
        frm.add_custom_button(__("Make Invoice"), function() {
            frappe.call({
                method: "test_app.selling.doctype.sales_order.sales_order.make_invoice",
                args: { sales_order: frm.doc.name },
            });
        });
    }
});

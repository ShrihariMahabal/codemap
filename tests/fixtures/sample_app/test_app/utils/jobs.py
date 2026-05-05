"""Fixture: side-effect calls for extraction tests."""
import frappe


def schedule_things():
    frappe.enqueue("test_app.tasks.run_nightly", queue="long")
    frappe.enqueue(method="test_app.tasks.update_totals")
    frappe.enqueue_doc("Sales Order", "SO-0001", "on_submit")
    frappe.publish_realtime("order_created", message={"id": 1})
    frappe.sendmail(recipients=["a@b.com"], subject="hi", message="hey")

# Test app hooks
app_name = "test_app"
app_title = "Test App"

doc_events = {
    "Sales Order": {
        "on_submit": "test_app.selling.doctype.sales_order.sales_order.on_submit",
    }
}

scheduler_events = {
    "daily": [
        "test_app.tasks.daily_cleanup",
    ]
}

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

has_permission = {
    "Sales Order": "test_app.permissions.has_permission_for_sales_order",
}

permission_query_conditions = {
    "Sales Order": "test_app.permissions.sales_order_query",
}

jinja = {
    "methods": [
        "test_app.utils.format_currency",
        "money:test_app.utils.format_money",
    ],
    "filters": [
        "test_app.utils.titlecase",
    ],
}

app_include_js = [
    "test_app.bundle.js",
]

app_include_css = "test_app.bundle.css"

before_request = ["test_app.middleware.log_request"]
after_request = "test_app.middleware.cleanup"

boot_session = "test_app.boot.update_bootinfo"

notification_config = "test_app.notifications.get_notification_config"

regional_overrides = {
    "India": {
        "test_app.utils.calculate_taxes": "test_app.regional.india.calculate_taxes",
    }
}

fixtures = [
    "Custom Field",
    {"dt": "Property Setter", "filters": [["doc_type", "in", ["Sales Order"]]]},
]

auto_cancel_exempted_doctypes = ["Sales Invoice"]

override_doctype_dashboards = {
    "Sales Order": "test_app.dashboards.sales_order_dashboard",
}

"""Sales Order dashboard configuration."""


def get_data():
    return {
        "fieldname": "sales_order",
        "internal_links": {
            "Customer": ["customer"],
        },
        "transactions": [
            {
                "label": "Fulfillment",
                "items": ["Delivery Note", "Sales Invoice"],
            },
        ],
    }

from apps.categories.models import Category


DEFAULT_CATEGORY_GROUPS = {
    Category.Kind.EXPENSE: {
        "Housing": (
            "Rent", "Mortgage", "Maintenance", "Electricity", "Water", "Internet",
        ),
        "Food": (
            "Groceries", "Restaurants", "Takeaway", "Coffee", "Snacks",
        ),
        "Transport": (
            "Fuel", "Taxi", "Public transport", "Car maintenance", "Parking",
        ),
        "Personal": (
            "Clothing", "Shoes", "Haircuts", "Toiletries", "Personal care",
        ),
        "Health": (
            "Medicine", "Doctor", "Dental", "Insurance", "Optical",
        ),
        "Family": (
            "Childcare", "School fees", "Family support", "Gifts", "Activities",
        ),
        "Lifestyle": (
            "Entertainment", "Subscriptions", "Hobbies", "Travel", "Fitness",
        ),
        "Financial": (
            "Bank fees", "Loan interest", "Credit card fees", "Taxes",
        ),
        "Business": (
            "Supplies", "Equipment", "Marketing", "Software",
        ),
        "Communication": (
            "Mobile airtime", "Mobile data", "Phone bills",
        ),
        "Giving": (
            "Charity", "Donations", "Religious contributions",
        ),
        "Other": (
            "Miscellaneous", "Unexpected expenses", "Uncategorized",
        ),
    },
    Category.Kind.INCOME: {
        "Employment": ("Salary", "Bonus", "Overtime", "Commission"),
        "Business": ("Sales", "Service income", "Business profit"),
        "Freelance": ("Consulting", "Freelance work", "Contract work"),
        "Investments": ("Dividends", "Interest", "Capital gains"),
        "Property": ("Rent received",),
        "Other": ("Gifts", "Refunds", "Reimbursements", "Other income"),
    },
}


def seed_default_categories(workspace):
    for kind, groups in DEFAULT_CATEGORY_GROUPS.items():
        for group, names in groups.items():
            for name in names:
                Category.objects.get_or_create(
                    workspace=workspace,
                    name=name,
                    kind=kind,
                    defaults={"group": group},
                )

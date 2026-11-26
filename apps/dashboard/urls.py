from django.urls import path

from apps.dashboard.pages import (
    accounts_page,
    activity,
    account_management,
    account_detail,
    bill_payments,
    categories_page,
    export_accounts,
    export_account_transactions,
    export_reports_csv,
    goals,
    more,
    reports,
    record_transaction,
    settings,
)
from apps.dashboard.views import (
    home,
    home_cards,
    offline_context,
    service_worker,
    switch_workspace,
    sync_timezone,
)

app_name = "dashboard"

urlpatterns = [
    path("", home_cards, name="home"),
    path("home/", home_cards, name="home_cards"),
    path("overview/", home, name="overview"),
    path("timezone/sync/", sync_timezone, name="sync_timezone"),
    path("api/offline/context/", offline_context, name="offline_context"),
    path("service-worker.js", service_worker, name="service_worker"),
    path("workspace/switch/", switch_workspace, name="switch_workspace"),
    path("activity/", activity, name="activity"),
    path("transaction-history/", activity, name="transaction_history"),
    path("activity/new/", record_transaction, name="record_transaction"),
    path("accounts/", accounts_page, name="accounts"),
    path("accounts/export.csv", export_accounts, name="accounts_export"),
    path("accounts/<uuid:account_id>/", account_detail, name="account_detail"),
    path(
        "accounts/<uuid:account_id>/transactions.csv",
        export_account_transactions,
        name="account_transactions_export",
    ),
    path("categories/", categories_page, name="categories"),
    path("reports/", reports, name="reports"),
    path("reports/export.csv", export_reports_csv, name="reports_export"),
    path("goals/", goals, name="goals"),
    path("more/", more, name="more"),
    path("settings/", settings, name="settings"),
    path("settings/accounts/", account_management, name="settings_accounts"),
    path("bills-payments/", bill_payments, name="bills_payments"),
]

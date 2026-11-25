from django.urls import path

from dashboard.pages import (
    accounts_page,
    activity,
    categories_page,
    export_accounts,
    goals,
    more,
    reports,
    settings,
)
from dashboard.views import home, switch_workspace

app_name = "dashboard"

urlpatterns = [
    path("", home, name="home"),
    path("workspace/switch/", switch_workspace, name="switch_workspace"),
    path("activity/", activity, name="activity"),
    path("accounts/", accounts_page, name="accounts"),
    path("accounts/export.csv", export_accounts, name="accounts_export"),
    path("categories/", categories_page, name="categories"),
    path("reports/", reports, name="reports"),
    path("goals/", goals, name="goals"),
    path("more/", more, name="more"),
    path("settings/", settings, name="settings"),
]

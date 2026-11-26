from django.urls import path

from apps.workspaces.pages import create_shared_workspace

app_name = "workspaces"

urlpatterns = [
    path("new/", create_shared_workspace, name="create"),
]

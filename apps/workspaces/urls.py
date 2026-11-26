from django.urls import path

from apps.workspaces.views import workspaces

urlpatterns = [path("", workspaces, name="workspaces")]

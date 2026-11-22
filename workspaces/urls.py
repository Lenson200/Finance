from django.urls import path

from workspaces.views import workspaces

urlpatterns = [path("", workspaces, name="workspaces")]

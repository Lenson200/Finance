from django.urls import path

from apps.accounts.views import accounts

urlpatterns = [path("", accounts, name="accounts")]

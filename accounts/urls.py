from django.urls import path

from accounts.views import accounts

urlpatterns = [path("", accounts, name="accounts")]

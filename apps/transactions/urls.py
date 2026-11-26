from django.urls import path

from apps.transactions.views import transactions

urlpatterns = [path("", transactions, name="transactions")]

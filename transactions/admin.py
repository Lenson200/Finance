from django.contrib import admin

from transactions.models import Transaction, TransactionTemplate


admin.site.register(Transaction)
admin.site.register(TransactionTemplate)

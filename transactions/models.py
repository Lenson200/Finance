import uuid

from django.db import models
from django.utils import timezone


class Transaction(models.Model):
    class Kind(models.TextChoices):
        INCOME = "income", "Income"
        EXPENSE = "expense", "Expense"
        TRANSFER = "transfer", "Transfer"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    account = models.ForeignKey(
        "accounts.Account",
        on_delete=models.PROTECT,
        related_name="transactions",
    )
    destination_account = models.ForeignKey(
        "accounts.Account",
        on_delete=models.PROTECT,
        related_name="incoming_transfers",
        null=True,
        blank=True,
    )
    kind = models.CharField(max_length=10, choices=Kind.choices)
    amount = models.DecimalField(max_digits=14, decimal_places=2)
    description = models.CharField(max_length=255, blank=True)
    occurred_at = models.DateTimeField(default=timezone.now)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-occurred_at", "-id"]

    def __str__(self):
        return f"{self.kind}: {self.amount} {self.account.currency}"

import uuid

from django.db import models


class Account(models.Model):
    class AccountType(models.TextChoices):
        BANK = "bank", "Bank"
        CASH = "cash", "Cash"
        SAVINGS = "savings", "Savings"
        CREDIT_CARD = "credit_card", "Credit card"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    workspace = models.ForeignKey(
        "workspaces.Workspace",
        on_delete=models.CASCADE,
        related_name="accounts",
    )
    name = models.CharField(max_length=150)
    account_type = models.CharField(max_length=20, choices=AccountType.choices)
    currency = models.CharField(max_length=3)
    opening_balance = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["name", "id"]

    def __str__(self):
        return f"{self.name} ({self.currency})"

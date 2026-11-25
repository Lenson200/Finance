import uuid
from decimal import Decimal, InvalidOperation

from django.core.exceptions import ValidationError
from django.db import models


class Account(models.Model):
    class AccountType(models.TextChoices):
        BANK = "bank", "Bank"
        CASH = "cash", "Cash"
        MOBILE_MONEY = "mobile_money", "Mobile money"
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
    opening_balance_rate = models.DecimalField(
        max_digits=18,
        decimal_places=10,
        default=None,
        null=True,
        blank=True,
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["name", "id"]

    def __str__(self):
        return f"{self.name} ({self.currency})"

    def clean(self):
        from workspaces.models import Currency

        super().clean()
        if self.currency:
            self.currency = self.currency.upper()
        if not Currency.objects.filter(code=self.currency, is_active=True).exists():
            raise ValidationError({"currency": "Choose an active supported currency."})
        if self.opening_balance_rate is None:
            base_currency = (
                self.workspace.base_currency if self.workspace_id else None
            )
            if self.currency == base_currency:
                self.opening_balance_rate = Decimal("1")
            else:
                try:
                    opening_balance = Decimal(str(self.opening_balance))
                except (InvalidOperation, TypeError, ValueError):
                    opening_balance = Decimal("0")
                if opening_balance:
                    raise ValidationError(
                        {"opening_balance_rate": "Enter an exchange rate for this opening balance."}
                    )
                self.opening_balance_rate = Decimal("1")
        else:
            try:
                self.opening_balance_rate = Decimal(str(self.opening_balance_rate))
            except (InvalidOperation, TypeError, ValueError):
                raise ValidationError(
                    {"opening_balance_rate": "Enter a valid exchange rate."}
                )
        if (
            not self.opening_balance_rate.is_finite()
            or self.opening_balance_rate <= 0
        ):
            raise ValidationError({"opening_balance_rate": "The exchange rate must be positive."})
        if (
            self.workspace_id
            and self.currency == self.workspace.base_currency
            and self.opening_balance_rate != 1
        ):
            raise ValidationError(
                {"opening_balance_rate": "The opening balance rate must be 1 for the base currency."}
            )

    def save(self, *args, **kwargs):
        self.full_clean()
        return super().save(*args, **kwargs)

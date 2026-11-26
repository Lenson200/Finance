import uuid
from decimal import Decimal, InvalidOperation

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models


class Account(models.Model):
    class AccountType(models.TextChoices):
        BANK = "bank", "Bank"
        CASH = "cash", "Cash"
        MOBILE_MONEY = "mobile_money", "Mobile money"
        SAVINGS = "savings", "Savings"
        CREDIT_CARD = "credit_card", "Credit card"
        DEBT = "debt", "Debt or loan"

    class DebtTerm(models.TextChoices):
        SHORT = "short", "Short-term"
        LONG = "long", "Long-term"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    workspace = models.ForeignKey(
        "workspaces.Workspace",
        on_delete=models.CASCADE,
        related_name="accounts",
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="created_accounts",
        null=True,
        blank=True,
    )
    name = models.CharField(max_length=150)
    account_type = models.CharField(max_length=20, choices=AccountType.choices)
    debt_term = models.CharField(
        max_length=5,
        choices=DebtTerm.choices,
        default=DebtTerm.SHORT,
    )
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

    @property
    def is_liability(self):
        return self.account_type in (self.AccountType.CREDIT_CARD, self.AccountType.DEBT)

    def clean(self):
        from apps.workspaces.models import Currency

        super().clean()
        if self.created_by_id and self.workspace_id:
            if not self.workspace.memberships.filter(user_id=self.created_by_id).exists():
                raise ValidationError({"created_by": "Account creator must belong to this workspace."})
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
        adding = self._state.adding
        if adding and self.created_by_id is None and self.workspace_id:
            self.created_by = self.workspace.created_by
        self.full_clean()
        result = super().save(*args, **kwargs)
        if adding:
            creator_membership = self.workspace.memberships.filter(
                user_id=self.created_by_id
            ).first()
            owners = self.workspace.memberships.filter(role="owner")
            memberships = list(owners)
            if creator_membership and creator_membership not in memberships:
                memberships.append(creator_membership)
            for membership in memberships:
                AccountAccess.objects.get_or_create(
                    account=self,
                    membership=membership,
                    defaults={
                        "can_write": True,
                        "can_manage": (
                            membership.role == "owner"
                            or membership.user_id == self.created_by_id
                        ),
                    },
                )
        return result


class AccountAccess(models.Model):
    account = models.ForeignKey(
        Account,
        on_delete=models.CASCADE,
        related_name="access_grants",
    )
    membership = models.ForeignKey(
        "workspaces.Membership",
        on_delete=models.CASCADE,
        related_name="account_access",
    )
    can_write = models.BooleanField(default=False)
    can_manage = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=("account", "membership"),
                name="accounts_unique_account_access",
            ),
        ]
        ordering = ["membership__user__email"]

    def clean(self):
        super().clean()
        if (
            self.account_id
            and self.membership_id
            and self.account.workspace_id != self.membership.workspace_id
        ):
            raise ValidationError(
                {"membership": "Account access must belong to the account workspace."}
            )

    def save(self, *args, **kwargs):
        self.full_clean()
        return super().save(*args, **kwargs)

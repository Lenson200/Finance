import uuid
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

from django.core.exceptions import ValidationError
from django.db import models
from django.db import transaction as db_transaction
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
    category = models.ForeignKey(
        "categories.Category",
        on_delete=models.PROTECT,
        related_name="transactions",
        null=True,
        blank=True,
    )
    refund_of = models.ForeignKey(
        "self",
        on_delete=models.PROTECT,
        related_name="refunds",
        null=True,
        blank=True,
        limit_choices_to={"kind": "expense"},
    )
    kind = models.CharField(max_length=10, choices=Kind.choices)
    amount = models.DecimalField(max_digits=14, decimal_places=2)
    exchange_rate = models.DecimalField(
        max_digits=16,
        decimal_places=8,
        default=None,
        null=True,
        blank=True,
        help_text="Reporting-currency units for one unit of the account currency.",
    )
    amount_in_base = models.DecimalField(
        max_digits=14,
        decimal_places=2,
        default=0,
        editable=False,
    )
    idempotency_key = models.UUIDField(null=True, blank=True, unique=True)
    description = models.CharField(max_length=255, blank=True)
    occurred_at = models.DateTimeField(default=timezone.now)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-occurred_at", "-id"]

    def __str__(self):
        return f"{self.kind}: {self.amount} {self.account.currency}"

    def clean(self):
        super().clean()
        errors = {}
        occurred_at = self.occurred_at
        if occurred_at:
            if timezone.is_naive(occurred_at):
                occurred_at = timezone.make_aware(
                    occurred_at,
                    timezone.get_current_timezone(),
                )
            if occurred_at > timezone.now():
                errors["occurred_at"] = "Completed transactions cannot be dated in the future."
        if self.amount is None or not self.amount.is_finite() or self.amount <= 0:
            errors["amount"] = "Amount must be greater than zero."
        if (
            self.exchange_rate is None
            or not self.exchange_rate.is_finite()
            or self.exchange_rate <= 0
        ):
            if not (
                self.exchange_rate is None
                and self.account_id
                and self.account.currency == self.account.workspace.base_currency
            ):
                errors["exchange_rate"] = "The exchange rate must be greater than zero."
        elif (
            self.account_id
            and self.account.currency == self.account.workspace.base_currency
            and self.exchange_rate != 1
        ):
            errors["exchange_rate"] = "Exchange rate must be 1 for the base currency."
        if self.kind == self.Kind.TRANSFER:
            if not self.destination_account_id:
                errors["destination_account"] = "Choose a destination account."
            elif not self.account_id:
                errors["account"] = "Choose a source account."
            elif self.destination_account_id == self.account_id:
                errors["destination_account"] = "Transfer accounts must be different."
            elif (
                self.destination_account.workspace_id != self.account.workspace_id
                or self.destination_account.currency != self.account.currency
            ):
                errors["destination_account"] = (
                    "Transfer accounts must belong to this workspace and use the same currency."
                )
            if self.category_id:
                errors["category"] = "Transfers cannot have an income or expense category."
        elif self.destination_account_id:
            errors["destination_account"] = "Only transfers have a destination account."
        if self.refund_of_id:
            if self.kind != self.Kind.INCOME:
                errors["refund_of"] = "Only income transactions can record an expense refund."
            elif (
                self.refund_of.kind != self.Kind.EXPENSE
                or self.refund_of.account.workspace_id != self.account.workspace_id
            ):
                errors["refund_of"] = "Choose an expense from this workspace."
            else:
                refunded = (
                    self.refund_of.refunds.aggregate(
                        total=models.Sum("amount_in_base")
                    )["total"]
                    or Decimal("0.00")
                )
                if self.amount_in_base > self.refund_of.amount_in_base - refunded:
                    errors["refund_of"] = (
                        "Refund cannot exceed the remaining expense amount."
                    )
        if self.category_id and self.kind in (self.Kind.INCOME, self.Kind.EXPENSE):
            expected_kind = self.kind
            if (
                self.category.workspace_id != self.account.workspace_id
                or self.category.kind != expected_kind
            ):
                errors["category"] = "Choose a matching category in this workspace."
        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        if not self._state.adding:
            raise ValidationError("Posted transactions cannot be edited; record a correction instead.")

        if not self.account_id:
            raise ValidationError({"account": "Choose an account."})
        if self.kind in (self.Kind.INCOME, self.Kind.EXPENSE) and not self.category_id:
            from apps.categories.models import Category

            name = "Other income" if self.kind == self.Kind.INCOME else "Other expense"
            self.category, _ = Category.objects.get_or_create(
                workspace=self.account.workspace,
                name=name,
                kind=self.kind,
            )

        try:
            self.amount = Decimal(str(self.amount))
            if (
                self.exchange_rate is None
                and self.account.currency == self.account.workspace.base_currency
            ):
                self.exchange_rate = Decimal("1")
            elif self.exchange_rate is not None:
                self.exchange_rate = Decimal(str(self.exchange_rate))
        except (InvalidOperation, TypeError, ValueError):
            raise ValidationError("Enter a valid amount and exchange rate.")
        if (
            not self.amount.is_finite()
            or self.amount <= 0
            or self.exchange_rate is None
            or not self.exchange_rate.is_finite()
            or self.exchange_rate <= 0
        ):
            raise ValidationError(
                "A positive finite amount and exchange rate are required."
            )
        if self.idempotency_key is None:
            self.idempotency_key = uuid.uuid4()
        self.amount_in_base = (self.amount * self.exchange_rate).quantize(
            Decimal("0.01"),
            rounding=ROUND_HALF_UP,
        )
        if self.amount_in_base <= 0:
            raise ValidationError(
                "The converted amount is too small to post in the reporting currency."
            )
        with db_transaction.atomic():
            if self.refund_of_id:
                self.refund_of = Transaction.objects.select_for_update().get(
                    pk=self.refund_of_id
                )
            self.full_clean()
            super().save(*args, **kwargs)
            entry = JournalEntry.objects.create(
                transaction=self,
                currency=self.account.workspace.base_currency,
            )
            if self.kind == self.Kind.INCOME:
                debit_account, credit_category = self.account, self.category
                debit_category, credit_account = None, None
            elif self.kind == self.Kind.EXPENSE:
                debit_account, credit_category = None, None
                debit_category, credit_account = self.category, self.account
            else:
                debit_account, credit_account = self.destination_account, self.account
                debit_category = credit_category = None

            JournalLine.objects.create(
                entry=entry,
                account=debit_account,
                category=debit_category,
                debit=self.amount_in_base,
            )
            JournalLine.objects.create(
                entry=entry,
                account=credit_account,
                category=(
                    self.refund_of.category
                    if self.refund_of_id and self.refund_of.category_id
                    else credit_category
                ),
                credit=self.amount_in_base,
            )

    def delete(self, *args, **kwargs):
        raise ValidationError("Posted transactions cannot be deleted.")


class TransactionTemplate(models.Model):
    workspace = models.ForeignKey(
        "workspaces.Workspace",
        on_delete=models.CASCADE,
        related_name="transaction_templates",
    )
    name = models.CharField(max_length=100)
    kind = models.CharField(max_length=10, choices=Transaction.Kind.choices)
    account = models.ForeignKey(
        "accounts.Account",
        on_delete=models.CASCADE,
        related_name="transaction_templates",
    )
    destination_account = models.ForeignKey(
        "accounts.Account",
        on_delete=models.CASCADE,
        related_name="incoming_transaction_templates",
        null=True,
        blank=True,
    )
    category = models.ForeignKey(
        "categories.Category",
        on_delete=models.SET_NULL,
        related_name="transaction_templates",
        null=True,
        blank=True,
    )
    amount = models.DecimalField(max_digits=14, decimal_places=2)
    description = models.CharField(max_length=255, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["name", "id"]
        constraints = [
            models.UniqueConstraint(
                fields=("workspace", "name"),
                name="transactions_unique_template_name",
            ),
        ]

    def clean(self):
        super().clean()
        if self.account_id and self.account.workspace_id != self.workspace_id:
            raise ValidationError({"account": "Choose an account in this workspace."})
        if self.destination_account_id and (
            self.destination_account.workspace_id != self.workspace_id
            or self.destination_account_id == self.account_id
            or self.destination_account.currency != self.account.currency
        ):
            raise ValidationError(
                {"destination_account": "Choose a different same-currency account."}
            )
        if self.category_id and (
            self.category.workspace_id != self.workspace_id
            or self.category.kind != self.kind
        ):
            raise ValidationError({"category": "Choose a matching category."})


class JournalEntry(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    transaction = models.OneToOneField(
        Transaction,
        on_delete=models.PROTECT,
        related_name="journal_entry",
    )
    currency = models.CharField(max_length=3)
    posted_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["posted_at", "id"]

    def save(self, *args, **kwargs):
        if not self._state.adding:
            raise ValidationError("Posted journal entries are immutable.")
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError("Posted journal entries cannot be deleted.")


class JournalLine(models.Model):
    entry = models.ForeignKey(
        JournalEntry,
        on_delete=models.PROTECT,
        related_name="lines",
    )
    account = models.ForeignKey(
        "accounts.Account",
        on_delete=models.PROTECT,
        related_name="journal_lines",
        null=True,
        blank=True,
    )
    category = models.ForeignKey(
        "categories.Category",
        on_delete=models.PROTECT,
        related_name="journal_lines",
        null=True,
        blank=True,
    )
    debit = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    credit = models.DecimalField(max_digits=14, decimal_places=2, default=0)

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=(
                    (models.Q(debit__gt=0) & models.Q(credit=0))
                    | (models.Q(credit__gt=0) & models.Q(debit=0))
                ),
                name="transactions_journal_line_one_positive_side",
            ),
            models.CheckConstraint(
                condition=(
                    (models.Q(account__isnull=False) & models.Q(category__isnull=True))
                    | (models.Q(account__isnull=True) & models.Q(category__isnull=False))
                ),
                name="transactions_journal_line_one_ledger_account",
            ),
        ]

    def save(self, *args, **kwargs):
        if not self._state.adding:
            raise ValidationError("Posted journal lines are immutable.")
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError("Posted journal lines cannot be deleted.")

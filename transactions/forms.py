import uuid
from datetime import datetime, time
from decimal import Decimal
from zoneinfo import ZoneInfo

from django import forms
from django.db import transaction as db_transaction
from django.utils import timezone

from accounts.models import Account
from categories.models import Category
from transactions.models import Transaction, TransactionTemplate


class TransactionForm(forms.ModelForm):
    save_as_template = forms.BooleanField(
        label="Save as a quick-entry template",
        required=False,
    )
    template_name = forms.CharField(
        label="Template name",
        max_length=100,
        required=False,
        widget=forms.TextInput(attrs={"placeholder": "e.g. Morning coffee"}),
    )
    occurred_date = forms.DateField(
        label="Date",
        required=False,
        widget=forms.DateInput(attrs={"type": "date"}),
    )
    idempotency_key = forms.UUIDField(
        widget=forms.HiddenInput,
        initial=uuid.uuid4,
    )

    class Meta:
        model = Transaction
        fields = (
            "kind",
            "account",
            "destination_account",
            "category",
            "refund_of",
            "amount",
            "description",
            "idempotency_key",
        )
        widgets = {
            "amount": forms.NumberInput(attrs={"step": "0.01", "min": "0.01"}),
            "description": forms.TextInput(
                attrs={"placeholder": "e.g. Lunch at restaurant"}
            ),
        }

    def __init__(self, *args, workspace, **kwargs):
        self.workspace = workspace
        super().__init__(*args, **kwargs)
        self.fields["account"].queryset = Account.objects.filter(workspace=workspace)
        self.fields["destination_account"].queryset = Account.objects.filter(
            workspace=workspace
        )
        self.fields["destination_account"].required = False
        self.fields["category"].queryset = Category.objects.filter(
            workspace=workspace
        )
        self.fields["category"].required = False
        self.fields["refund_of"].queryset = Transaction.objects.filter(
            account__workspace=workspace,
            kind=Transaction.Kind.EXPENSE,
        ).select_related("account", "category")
        self.fields["refund_of"].required = False
        self.fields["refund_of"].label = "Original expense (optional)"
        self.fields["refund_of"].label_from_instance = (
            lambda item: (
                f"{item.description or item.category.name} · "
                f"{item.amount} {item.account.currency}"
            )
        )
        self.fields["occurred_date"].initial = timezone.localtime(
            timezone.now(),
            ZoneInfo(workspace.timezone),
        ).date()
        if not self.is_bound and not self.instance.pk:
            accounts = self.fields["account"].queryset
            self.fields["account"].initial = accounts.first()
            if accounts.exists():
                self.fields["amount"].label = f"Amount ({accounts.first().currency})"

    def clean(self):
        cleaned = super().clean()
        account = cleaned.get("account")
        kind = cleaned.get("kind")
        destination = cleaned.get("destination_account")
        category = cleaned.get("category")
        refund_of = cleaned.get("refund_of")
        if cleaned.get("save_as_template") and not cleaned.get("template_name", "").strip():
            self.add_error("template_name", "Give this quick-entry template a name.")
        if account and kind and kind != Transaction.Kind.TRANSFER:
            if not category:
                self.add_error("category", "Choose an income or expense category.")
            elif category.kind != kind:
                self.add_error("category", "Choose a category matching the transaction.")
        if kind == Transaction.Kind.TRANSFER and not destination:
            self.add_error("destination_account", "Choose a destination account.")
        if destination and account:
            if destination.pk == account.pk:
                self.add_error("destination_account", "Choose a different account.")
            elif destination.currency != account.currency:
                self.add_error(
                    "destination_account",
                    "Transfers must use accounts with the same currency.",
                )
        if account:
            self.fields["amount"].label = f"Amount ({account.currency})"
            if account.currency == self.workspace.base_currency:
                self.instance.exchange_rate = Decimal("1")
            else:
                try:
                    from workspaces.rates import (
                        ExchangeRateUnavailable,
                        get_exchange_rate,
                    )

                    self.instance.exchange_rate = get_exchange_rate(
                        account.currency,
                        self.workspace.base_currency,
                    )
                except ExchangeRateUnavailable as error:
                    self.add_error(None, str(error))
        if refund_of and kind != Transaction.Kind.INCOME:
            self.add_error("refund_of", "Choose Income to record a refund.")
        occurred_date = cleaned.get("occurred_date") or timezone.localtime(
            timezone.now(),
            ZoneInfo(self.workspace.timezone),
        ).date()
        if occurred_date:
            cleaned["occurred_at"] = timezone.make_aware(
                datetime.combine(occurred_date, time.min),
                ZoneInfo(self.workspace.timezone),
            )
        return cleaned

    def save(self, commit=True):
        item = super().save(commit=False)
        item.occurred_at = self.cleaned_data["occurred_at"]
        if commit:
            with db_transaction.atomic():
                item.save()
                if self.cleaned_data.get("save_as_template"):
                    TransactionTemplate.objects.update_or_create(
                        workspace=self.workspace,
                        name=self.cleaned_data["template_name"].strip(),
                        defaults={
                            "kind": item.kind,
                            "account": item.account,
                            "destination_account": item.destination_account,
                            "category": item.category,
                            "amount": item.amount,
                            "description": item.description,
                        },
                    )
        return item

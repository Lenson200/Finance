import uuid
from datetime import datetime, time
from decimal import Decimal,ROUND_HALF_UP
from django import forms
from django.db import transaction as db_transaction
from django.utils import timezone
from apps.accounts.access import accounts_for_membership
from apps.accounts.models import Account
from apps.categories.models import Category
from apps.transactions.models import Transaction, TransactionTemplate


class CategorySelect(forms.Select):
    def create_option(self, name, value, label, selected, index, **kwargs):
        option = super().create_option(
            name,
            value,
            label,
            selected,
            index,
            **kwargs,
        )
        category_kind = self.category_kinds.get(str(value))
        if category_kind:
            option["attrs"]["data-kind"] = category_kind
            if category_kind != self.selected_kind:
                option["attrs"]["hidden"] = True
        return option



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
            "expense_intent",
            "amount",
            "description",
            "idempotency_key",
        )
        widgets = {
            "amount": forms.NumberInput(
                attrs={"step": "0.01", "min": "0.01"}
            ),
            "description": forms.TextInput(
                attrs={"placeholder": "e.g. Lunch at restaurant"}
            ),
        }

    def __init__(self, *args, workspace, membership, **kwargs):
        self.workspace = workspace
        self.membership = membership
        super().__init__(*args, **kwargs)

        writable_accounts = accounts_for_membership(membership, write=True)
        self.fields["account"].queryset = writable_accounts
        self.fields["destination_account"].queryset = writable_accounts
        self.fields["destination_account"].required = False

        self.fields["category"].queryset = Category.objects.filter(
            workspace=workspace
        )
        self.fields["category"].widget = CategorySelect()
        self.fields["category"].widget.choices = self.fields["category"].choices
        self.fields["category"].widget.category_kinds = {
            str(category.pk): category.kind
            for category in self.fields["category"].queryset.only("pk", "kind")
        }
        selected_kind = (
            self.data.get(self.add_prefix("kind"))
            if self.is_bound
            else self.initial.get("kind", Transaction.Kind.EXPENSE)
        )
        self.fields["category"].widget.selected_kind = selected_kind
        self.fields["category"].required = False
        self.fields["expense_intent"].required = False

        self.fields["refund_of"].queryset = Transaction.objects.filter(
            account__in=writable_accounts,
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

        self.fields["occurred_date"].initial = timezone.localdate()

        if not self.is_bound and not self.instance.pk:
            accounts = self.fields["account"].queryset
            first_account = accounts.first()

            if first_account:
                self.fields["account"].initial = first_account
                self.fields["amount"].label = (
                    f"Amount ({first_account.currency})"
                )

    def clean(self):
        cleaned = super().clean()

        account = cleaned.get("account")
        kind = cleaned.get("kind")
        destination = cleaned.get("destination_account")
        category = cleaned.get("category")
        refund_of = cleaned.get("refund_of")

        # Quick-entry template validation
        if cleaned.get("save_as_template") and not cleaned.get(
            "template_name", ""
        ).strip():
            self.add_error(
                "template_name",
                "Give this quick-entry template a name.",
            )

        # Category validation
        if account and kind and kind != Transaction.Kind.TRANSFER:
            if not category:
                self.add_error(
                    "category",
                    "Choose an income or expense category.",
                )
            elif category.kind != kind:
                self.add_error(
                    "category",
                    "Choose a category matching the transaction.",
                )

        # Transfer validation
        if kind == Transaction.Kind.TRANSFER and not destination:
            self.add_error(
                "destination_account",
                "Choose a destination account.",
            )

        if destination and account:
            if destination.pk == account.pk:
                self.add_error(
                    "destination_account",
                    "Choose a different account.",
                )
            elif destination.currency != account.currency:
                self.add_error(
                    "destination_account",
                    "Transfers must use accounts with the same currency.",
                )

        # Refund validation
        if refund_of and kind != Transaction.Kind.INCOME:
            self.add_error(
                "refund_of",
                "Choose Income to record a refund.",
            )

        # Exchange-rate calculation and precision handling
        if account:
            self.fields["amount"].label = f"Amount ({account.currency})"

            if account.currency == self.workspace.base_currency:
                rate = Decimal("1")
            else:
                try:
                    from apps.workspaces.rates import (
                        ExchangeRateUnavailable,
                        get_exchange_rate,
                    )

                    rate = get_exchange_rate(
                        account.currency,
                        self.workspace.base_currency,
                    )

                except ExchangeRateUnavailable as error:
                    self.add_error(None, str(error))
                    rate = None

            if rate is not None:
                decimal_places = Transaction._meta.get_field(
                    "exchange_rate"
                ).decimal_places

                precision = Decimal("1").scaleb(-decimal_places)

                self.instance.exchange_rate = Decimal(
                    str(rate)
                ).quantize(
                    precision,
                    rounding=ROUND_HALF_UP,
                )

        # Transaction date
        occurred_date = cleaned.get("occurred_date") or timezone.localdate()

        cleaned["occurred_at"] = timezone.make_aware(
            datetime.combine(occurred_date, time.min),
            timezone.get_current_timezone(),
        )

        return cleaned

    def clean_occurred_date(self):
        occurred_date = self.cleaned_data.get("occurred_date")
        if occurred_date and occurred_date > timezone.localdate():
            raise forms.ValidationError("Completed transactions cannot use a future date.")
        return occurred_date

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

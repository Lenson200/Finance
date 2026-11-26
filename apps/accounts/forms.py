from django import forms

from apps.accounts.access import accounts_for_membership
from apps.accounts.models import Account, AccountAccess
from apps.workspaces.models import Currency, Membership
from apps.workspaces.rates import ExchangeRateUnavailable, get_exchange_rate


class AccountForm(forms.ModelForm):
    currency = forms.ChoiceField()

    class Meta:
        model = Account
        fields = (
            "name",
            "account_type",
            "debt_term",
            "currency",
            "opening_balance",
        )
        widgets = {"opening_balance": forms.NumberInput(attrs={"step": "0.01"})}

    def __init__(self, *args, workspace, membership, **kwargs):
        self.workspace = workspace
        self.membership = membership
        super().__init__(*args, **kwargs)
        currencies = Currency.objects.filter(is_active=True)
        self.fields["currency"].choices = [
            (currency.code, f"{currency.code} — {currency.name}")
            for currency in currencies
        ]
        self.fields["debt_term"].required = False
        self.fields["debt_term"].initial = Account.DebtTerm.SHORT
        if not self.is_bound and not self.instance.pk:
            self.fields["currency"].initial = workspace.base_currency

    def clean(self):
        cleaned = super().clean()
        currency = cleaned.get("currency")
        opening_balance = cleaned.get("opening_balance")
        cleaned["debt_term"] = cleaned.get("debt_term") or Account.DebtTerm.SHORT
        if currency and opening_balance is not None:
            if currency == self.workspace.base_currency or not opening_balance:
                self.instance.opening_balance_rate = 1
            else:
                try:
                    self.instance.opening_balance_rate = get_exchange_rate(
                        currency,
                        self.workspace.base_currency,
                    )
                except ExchangeRateUnavailable as error:
                    self.add_error(None, str(error))
        return cleaned

    def save(self, commit=True):
        account = super().save(commit=False)
        account.workspace = self.workspace
        account.created_by = self.membership.user
        if account.currency == self.workspace.base_currency or not account.opening_balance:
            account.opening_balance_rate = 1
        elif account.opening_balance_rate is None:
            account.opening_balance_rate = get_exchange_rate(
                account.currency,
                self.workspace.base_currency,
            )
        if commit:
            account.save()
        return account


class AccountAccessForm(forms.ModelForm):
    class Meta:
        model = AccountAccess
        fields = ("account", "membership", "can_write")
        labels = {"can_write": "Allow recording transactions"}

    def __init__(self, *args, membership, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["account"].queryset = accounts_for_membership(
            membership,
            manage=True,
        )
        self.fields["membership"].queryset = Membership.objects.filter(
            workspace=membership.workspace,
        ).select_related("user")
        self.fields["membership"].label_from_instance = (
            lambda item: f"{item.user.get_full_name() or item.user.email} · {item.get_role_display()}"
        )

    def save(self, commit=True):
        account = self.cleaned_data["account"]
        membership = self.cleaned_data["membership"]
        access, created = AccountAccess.objects.get_or_create(
            account=account,
            membership=membership,
            defaults={
                "can_write": self.cleaned_data["can_write"],
                "can_manage": False,
            },
        )
        if not created and access.can_write != self.cleaned_data["can_write"]:
            access.can_write = self.cleaned_data["can_write"]
            if commit:
                access.save(update_fields=["can_write"])
        return access

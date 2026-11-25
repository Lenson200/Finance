from django import forms

from accounts.models import Account
from workspaces.models import Currency
from workspaces.rates import ExchangeRateUnavailable, get_exchange_rate


class AccountForm(forms.ModelForm):
    currency = forms.ChoiceField()

    class Meta:
        model = Account
        fields = (
            "name",
            "account_type",
            "currency",
            "opening_balance",
        )
        widgets = {"opening_balance": forms.NumberInput(attrs={"step": "0.01"})}

    def __init__(self, *args, workspace, **kwargs):
        self.workspace = workspace
        super().__init__(*args, **kwargs)
        currencies = Currency.objects.filter(is_active=True)
        self.fields["currency"].choices = [
            (currency.code, f"{currency.code} — {currency.name}")
            for currency in currencies
        ]
        if not self.is_bound and not self.instance.pk:
            self.fields["currency"].initial = workspace.base_currency

    def clean(self):
        cleaned = super().clean()
        currency = cleaned.get("currency")
        opening_balance = cleaned.get("opening_balance")
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

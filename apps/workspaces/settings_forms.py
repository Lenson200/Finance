from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from django import forms
from django.core.exceptions import ValidationError

from apps.workspaces.models import Currency, Workspace


class WorkspaceSettingsForm(forms.ModelForm):
    base_currency = forms.ChoiceField()

    class Meta:
        model = Workspace
        fields = ("base_currency", "timezone")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["base_currency"].choices = [
            (currency.code, f"{currency.code} — {currency.name}")
            for currency in Currency.objects.filter(is_active=True)
        ]

    def clean_base_currency(self):
        currency = self.cleaned_data["base_currency"]
        if self.instance.pk and currency != self.instance.base_currency:
            from apps.accounts.models import Account
            from apps.transactions.models import Transaction

            if (
                Transaction.objects.filter(account__workspace=self.instance).exists()
                or Account.objects.filter(workspace=self.instance)
                .exclude(opening_balance=0)
                .exists()
            ):
                raise ValidationError(
                    "Base currency cannot change after balances or transactions have been recorded."
                )
        return currency

    def clean_timezone(self):
        value = self.cleaned_data["timezone"].strip()
        try:
            ZoneInfo(value)
        except (ValueError, ZoneInfoNotFoundError):
            raise ValidationError("Enter a valid timezone, such as UTC.")
        return value

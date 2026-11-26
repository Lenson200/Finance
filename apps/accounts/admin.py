from django import forms
from django.contrib import admin

from apps.accounts.models import Account
from apps.workspaces.rates import ExchangeRateUnavailable, get_exchange_rate


class AccountAdminForm(forms.ModelForm):
    class Meta:
        model = Account
        exclude = ("opening_balance_rate",)

    def clean(self):
        cleaned = super().clean()
        workspace = cleaned.get("workspace")
        currency = cleaned.get("currency")
        opening_balance = cleaned.get("opening_balance")
        if workspace and currency and opening_balance is not None:
            if (
                self.instance.pk
                and not {"workspace", "currency", "opening_balance"}.intersection(
                    self.changed_data
                )
            ):
                return cleaned
            elif currency == workspace.base_currency or not opening_balance:
                self.instance.opening_balance_rate = 1
            else:
                try:
                    self.instance.opening_balance_rate = get_exchange_rate(
                        currency,
                        workspace.base_currency,
                    )
                except ExchangeRateUnavailable as error:
                    self.add_error(None, str(error))
        return cleaned


@admin.register(Account)
class AccountAdmin(admin.ModelAdmin):
    form = AccountAdminForm

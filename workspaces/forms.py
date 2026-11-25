from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from django import forms

from workspaces.models import Currency, Workspace


class SharedWorkspaceForm(forms.ModelForm):
    base_currency = forms.ChoiceField()

    class Meta:
        model = Workspace
        fields = ("name", "base_currency", "timezone")
        widgets = {
            "name": forms.TextInput(attrs={"placeholder": "e.g. Family finances"}),
            "timezone": forms.TextInput(attrs={"placeholder": "e.g. Africa/Doha"}),
        }

    def clean_base_currency(self):
        return self.cleaned_data["base_currency"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.is_bound:
            data = self.data.copy()
            key = self.add_prefix("base_currency")
            if data.get(key):
                data[key] = data[key].upper()
            self.data = data
        self.fields["base_currency"].choices = [
            (currency.code, f"{currency.code} — {currency.name}")
            for currency in Currency.objects.filter(is_active=True)
        ]

    def clean_timezone(self):
        value = self.cleaned_data["timezone"].strip()
        try:
            ZoneInfo(value)
        except (ValueError, ZoneInfoNotFoundError):
            raise forms.ValidationError("Enter a valid timezone, such as UTC.")
        return value

from django import forms

from categories.models import Category


class CategoryForm(forms.ModelForm):
    group = forms.CharField(
        max_length=50,
        required=False,
        widget=forms.TextInput(
            attrs={
                "list": "category-groups",
                "placeholder": "e.g. Food",
            }
        ),
    )

    class Meta:
        model = Category
        fields = ("name", "group", "kind")

    def __init__(self, *args, workspace, **kwargs):
        self.workspace = workspace
        super().__init__(*args, **kwargs)

    def save(self, commit=True):
        category = super().save(commit=False)
        category.workspace = self.workspace
        if commit:
            category.save()
        return category

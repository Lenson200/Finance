from django.core.exceptions import ValidationError
from django.db import models


class Category(models.Model):
    class Kind(models.TextChoices):
        INCOME = "income", "Income"
        EXPENSE = "expense", "Expense"

    workspace = models.ForeignKey(
        "workspaces.Workspace",
        on_delete=models.CASCADE,
        related_name="categories",
    )
    name = models.CharField(max_length=100)
    group = models.CharField(max_length=50, blank=True)
    kind = models.CharField(max_length=10, choices=Kind.choices)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["kind", "name"]
        constraints = [
            models.UniqueConstraint(
                fields=("workspace", "name", "kind"),
                name="categories_unique_workspace_name_kind",
            ),
        ]

    def __str__(self):
        return f"{self.name} ({self.get_kind_display()})"

    def clean(self):
        super().clean()
        if self.kind not in self.Kind.values:
            raise ValidationError({"kind": "Choose income or expense."})

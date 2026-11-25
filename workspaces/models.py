import uuid

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models


class Currency(models.Model):
    code = models.CharField(max_length=3, primary_key=True)
    name = models.CharField(max_length=50)
    symbol = models.CharField(max_length=8)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["code"]
        verbose_name_plural = "currencies"

    def __str__(self):
        return f"{self.code} — {self.name}"

    def clean(self):
        super().clean()
        if self.code:
            self.code = self.code.upper()
        if (
            not self.code
            or len(self.code) != 3
            or not self.code.isascii()
            or not self.code.isalpha()
        ):
            raise ValidationError({"code": "Currency codes must be three ASCII letters."})

    def save(self, *args, **kwargs):
        self.full_clean()
        return super().save(*args, **kwargs)


class ExchangeRate(models.Model):
    from_currency = models.ForeignKey(
        Currency,
        on_delete=models.PROTECT,
        related_name="exchange_rates_from",
    )
    to_currency = models.ForeignKey(
        Currency,
        on_delete=models.PROTECT,
        related_name="exchange_rates_to",
    )
    rate = models.DecimalField(max_digits=18, decimal_places=10)
    fetched_at = models.DateTimeField()
    provider = models.CharField(max_length=100)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=("from_currency", "to_currency"),
                name="workspaces_unique_exchange_rate_pair",
            ),
            models.CheckConstraint(
                condition=models.Q(rate__gt=0),
                name="workspaces_exchange_rate_positive",
            ),
        ]


class Workspace(models.Model):
    class WorkspaceType(models.TextChoices):
        PERSONAL = "personal", "Personal"
        SHARED = "shared", "Shared"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=150)
    workspace_type = models.CharField(
        max_length=10,
        choices=WorkspaceType.choices,
        default=WorkspaceType.PERSONAL,
    )
    base_currency = models.CharField(max_length=3, default="USD")
    timezone = models.CharField(max_length=64, default="UTC")
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="created_workspaces",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.name


class Membership(models.Model):
    class Role(models.TextChoices):
        OWNER = "owner", "Owner"
        ADMIN = "admin", "Admin"
        MEMBER = "member", "Member"
        VIEWER = "viewer", "Viewer"

    workspace = models.ForeignKey(
        Workspace,
        on_delete=models.CASCADE,
        related_name="memberships",
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="workspace_memberships",
    )
    role = models.CharField(max_length=10, choices=Role.choices)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=("workspace", "user"),
                name="workspaces_unique_membership",
            ),
        ]

    def __str__(self):
        return f"{self.user} - {self.workspace} ({self.role})"

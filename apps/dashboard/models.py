from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone


class Goal(models.Model):
    class Category(models.TextChoices):
        TRAVEL = "travel", "Travel"
        HOME = "home", "Home"
        EDUCATION = "education", "Education"
        EMERGENCY = "emergency", "Emergency fund"
        VEHICLE = "vehicle", "Vehicle"
        TECHNOLOGY = "technology", "Technology"
        WEDDING = "wedding", "Wedding"
        OTHER = "other", "Other"

    class Frequency(models.TextChoices):
        DAILY = "daily", "Daily"
        WEEKLY = "weekly", "Weekly"
        MONTHLY = "monthly", "Monthly"

    workspace = models.ForeignKey(
        "workspaces.Workspace",
        on_delete=models.CASCADE,
        related_name="goals",
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="financial_goals",
    )
    name = models.CharField(max_length=120)
    category = models.CharField(
        max_length=20,
        choices=Category.choices,
        default=Category.OTHER,
    )
    target_amount = models.DecimalField(max_digits=14, decimal_places=2)
    current_savings = models.DecimalField(
        max_digits=14,
        decimal_places=2,
        default=Decimal("0.00"),
    )
    target_date = models.DateField(null=True, blank=True)
    contribution_frequency = models.CharField(
        max_length=10,
        choices=Frequency.choices,
        default=Frequency.MONTHLY,
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["target_date", "name", "pk"]

    @property
    def progress_percent(self):
        if self.target_amount <= 0:
            return 0
        return min(
            100,
            int((self.current_savings / self.target_amount) * 100),
        )

    @property
    def remaining_amount(self):
        return max(Decimal("0.00"), self.target_amount - self.current_savings)

    @property
    def achieved(self):
        return self.current_savings >= self.target_amount

    def clean(self):
        super().clean()
        errors = {}
        if self.target_amount is not None and self.target_amount <= 0:
            errors["target_amount"] = "Target amount must be greater than zero."
        if self.current_savings is not None and self.current_savings < 0:
            errors["current_savings"] = "Current savings cannot be negative."
        if self.target_date and self.target_date < timezone.localdate():
            errors["target_date"] = "Choose today or a future target date."
        if errors:
            raise ValidationError(errors)

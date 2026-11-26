import uuid
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from django.contrib.auth.models import AbstractUser, UserManager as DjangoUserManager
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models.functions import Lower


class UserManager(DjangoUserManager):
    def _create_user(self, email, password, **extra_fields):
        if not email:
            raise ValueError("An email address is required.")
        email = self.normalize_email(email).lower()
        user = self.model(email=email, **extra_fields)
        user.set_password(password)
        user.save(using=self._db)
        return user

    def create_user(self, email, password=None, **extra_fields):
        extra_fields.setdefault("is_staff", False)
        extra_fields.setdefault("is_superuser", False)
        return self._create_user(email, password, **extra_fields)

    def create_superuser(self, email, password=None, **extra_fields):
        extra_fields.setdefault("is_staff", True)
        extra_fields.setdefault("is_superuser", True)
        if extra_fields.get("is_staff") is not True:
            raise ValueError("A superuser must have is_staff=True.")
        if extra_fields.get("is_superuser") is not True:
            raise ValueError("A superuser must have is_superuser=True.")
        return self._create_user(email, password, **extra_fields)


class User(AbstractUser):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    username = None
    email = models.EmailField(unique=True)
    timezone = models.CharField(max_length=64, default="UTC")

    USERNAME_FIELD = "email"
    REQUIRED_FIELDS = ["first_name", "last_name"]

    objects = UserManager()

    class Meta:
        constraints = [
            models.UniqueConstraint(Lower("email"), name="users_email_ci_unique"),
        ]

    def save(self, *args, **kwargs):
        self.email = self.email.lower()
        if not isinstance(self.timezone, str):
            raise ValidationError({"timezone": "Enter a valid IANA timezone."})
        try:
            ZoneInfo(self.timezone)
        except (TypeError, ValueError, ZoneInfoNotFoundError):
            raise ValidationError({"timezone": "Enter a valid IANA timezone."})
        super().save(*args, **kwargs)

    def __str__(self):
        return self.email

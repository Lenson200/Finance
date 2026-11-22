import uuid

from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction
from django.test import TestCase


User = get_user_model()


class UserModelTests(TestCase):
    def test_email_is_the_login_identifier_and_password_is_hashed(self):
        user = User.objects.create_user(
            email="First.Last@Example.com",
            password="secure-test-password",
            first_name="First",
            last_name="Last",
        )

        self.assertEqual(user.email, "first.last@example.com")
        self.assertNotEqual(user.password, "secure-test-password")
        self.assertTrue(user.check_password("secure-test-password"))
        self.assertEqual(User.USERNAME_FIELD, "email")
        self.assertIsInstance(user.pk, uuid.UUID)

    def test_email_uniqueness_is_case_insensitive(self):
        User.objects.create_user(email="person@example.com", password="password")
        with self.assertRaises(IntegrityError), transaction.atomic():
            User.objects.create_user(email="PERSON@example.com", password="password")

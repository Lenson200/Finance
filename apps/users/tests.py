import uuid

from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction
from django.test import TestCase

from apps.categories.models import Category
from apps.workspaces.models import Membership, Workspace


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


class AuthenticationPageTests(TestCase):
    def test_registration_creates_personal_workspace_and_signs_user_in(self):
        response = self.client.post(
            "/register/",
            {
                "email": "new@example.com",
                "first_name": "New",
                "last_name": "Person",
                "password1": "Strong-password-493!",
                "password2": "Strong-password-493!",
            },
        )

        self.assertRedirects(response, "/")
        user = User.objects.get(email="new@example.com")
        workspace = Workspace.objects.get(created_by=user)
        self.assertEqual(workspace.workspace_type, Workspace.WorkspaceType.PERSONAL)
        self.assertEqual(workspace.base_currency, "USD")
        self.assertEqual(
            Membership.objects.get(workspace=workspace, user=user).role,
            Membership.Role.OWNER,
        )
        self.assertTrue(
            Category.objects.filter(
                workspace=workspace,
                kind=Category.Kind.EXPENSE,
                group="Housing",
                name="Rent",
            ).exists()
        )
        self.assertTrue(
            Category.objects.filter(
                workspace=workspace,
                kind=Category.Kind.INCOME,
                group="Employment",
                name="Salary",
            ).exists()
        )
        self.assertIn("_auth_user_id", self.client.session)

    def test_login_and_logout_use_post_and_redirect_to_dashboard(self):
        User.objects.create_user(
            email="login@example.com",
            password="Strong-password-493!",
        )

        login_response = self.client.post(
            "/login/",
            {"username": "login@example.com", "password": "Strong-password-493!"},
        )
        self.assertRedirects(login_response, "/")

        logout_response = self.client.post("/logout/")
        self.assertRedirects(logout_response, "/login/")
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_dashboard_redirects_anonymous_users_to_login(self):
        response = self.client.get("/")

        self.assertRedirects(response, "/login/?next=/")

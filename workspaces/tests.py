import json

from django.contrib.auth import get_user_model
from django.test import TestCase

from categories.models import Category
from workspaces.models import Membership, Workspace


class WorkspaceTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            email="owner@example.com",
            password="password",
        )
        self.client.force_login(self.user)

    def test_workspace_creation_assigns_owner_membership(self):
        response = self.client.post(
            "/api/workspaces/",
            data=json.dumps({"name": "Family"}),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 201)
        workspace = Workspace.objects.get(pk=response.json()["id"])
        self.assertEqual(workspace.created_by, self.user)
        self.assertEqual(
            Membership.objects.get(workspace=workspace, user=self.user).role,
            Membership.Role.OWNER,
        )
        self.assertTrue(
            Category.objects.filter(
                workspace=workspace,
                group="Food",
                name="Groceries",
            ).exists()
        )

    def test_user_can_have_memberships_in_multiple_workspaces(self):
        first = Workspace.objects.create(name="First", created_by=self.user)
        second = Workspace.objects.create(name="Second", created_by=self.user)
        Membership.objects.create(workspace=first, user=self.user, role=Membership.Role.OWNER)
        Membership.objects.create(workspace=second, user=self.user, role=Membership.Role.VIEWER)

        response = self.client.get("/api/workspaces/")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.json()["workspaces"]), 2)

    def test_shared_workspace_form_saves_configuration_and_owner_membership(self):
        response = self.client.post(
            "/workspaces/new/",
            {
                "name": "Family finances",
                "base_currency": "qar",
                "timezone": "Asia/Qatar",
            },
        )

        self.assertRedirects(response, "/")
        workspace = Workspace.objects.get(name="Family finances")
        self.assertEqual(workspace.workspace_type, Workspace.WorkspaceType.SHARED)
        self.assertEqual(workspace.base_currency, "QAR")
        self.assertEqual(workspace.timezone, "Asia/Qatar")
        self.assertEqual(
            Membership.objects.get(workspace=workspace, user=self.user).role,
            Membership.Role.OWNER,
        )
        self.assertEqual(self.client.session["active_workspace_id"], str(workspace.pk))

    def test_shared_workspace_rejects_invalid_currency_or_timezone(self):
        response = self.client.post(
            "/workspaces/new/",
            {
                "name": "Invalid setup",
                "base_currency": "US",
                "timezone": "Not/A_Real_Zone",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertFalse(Workspace.objects.filter(name="Invalid setup").exists())

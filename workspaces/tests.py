import json

from django.contrib.auth import get_user_model
from django.test import TestCase

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

    def test_user_can_have_memberships_in_multiple_workspaces(self):
        first = Workspace.objects.create(name="First", created_by=self.user)
        second = Workspace.objects.create(name="Second", created_by=self.user)
        Membership.objects.create(workspace=first, user=self.user, role=Membership.Role.OWNER)
        Membership.objects.create(workspace=second, user=self.user, role=Membership.Role.VIEWER)

        response = self.client.get("/api/workspaces/")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.json()["workspaces"]), 2)

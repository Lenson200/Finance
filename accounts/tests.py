import json

from django.contrib.auth import get_user_model
from django.test import TestCase

from accounts.models import Account
from transactions.models import Transaction
from workspaces.models import Membership, Workspace


class FinancialEndpointPermissionTests(TestCase):
    def setUp(self):
        user_model = get_user_model()
        self.owner = user_model.objects.create_user(
            email="owner@example.com",
            password="password",
        )
        self.viewer = user_model.objects.create_user(
            email="viewer@example.com",
            password="password",
        )
        self.outsider = user_model.objects.create_user(
            email="outsider@example.com",
            password="password",
        )
        self.workspace = Workspace.objects.create(name="Home", created_by=self.owner)
        Membership.objects.create(
            workspace=self.workspace,
            user=self.owner,
            role=Membership.Role.OWNER,
        )
        Membership.objects.create(
            workspace=self.workspace,
            user=self.viewer,
            role=Membership.Role.VIEWER,
        )
        self.account = Account.objects.create(
            workspace=self.workspace,
            name="Checking",
            account_type=Account.AccountType.BANK,
            currency="USD",
            opening_balance="100.00",
        )
        self.endpoint_paths = [
            f"/api/workspaces/{self.workspace.pk}/accounts/",
            f"/api/workspaces/{self.workspace.pk}/transactions/",
            f"/api/workspaces/{self.workspace.pk}/dashboard/",
        ]

    def test_all_financial_endpoints_require_authentication(self):
        for endpoint in self.endpoint_paths:
            with self.subTest(endpoint=endpoint):
                self.assertEqual(self.client.get(endpoint).status_code, 401)

    def test_all_financial_endpoints_are_workspace_scoped(self):
        self.client.force_login(self.outsider)
        for endpoint in self.endpoint_paths:
            with self.subTest(endpoint=endpoint):
                self.assertEqual(self.client.get(endpoint).status_code, 403)

    def test_viewers_can_read_but_cannot_write_financial_data(self):
        self.client.force_login(self.viewer)
        for endpoint in self.endpoint_paths:
            with self.subTest(endpoint=endpoint):
                self.assertEqual(self.client.get(endpoint).status_code, 200)

        account_response = self.client.post(
            self.endpoint_paths[0],
            data=json.dumps({"name": "Savings", "currency": "USD"}),
            content_type="application/json",
        )
        transaction_response = self.client.post(
            self.endpoint_paths[1],
            data=json.dumps(
                {
                    "account_id": str(self.account.pk),
                    "kind": Transaction.Kind.EXPENSE,
                    "amount": "5.00",
                }
            ),
            content_type="application/json",
        )

        self.assertEqual(account_response.status_code, 403)
        self.assertEqual(transaction_response.status_code, 403)

    def test_member_can_create_records_and_dashboard_groups_by_currency(self):
        Membership.objects.create(
            workspace=self.workspace,
            user=get_user_model().objects.create_user(
                email="member@example.com",
                password="password",
            ),
            role=Membership.Role.MEMBER,
        )
        member = get_user_model().objects.get(email="member@example.com")
        self.client.force_login(member)

        response = self.client.post(
            self.endpoint_paths[1],
            data=json.dumps(
                {
                    "account_id": str(self.account.pk),
                    "kind": Transaction.Kind.INCOME,
                    "amount": "25.00",
                    "description": "Pay",
                }
            ),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 201)

        dashboard_response = self.client.get(self.endpoint_paths[2])
        self.assertEqual(dashboard_response.status_code, 200)
        self.assertEqual(
            dashboard_response.json()["balances"],
            [
                {
                    "currency": "USD",
                    "opening_balance": "100.00",
                    "income": "25.00",
                    "expenses": "0.00",
                    "balance": "125.00",
                }
            ],
        )

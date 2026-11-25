import json
import uuid
from decimal import Decimal
from unittest.mock import MagicMock, patch
from urllib.error import URLError

from django.contrib.auth import get_user_model
from django.test import TestCase

from accounts.models import Account
from transactions.models import Transaction
from workspaces.models import Currency, ExchangeRate, Membership, Workspace


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
                    "idempotency_key": str(uuid.uuid4()),
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

    def test_account_creation_rejects_uncatalogued_currency(self):
        self.client.force_login(self.owner)

        response = self.client.post(
            self.endpoint_paths[0],
            data=json.dumps(
                {
                    "name": "Unsupported",
                    "type": Account.AccountType.BANK,
                    "currency": "CAD",
                }
            ),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertFalse(Account.objects.filter(name="Unsupported").exists())

    def test_currency_catalog_can_be_extended(self):
        Currency.objects.create(code="CAD", name="Canadian dollar", symbol="$")
        self.client.force_login(self.owner)

        response = self.client.post(
            self.endpoint_paths[0],
            data=json.dumps(
                {
                    "name": "Canadian account",
                    "type": Account.AccountType.BANK,
                    "currency": "CAD",
                    "opening_balance": "0.00",
                }
            ),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.json()["currency"], "CAD")

    @patch("workspaces.rates.urlopen")
    def test_foreign_opening_balance_rate_is_fetched_and_cached(self, open_url):
        response_context = MagicMock()
        response_context.__enter__.return_value.read.return_value = json.dumps(
            {
                "result": "success",
                "base_code": "QAR",
                "rates": {"USD": 0.274725},
            }
        ).encode()
        open_url.return_value = response_context
        self.client.force_login(self.owner)
        payload = {
            "type": Account.AccountType.BANK,
            "currency": "QAR",
            "opening_balance": "100.00",
        }

        first = self.client.post(
            self.endpoint_paths[0],
            data=json.dumps({**payload, "name": "QAR account"}),
            content_type="application/json",
        )
        second = self.client.post(
            self.endpoint_paths[0],
            data=json.dumps({**payload, "name": "Another QAR account"}),
            content_type="application/json",
        )

        self.assertEqual(first.status_code, 201)
        self.assertEqual(second.status_code, 201, second.content)
        self.assertNotIn("opening_balance_rate", first.json())
        self.assertEqual(
            Account.objects.get(name="QAR account").opening_balance_rate,
            Decimal("0.274725"),
        )
        self.assertEqual(
            ExchangeRate.objects.get(
                from_currency_id="QAR",
                to_currency_id="USD",
            ).provider,
            "open.er-api.com",
        )
        open_url.assert_called_once()

    @patch("workspaces.rates.urlopen", side_effect=URLError("offline"))
    def test_foreign_opening_balance_fails_clearly_when_rate_unavailable(self, _open_url):
        self.client.force_login(self.owner)

        response = self.client.post(
            self.endpoint_paths[0],
            data=json.dumps(
                {
                    "name": "QAR account",
                    "type": Account.AccountType.BANK,
                    "currency": "QAR",
                    "opening_balance": "100.00",
                }
            ),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 503)
        self.assertIn("A current exchange rate is unavailable", response.json()["detail"])
        self.assertFalse(Account.objects.filter(name="QAR account").exists())

    def test_transaction_idempotency_key_prevents_duplicate_postings(self):
        self.client.force_login(self.owner)
        payload = {
            "account_id": str(self.account.pk),
            "kind": Transaction.Kind.EXPENSE,
            "amount": "5.00",
            "description": "Lunch",
            "idempotency_key": str(uuid.uuid4()),
        }

        first = self.client.post(
            self.endpoint_paths[1],
            data=json.dumps(payload),
            content_type="application/json",
        )
        second = self.client.post(
            self.endpoint_paths[1],
            data=json.dumps(payload),
            content_type="application/json",
        )

        self.assertEqual(first.status_code, 201)
        self.assertEqual(second.status_code, 200)
        self.assertEqual(
            Transaction.objects.filter(description="Lunch").count(),
            1,
        )
        self.assertEqual(
            Transaction.objects.get(description="Lunch").journal_entry.lines.count(),
            2,
        )

    def test_transaction_posting_requires_idempotency_key(self):
        self.client.force_login(self.owner)

        response = self.client.post(
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

        self.assertEqual(response.status_code, 400)
        self.assertFalse(Transaction.objects.exists())

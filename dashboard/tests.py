import uuid
from decimal import Decimal
import json
from unittest.mock import MagicMock, patch

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.utils import timezone

from accounts.models import Account
from categories.models import Category
from transactions.models import Transaction, TransactionTemplate
from workspaces.models import Membership, Workspace


class DashboardPageTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            email="dashboard@example.com",
            password="Strong-password-493!",
            first_name="Sam",
        )
        self.personal = Workspace.objects.create(
            name="Personal",
            created_by=self.user,
            workspace_type=Workspace.WorkspaceType.PERSONAL,
        )
        Membership.objects.create(
            workspace=self.personal,
            user=self.user,
            role=Membership.Role.OWNER,
        )
        self.client.force_login(self.user)

    def test_dashboard_displays_active_workspace_and_empty_state(self):
        response = self.client.get("/")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Hello, Sam.")
        self.assertContains(response, "Personal workspace")
        self.assertContains(response, "Add an account to see your balances")

    def test_dashboard_shows_financial_data_separately_by_currency(self):
        Account.objects.create(
            workspace=self.personal,
            name="Everyday",
            account_type=Account.AccountType.BANK,
            currency="USD",
            opening_balance="125.00",
        )

        response = self.client.get("/")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "125.00")
        self.assertContains(response, "USD")

    def test_dashboard_summarizes_this_month_income(self):
        account = Account.objects.create(
            workspace=self.personal,
            name="Everyday",
            account_type=Account.AccountType.BANK,
            currency="USD",
            opening_balance="0.00",
        )
        Transaction.objects.create(
            account=account,
            kind=Transaction.Kind.INCOME,
            amount="45.00",
            occurred_at=timezone.now(),
        )

        response = self.client.get("/")

        self.assertEqual(response.context["balances"][0]["month_income"], "45.00")

    def test_workspace_switch_only_selects_a_membership_owned_by_user(self):
        shared = Workspace.objects.create(
            name="Shared home",
            created_by=self.user,
            workspace_type=Workspace.WorkspaceType.SHARED,
        )
        Membership.objects.create(
            workspace=shared,
            user=self.user,
            role=Membership.Role.OWNER,
        )

        response = self.client.post(
            "/workspace/switch/",
            {"workspace_id": str(shared.pk)},
        )

        self.assertRedirects(response, "/")
        self.assertEqual(self.client.session["active_workspace_id"], str(shared.pk))

    def test_workspace_switch_rejects_malformed_identifier(self):
        response = self.client.post(
            "/workspace/switch/",
            {"workspace_id": "not-a-uuid"},
        )

        self.assertRedirects(response, "/", fetch_redirect_response=False)
        self.assertContains(self.client.get("/"), "Choose a valid workspace.")

    def test_primary_navigation_exposes_six_destinations(self):
        response = self.client.get("/")

        self.assertEqual(response.status_code, 200)
        for destination in ("Home", "Activity", "Accounts", "Reports", "Goals", "More"):
            with self.subTest(destination=destination):
                self.assertContains(response, f"<span>{destination}</span>", html=True)

    def test_primary_destinations_and_more_pages_render(self):
        for path in (
            "/",
            "/activity/",
            "/accounts/",
            "/reports/",
            "/goals/",
            "/more/",
            "/categories/",
            "/settings/",
        ):
            with self.subTest(path=path):
                self.assertEqual(self.client.get(path).status_code, 200)

    def test_quick_expense_posts_to_the_ledger(self):
        account = Account.objects.create(
            workspace=self.personal,
            name="Everyday",
            account_type=Account.AccountType.BANK,
            currency="USD",
            opening_balance="200.00",
        )
        groceries = Category.objects.create(
            workspace=self.personal,
            name="Groceries",
            kind=Category.Kind.EXPENSE,
        )

        response = self.client.post(
            "/activity/",
            {
                "kind": Transaction.Kind.EXPENSE,
                "account": str(account.pk),
                "category": str(groceries.pk),
                "amount": "100.00",
                "exchange_rate": "1",
                "description": "Groceries",
                "idempotency_key": str(uuid.uuid4()),
            },
        )

        self.assertRedirects(response, "/activity/")
        item = Transaction.objects.get(description="Groceries")
        self.assertEqual(item.journal_entry.lines.count(), 2)

    @patch("workspaces.rates.urlopen")
    def test_foreign_currency_quick_expense_uses_automatic_exchange_rate(self, open_url):
        response_context = MagicMock()
        response_context.__enter__.return_value.read.return_value = json.dumps(
            {
                "result": "success",
                "base_code": "QAR",
                "rates": {"USD": 0.274725},
            }
        ).encode()
        open_url.return_value = response_context
        account = Account.objects.create(
            workspace=self.personal,
            name="QAR Wallet",
            account_type=Account.AccountType.CASH,
            currency="QAR",
        )
        groceries = Category.objects.create(
            workspace=self.personal,
            name="Groceries",
            group="Food",
            kind=Category.Kind.EXPENSE,
        )

        response = self.client.post(
            "/activity/",
            {
                "kind": Transaction.Kind.EXPENSE,
                "account": str(account.pk),
                "category": str(groceries.pk),
                "amount": "45.00",
                "description": "Lunch",
                "idempotency_key": str(uuid.uuid4()),
            },
        )

        self.assertRedirects(response, "/activity/")
        item = Transaction.objects.get(description="Lunch")
        self.assertEqual(item.exchange_rate, Decimal("0.274725"))
        self.assertEqual(item.amount_in_base, Decimal("12.36"))
        open_url.assert_called_once()

    def test_activity_suggests_recent_transaction_details(self):
        account = Account.objects.create(
            workspace=self.personal,
            name="Everyday",
            account_type=Account.AccountType.BANK,
            currency="USD",
        )
        groceries = Category.objects.create(
            workspace=self.personal,
            name="Groceries",
            kind=Category.Kind.EXPENSE,
        )
        Transaction.objects.create(
            account=account,
            category=groceries,
            kind=Transaction.Kind.EXPENSE,
            amount="38.50",
            description="Groceries",
        )

        response = self.client.get("/activity/")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "USE A RECENT TRANSACTION")
        self.assertContains(response, "Groceries · Everyday · 38.50 USD")

    def test_activity_can_save_and_prefill_a_quick_entry_template(self):
        account = Account.objects.create(
            workspace=self.personal,
            name="Everyday",
            account_type=Account.AccountType.BANK,
            currency=self.personal.base_currency,
        )
        groceries = Category.objects.create(
            workspace=self.personal,
            name="Groceries",
            kind=Category.Kind.EXPENSE,
        )

        response = self.client.post(
            "/activity/",
            {
                "kind": Transaction.Kind.EXPENSE,
                "account": str(account.pk),
                "category": str(groceries.pk),
                "amount": "24.50",
                "description": "Weekly groceries",
                "save_as_template": "on",
                "template_name": "Weekly shop",
                "idempotency_key": str(uuid.uuid4()),
            },
        )

        self.assertRedirects(response, "/activity/")
        template = TransactionTemplate.objects.get(
            workspace=self.personal,
            name="Weekly shop",
        )
        self.assertEqual(template.amount, Decimal("24.50"))
        self.assertEqual(template.category, groceries)

        response = self.client.get("/activity/?template=1")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.context["transaction_form"]["amount"].value(),
            Decimal("24.50"),
        )
        self.assertEqual(
            response.context["transaction_form"]["account"].value(),
            str(account.pk),
        )
        self.assertContains(response, "Weekly shop")

    def test_floating_add_opens_four_transaction_choices(self):
        response = self.client.get("/")

        self.assertContains(response, 'aria-label="Add transaction"')
        self.assertContains(response, 'aria-labelledby="quick-actions-title"')
        for action in ("Expense", "Income", "Transfer", "Quick entry"):
            with self.subTest(action=action):
                self.assertContains(response, action)

    def test_transaction_choice_preselects_recording_form(self):
        account = Account.objects.create(
            workspace=self.personal,
            name="Cash Wallet",
            account_type=Account.AccountType.CASH,
            currency="USD",
        )

        response = self.client.get("/activity/?kind=expense#quick-entry")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.context["transaction_form"]["kind"].value(),
            Transaction.Kind.EXPENSE,
        )
        self.assertEqual(
            response.context["transaction_form"]["account"].value(),
            str(account.pk),
        )
        self.assertIn("occurred_date", response.context["transaction_form"].fields)
        self.assertNotIn("exchange_rate", response.context["transaction_form"].fields)

    def test_account_form_does_not_expose_opening_balance_rate(self):
        response = self.client.get("/accounts/")

        self.assertEqual(response.status_code, 200)
        self.assertNotIn("opening_balance_rate", response.context["account_form"].fields)
        self.assertNotContains(response, "Opening balance rate")

    @patch("workspaces.rates.urlopen")
    def test_account_form_calculates_foreign_opening_balance_rate(self, open_url):
        response_context = MagicMock()
        response_context.__enter__.return_value.read.return_value = json.dumps(
            {
                "result": "success",
                "base_code": "QAR",
                "rates": {"USD": 0.274725},
            }
        ).encode()
        open_url.return_value = response_context

        response = self.client.post(
            "/accounts/",
            {
                "name": "QAR wallet",
                "account_type": Account.AccountType.CASH,
                "currency": "QAR",
                "opening_balance": "100.00",
            },
        )

        self.assertRedirects(response, "/accounts/")
        account = Account.objects.get(name="QAR wallet")
        self.assertEqual(account.opening_balance_rate, Decimal("0.274725"))
        open_url.assert_called_once()

    def test_owner_can_update_workspace_reporting_currency(self):
        response = self.client.post(
            "/settings/",
            {"base_currency": "KES", "timezone": "Africa/Nairobi"},
        )

        self.assertRedirects(response, "/settings/")
        self.personal.refresh_from_db()
        self.assertEqual(self.personal.base_currency, "KES")
        self.assertEqual(self.personal.timezone, "Africa/Nairobi")

    def test_reporting_currency_cannot_be_changed_after_posting(self):
        account = Account.objects.create(
            workspace=self.personal,
            name="Everyday",
            account_type=Account.AccountType.BANK,
            currency="USD",
        )
        Transaction.objects.create(
            account=account,
            kind=Transaction.Kind.INCOME,
            amount="20.00",
        )

        response = self.client.post(
            "/settings/",
            {"base_currency": "KES", "timezone": "Africa/Nairobi"},
        )

        self.assertEqual(response.status_code, 200)
        self.personal.refresh_from_db()
        self.assertEqual(self.personal.base_currency, "USD")
        self.assertContains(response, "Base currency cannot change")

    def test_account_export_returns_workspace_accounts_as_csv(self):
        Account.objects.create(
            workspace=self.personal,
            name="=HYPERLINK(\"https://example.invalid\")",
            account_type=Account.AccountType.BANK,
            currency="USD",
            opening_balance="125.00",
        )

        response = self.client.get("/accounts/export.csv")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "text/csv")
        self.assertContains(response, "'=HYPERLINK")
        self.assertContains(response, "USD")

    def test_member_cannot_change_owner_settings(self):
        Membership.objects.filter(workspace=self.personal, user=self.user).update(
            role=Membership.Role.MEMBER
        )

        response = self.client.post(
            "/settings/",
            {"base_currency": "KES", "timezone": "Africa/Nairobi"},
        )

        self.assertEqual(response.status_code, 403)
        self.personal.refresh_from_db()
        self.assertEqual(self.personal.base_currency, "USD")

    def test_card_repayment_reduces_liability_without_increasing_expenses(self):
        bank = Account.objects.create(
            workspace=self.personal,
            name="Bank",
            account_type=Account.AccountType.BANK,
            currency="USD",
            opening_balance="1000.00",
        )
        card = Account.objects.create(
            workspace=self.personal,
            name="Card",
            account_type=Account.AccountType.CREDIT_CARD,
            currency="USD",
        )
        groceries = Category.objects.create(
            workspace=self.personal,
            name="Groceries",
            kind=Category.Kind.EXPENSE,
        )
        Transaction.objects.create(
            account=card,
            category=groceries,
            kind=Transaction.Kind.EXPENSE,
            amount="100.00",
        )
        Transaction.objects.create(
            account=bank,
            destination_account=card,
            kind=Transaction.Kind.TRANSFER,
            amount="40.00",
        )

        response = self.client.get("/")

        summary = response.context["reporting_summary"]
        self.assertEqual(summary["net_worth"], Decimal("900.00"))
        self.assertEqual(summary["expenses"], Decimal("100.00"))

    def test_activity_search_matches_transaction_description(self):
        account = Account.objects.create(
            workspace=self.personal,
            name="Everyday",
            account_type=Account.AccountType.BANK,
            currency="USD",
        )
        Transaction.objects.create(
            account=account,
            kind=Transaction.Kind.INCOME,
            amount="20.00",
            description="Weekend shift",
        )
        Transaction.objects.create(
            account=account,
            kind=Transaction.Kind.INCOME,
            amount="30.00",
            description="Salary",
        )

        response = self.client.get("/activity/?q=weekend")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            list(response.context["transactions"].values_list("description", flat=True)),
            ["Weekend shift"],
        )

    def test_refund_is_linked_to_expense_and_reduces_expense_reporting(self):
        account = Account.objects.create(
            workspace=self.personal,
            name="Cash Wallet",
            account_type=Account.AccountType.CASH,
            currency="USD",
            opening_balance="200.00",
        )
        groceries = Category.objects.create(
            workspace=self.personal,
            name="Groceries",
            group="Food",
            kind=Category.Kind.EXPENSE,
        )
        refunds = Category.objects.create(
            workspace=self.personal,
            name="Refunds",
            group="Other",
            kind=Category.Kind.INCOME,
        )
        purchase = Transaction.objects.create(
            account=account,
            category=groceries,
            kind=Transaction.Kind.EXPENSE,
            amount="45.00",
        )
        refund = Transaction.objects.create(
            account=account,
            category=refunds,
            refund_of=purchase,
            kind=Transaction.Kind.INCOME,
            amount="15.00",
        )

        self.assertEqual(refund.journal_entry.lines.get(credit__gt=0).category, groceries)
        response = self.client.get("/")
        self.assertEqual(response.context["reporting_summary"]["expenses"], Decimal("30.00"))
        self.assertEqual(response.context["reporting_summary"]["income"], Decimal("0.00"))

    def test_refund_cannot_exceed_original_expense(self):
        account = Account.objects.create(
            workspace=self.personal,
            name="Cash Wallet",
            account_type=Account.AccountType.CASH,
            currency="USD",
        )
        groceries = Category.objects.create(
            workspace=self.personal,
            name="Groceries",
            kind=Category.Kind.EXPENSE,
        )
        refunds = Category.objects.create(
            workspace=self.personal,
            name="Refunds",
            kind=Category.Kind.INCOME,
        )
        purchase = Transaction.objects.create(
            account=account,
            category=groceries,
            kind=Transaction.Kind.EXPENSE,
            amount="10.00",
        )

        with self.assertRaises(ValidationError):
            Transaction.objects.create(
                account=account,
                category=refunds,
                refund_of=purchase,
                kind=Transaction.Kind.INCOME,
                amount="10.01",
            )

        self.assertFalse(Transaction.objects.filter(refund_of=purchase).exists())

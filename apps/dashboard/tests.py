import uuid
from datetime import timedelta
from decimal import Decimal
import json
import re
from unittest.mock import MagicMock, patch
from zoneinfo import ZoneInfo

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import Client, TestCase
from django.utils import timezone

from apps.accounts.models import Account
from apps.categories.models import Category
from apps.transactions.models import Transaction, TransactionTemplate
from apps.workspaces.models import Membership, Workspace


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

    def test_home_is_landing_page_and_displays_active_workspace(self):
        response = self.client.get("/")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Hello, Sam.")
        self.assertContains(response, "Personal · USD")
        self.assertContains(response, "NET WORTH")
        self.assertEqual(response.context["active_nav"], "home_cards")

    def test_browser_timezone_sync_updates_valid_user_timezone(self):
        response = self.client.post(
            "/timezone/sync/",
            data=json.dumps({"timezone": "Africa/Nairobi"}),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"changed": True, "timezone": "Africa/Nairobi"})
        self.user.refresh_from_db()
        self.assertEqual(self.user.timezone, "Africa/Nairobi")
        self.personal.refresh_from_db()
        self.assertEqual(self.personal.timezone, "UTC")

    def test_browser_timezone_sync_rejects_invalid_timezone(self):
        response = self.client.post(
            "/timezone/sync/",
            data=json.dumps({"timezone": "Not/A_Timezone"}),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 400)
        self.user.refresh_from_db()
        self.assertEqual(self.user.timezone, "UTC")

    def test_browser_timezone_sync_accepts_csrf_protected_browser_request(self):
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.user)
        page = client.get("/")
        token = re.search(
            rb'id="timezone-sync-token" hidden><input type="hidden" '
            rb'name="csrfmiddlewaretoken" value="([^"]+)"',
            page.content,
        ).group(1).decode()

        response = client.post(
            "/timezone/sync/",
            data=json.dumps({"timezone": "Europe/Paris"}),
            content_type="application/json",
            HTTP_X_CSRFTOKEN=token,
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["timezone"], "Europe/Paris")

    def test_transaction_date_defaults_to_user_local_date(self):
        self.user.timezone = "Pacific/Kiritimati"
        self.user.save(update_fields=["timezone"])

        response = self.client.get("/activity/new/")

        self.assertEqual(
            response.context["transaction_form"]["occurred_date"].value(),
            timezone.localdate(timezone.now(), ZoneInfo(self.user.timezone)),
        )

    def test_transaction_form_rejects_a_future_date(self):
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
        tomorrow = timezone.localdate() + timedelta(days=1)

        response = self.client.post(
            "/activity/new/",
            {
                "kind": Transaction.Kind.EXPENSE,
                "account": str(account.pk),
                "category": str(groceries.pk),
                "amount": "10.00",
                "occurred_date": tomorrow.isoformat(),
                "idempotency_key": str(uuid.uuid4()),
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertIn(
            "Completed transactions cannot use a future date.",
            response.context["transaction_form"].errors["occurred_date"],
        )
        self.assertFalse(Transaction.objects.exists())

    def test_date_rules_table_displays_allowed_and_disallowed_future_dates(self):
        response = self.client.get("/bills-payments/")

        self.assertContains(response, "Allow future dates?")
        self.assertContains(response, "Expense entry")
        self.assertContains(response, "Scheduled payments")
        self.assertContains(response, "Investment maturity date")
        self.assertContains(response, "Planned")

    def test_dashboard_shows_financial_data_separately_by_currency(self):
        Account.objects.create(
            workspace=self.personal,
            name="Everyday",
            account_type=Account.AccountType.BANK,
            currency="USD",
            opening_balance="125.00",
        )

        response = self.client.get("/overview/")

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

        response = self.client.get("/overview/")

        self.assertEqual(response.context["balances"][0]["month_income"], "45.00")

    def test_home_restores_net_worth_income_and_expenses_cards(self):
        account = Account.objects.create(
            workspace=self.personal,
            name="Everyday",
            account_type=Account.AccountType.BANK,
            currency=self.personal.base_currency,
            opening_balance="500.00",
        )
        Transaction.objects.create(
            account=account,
            kind=Transaction.Kind.INCOME,
            amount="100.00",
            occurred_at=timezone.now(),
        )
        Transaction.objects.create(
            account=account,
            kind=Transaction.Kind.EXPENSE,
            amount="40.00",
            occurred_at=timezone.now(),
        )

        response = self.client.get("/home/")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["reporting_summary"]["net_worth"], Decimal("560.00"))
        self.assertEqual(response.context["reporting_summary"]["income"], Decimal("100.00"))
        self.assertEqual(response.context["reporting_summary"]["expenses"], Decimal("40.00"))
        self.assertContains(response, "NET WORTH")
        self.assertContains(response, "INCOME THIS MONTH")
        self.assertContains(response, "EXPENSES THIS MONTH")

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

    def test_primary_navigation_exposes_finance_destinations(self):
        response = self.client.get("/")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, '<script src="/static/js/main.js" defer></script>')
        self.assertNotContains(response, "<script>\n")
        for destination in (
            "Home",
            "Overview",
            "Transaction history",
            "Accounts",
            "Reports",
            "Bills &amp; payments",
            "Goals",
            "More",
            "Settings",
        ):
            with self.subTest(destination=destination):
                self.assertContains(response, f"<span>{destination}</span>", html=True)

    def test_primary_destinations_and_more_pages_render(self):
        for path in (
            "/",
            "/home/",
            "/overview/",
            "/activity/",
            "/activity/new/",
            "/transaction-history/",
            "/accounts/",
            "/reports/",
            "/goals/",
            "/more/",
            "/categories/",
            "/settings/",
            "/settings/accounts/",
            "/bills-payments/",
            "/service-worker.js",
        ):
            with self.subTest(path=path):
                self.assertEqual(self.client.get(path).status_code, 200)

    def test_offline_context_returns_authenticated_identity_and_csrf_token(self):
        response = self.client.get("/api/offline/context/")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["user_id"], str(self.user.pk))
        self.assertTrue(response.json()["csrf_token"])
        self.assertEqual(response["Cache-Control"], "no-store")

    def test_offline_context_requires_authentication(self):
        self.client.logout()

        response = self.client.get("/api/offline/context/")

        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json()["detail"], "Authentication is required.")

    def test_service_worker_allows_root_scope(self):
        response = self.client.get("/service-worker.js")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Service-Worker-Allowed"], "/")
        self.assertContains(response, "pocketfinance-transactions")

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
            "/activity/new/",
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

    @patch("apps.workspaces.rates.urlopen")
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
            "/activity/new/",
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

        response = self.client.get("/activity/new/")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "USE A RECENT TRANSACTION")
        self.assertContains(response, "Groceries · Everyday · 38.50 USD")
        self.assertIn("no-store", response["Cache-Control"])

    def test_transaction_history_does_not_render_recording_form(self):
        response = self.client.get("/transaction-history/")

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "QUICK RECORD")
        self.assertNotContains(response, 'id="quick-transaction-form"')
        self.assertNotContains(response, "USE A RECENT TRANSACTION")
        self.assertContains(response, "Record transaction")

    def test_recording_page_marks_recent_suggestions_and_categories_by_kind(self):
        account = Account.objects.create(
            workspace=self.personal,
            name="Everyday",
            account_type=Account.AccountType.BANK,
            currency=self.personal.base_currency,
        )
        expense = Category.objects.create(
            workspace=self.personal,
            name="Groceries",
            kind=Category.Kind.EXPENSE,
        )
        income = Category.objects.create(
            workspace=self.personal,
            name="Salary",
            kind=Category.Kind.INCOME,
        )
        Transaction.objects.create(
            account=account,
            category=expense,
            kind=Transaction.Kind.EXPENSE,
            amount="15.00",
            description="Market",
        )
        Transaction.objects.create(
            account=account,
            category=income,
            kind=Transaction.Kind.INCOME,
            amount="100.00",
            description="Pay",
        )

        response = self.client.get("/activity/new/?kind=expense")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'class="suggestion-chip recent-suggestion-chip"')
        self.assertContains(response, 'data-kind="expense"')
        self.assertContains(response, 'data-kind="income"')
        self.assertEqual(
            {item["kind"] for item in response.context["suggestions"]},
            {Transaction.Kind.EXPENSE, Transaction.Kind.INCOME},
        )
        self.assertEqual(
            response.context["offline_transaction_config"]["endpoint"],
            f"/api/workspaces/{self.personal.pk}/transactions/",
        )
        self.assertEqual(
            response.context["offline_transaction_config"]["rates"][str(account.pk)]["rate"],
            "1",
        )
        category_options = str(response.context["transaction_form"]["category"])
        self.assertIn(f'value="{expense.pk}" data-kind="expense"', category_options)
        self.assertIn(f'value="{income.pk}" data-kind="income" hidden', category_options)

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
            "/activity/new/",
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

        response = self.client.get("/activity/new/?template=1")

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

        response = self.client.get("/activity/new/?kind=expense#quick-entry")

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
        response = self.client.get("/settings/accounts/")

        self.assertEqual(response.status_code, 200)
        self.assertNotIn(
            "opening_balance_rate",
            response.context["account_form"].fields,
        )
        self.assertNotContains(response, "Opening balance rate")

    @patch("apps.workspaces.rates.urlopen")
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
            "/settings/accounts/",
            {
                "name": "QAR wallet",
                "account_type": Account.AccountType.CASH,
                "currency": "QAR",
                "opening_balance": "100.00",
            },
        )

        self.assertRedirects(response, "/settings/accounts/")
        account = Account.objects.get(name="QAR wallet")
        self.assertEqual(account.opening_balance_rate, Decimal("0.274725"))
        open_url.assert_called_once()

    def test_account_management_shares_account_with_a_workspace_member(self):
        member = get_user_model().objects.create_user(
            email="account-member@example.com",
            password="Strong-password-493!",
        )
        Membership.objects.create(
            workspace=self.personal,
            user=member,
            role=Membership.Role.MEMBER,
        )
        account = Account.objects.create(
            workspace=self.personal,
            name="Private checking",
            account_type=Account.AccountType.BANK,
            currency=self.personal.base_currency,
        )
        Transaction.objects.create(
            account=account,
            kind=Transaction.Kind.INCOME,
            amount="25.00",
            description="Shared account deposit",
        )

        response = self.client.post(
            "/settings/accounts/",
            {
                "action": "share",
                "account": str(account.pk),
                "membership": str(Membership.objects.get(user=member).pk),
                "can_write": "on",
            },
        )

        self.assertRedirects(response, "/settings/accounts/")
        self.client.force_login(member)
        self.assertContains(self.client.get("/accounts/"), "Private checking")
        history_response = self.client.get("/transaction-history/")
        self.assertContains(history_response, "Shared account deposit")
        self.assertContains(history_response, "Private checking")

    def test_overview_calculates_available_balance_from_cash_bank_and_short_debt(self):
        bank = Account.objects.create(
            workspace=self.personal,
            name="Checking",
            account_type=Account.AccountType.BANK,
            currency=self.personal.base_currency,
            opening_balance="1000.00",
        )
        Account.objects.create(
            workspace=self.personal,
            name="Long loan",
            account_type=Account.AccountType.DEBT,
            debt_term=Account.DebtTerm.LONG,
            currency=self.personal.base_currency,
            opening_balance="500.00",
        )
        short_debt = Account.objects.create(
            workspace=self.personal,
            name="Card",
            account_type=Account.AccountType.CREDIT_CARD,
            debt_term=Account.DebtTerm.SHORT,
            currency=self.personal.base_currency,
            opening_balance="100.00",
        )

        response = self.client.get("/overview/")

        self.assertEqual(response.context["reporting_summary"]["available_cash"], Decimal("900.00"))
        self.assertEqual(response.context["reporting_summary"]["long_term_debt"], Decimal("500.00"))
        self.assertContains(response, "AVAILABLE BALANCE")

    def test_available_balance_includes_converted_mobile_money(self):
        self.personal.base_currency = "QAR"
        self.personal.save()
        Account.objects.create(
            workspace=self.personal,
            name="Wallet",
            account_type=Account.AccountType.MOBILE_MONEY,
            currency="KES",
            opening_balance="5000.00",
            opening_balance_rate="0.028004",
        )
        card = Account.objects.create(
            workspace=self.personal,
            name="Card",
            account_type=Account.AccountType.CREDIT_CARD,
            currency="QAR",
            opening_balance="400.00",
        )
        Transaction.objects.create(
            account=card,
            kind=Transaction.Kind.EXPENSE,
            amount="300.00",
            description="Entertainment",
        )
        Transaction.objects.create(
            account=card,
            kind=Transaction.Kind.EXPENSE,
            amount="11.00",
            description="Netflix",
        )
        wallet = Account.objects.get(name="Wallet")
        for amount in ("6000.01", "50000.00", "50000.00"):
            Transaction.objects.create(
                account=wallet,
                kind=Transaction.Kind.INCOME,
                amount=amount,
                exchange_rate="0.028004",
            )

        response = self.client.get("/overview/")

        summary = response.context["reporting_summary"]
        self.assertEqual(summary["short_term_debt"], Decimal("711.00"))
        self.assertEqual(summary["available_cash"], Decimal("2397.44"))
        self.assertContains(response, "Bank, cash &amp; mobile money − short-term debt")

    def test_account_detail_shows_opening_balance_and_exports_full_history(self):
        self.personal.base_currency = "QAR"
        self.personal.save()
        card = Account.objects.create(
            workspace=self.personal,
            name="Test 3",
            account_type=Account.AccountType.CREDIT_CARD,
            currency="QAR",
            opening_balance="400.00",
        )
        Transaction.objects.create(
            account=card,
            kind=Transaction.Kind.EXPENSE,
            amount="300.00",
            description="went to have fun with friends",
        )
        Transaction.objects.create(
            account=card,
            kind=Transaction.Kind.EXPENSE,
            amount="11.00",
            description="paid my netflix",
        )

        listing = self.client.get("/accounts/")
        detail = self.client.get(f"/accounts/{card.pk}/")
        filtered = self.client.get(
            f"/accounts/{card.pk}/",
            {"q": "netflix"},
        )
        export = self.client.get(
            f"/accounts/{card.pk}/transactions.csv",
        )

        self.assertContains(listing, f'href="/accounts/{card.pk}/"')
        self.assertEqual(detail.status_code, 200)
        self.assertEqual(detail.context["balance"], Decimal("711.00"))
        self.assertEqual(len(detail.context["transactions"]), 2)
        self.assertContains(detail, "400.00")
        self.assertContains(detail, "711.00")
        self.assertEqual(len(filtered.context["transactions"]), 1)
        self.assertContains(filtered, "paid my netflix")
        self.assertNotContains(filtered, "went to have fun with friends")
        self.assertEqual(export.status_code, 200)
        self.assertContains(export, "went to have fun with friends")
        self.assertContains(export, "paid my netflix")

    def test_account_list_filters_by_name_type_and_currency(self):
        Account.objects.create(
            workspace=self.personal,
            name="Travel wallet",
            account_type=Account.AccountType.CASH,
            currency="USD",
        )
        Account.objects.create(
            workspace=self.personal,
            name="QAR card",
            account_type=Account.AccountType.CREDIT_CARD,
            currency="QAR",
        )

        response = self.client.get(
            "/accounts/",
            {"q": "QAR", "type": Account.AccountType.CREDIT_CARD, "currency": "QAR"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "QAR card")
        self.assertNotContains(response, "Travel wallet")

    def test_overview_applies_custom_date_range_and_renders_graphs(self):
        account = Account.objects.create(
            workspace=self.personal,
            name="Checking",
            account_type=Account.AccountType.BANK,
            currency=self.personal.base_currency,
        )
        groceries = Category.objects.create(
            workspace=self.personal,
            name="Groceries",
            kind=Category.Kind.EXPENSE,
        )
        today = timezone.localdate()
        Transaction.objects.create(
            account=account,
            kind=Transaction.Kind.EXPENSE,
            category=groceries,
            amount="20.00",
            occurred_at=timezone.now(),
        )
        Transaction.objects.create(
            account=account,
            kind=Transaction.Kind.INCOME,
            amount="75.00",
            occurred_at=timezone.now() - timedelta(days=45),
        )

        response = self.client.get(
            "/overview/",
            {
                "range": "custom",
                "from": (today - timedelta(days=2)).isoformat(),
                "to": today.isoformat(),
            },
        )

        self.assertEqual(response.context["reporting_summary"]["expenses"], Decimal("20.00"))
        self.assertEqual(response.context["reporting_summary"]["income"], Decimal("0.00"))
        self.assertContains(response, "Income graph for selected date range")
        self.assertContains(response, "Spending graph for selected date range")
        self.assertContains(response, "Cashflow graph for selected date range")

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

from decimal import Decimal
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.utils import timezone

from apps.accounts.models import Account
from apps.categories.models import Category
from apps.transactions.models import JournalLine, Transaction
from apps.workspaces.models import Membership, Workspace


class LedgerPostingTests(TestCase):
    def setUp(self):
        user = get_user_model().objects.create_user(
            email="ledger@example.com",
            password="Strong-password-493!",
        )
        self.workspace = Workspace.objects.create(
            name="Home",
            created_by=user,
            base_currency="QAR",
        )
        Membership.objects.create(
            workspace=self.workspace,
            user=user,
            role=Membership.Role.OWNER,
        )
        self.bank = Account.objects.create(
            workspace=self.workspace,
            name="Bank",
            account_type=Account.AccountType.BANK,
            currency="QAR",
            opening_balance="500.00",
        )
        self.card = Account.objects.create(
            workspace=self.workspace,
            name="Credit card",
            account_type=Account.AccountType.CREDIT_CARD,
            currency="QAR",
        )
        self.groceries = Category.objects.create(
            workspace=self.workspace,
            name="Groceries",
            kind=Category.Kind.EXPENSE,
        )

    def assert_entry_balances(self, item):
        lines = item.journal_entry.lines.all()
        self.assertEqual(lines.count(), 2)
        self.assertEqual(
            sum((line.debit for line in lines), Decimal("0.00")),
            sum((line.credit for line in lines), Decimal("0.00")),
        )

    def test_expense_and_card_repayment_post_balanced_lines_once(self):
        purchase = Transaction.objects.create(
            account=self.card,
            category=self.groceries,
            kind=Transaction.Kind.EXPENSE,
            amount="100.00",
            description="Groceries",
        )
        repayment = Transaction.objects.create(
            account=self.bank,
            destination_account=self.card,
            kind=Transaction.Kind.TRANSFER,
            amount="40.00",
            description="Card repayment",
        )

        self.assert_entry_balances(purchase)
        self.assert_entry_balances(repayment)
        self.assertEqual(purchase.journal_entry.lines.get(debit__gt=0).category, self.groceries)
        self.assertEqual(repayment.journal_entry.lines.get(debit__gt=0).account, self.card)
        self.assertEqual(
            Transaction.objects.filter(kind=Transaction.Kind.EXPENSE).count(),
            1,
        )

    def test_foreign_currency_transaction_keeps_recorded_rate_and_base_value(self):
        usd = Account.objects.create(
            workspace=self.workspace,
            name="USD wallet",
            account_type=Account.AccountType.CASH,
            currency="USD",
            opening_balance="10.00",
            opening_balance_rate="3.64",
        )
        income_category = Category.objects.create(
            workspace=self.workspace,
            name="Salary",
            kind=Category.Kind.INCOME,
        )
        item = Transaction.objects.create(
            account=usd,
            category=income_category,
            kind=Transaction.Kind.INCOME,
            amount="25.00",
            exchange_rate="3.64",
        )

        self.assertEqual(item.exchange_rate, Decimal("3.64000000"))
        self.assertEqual(item.amount_in_base, Decimal("91.00"))
        self.assertEqual(item.journal_entry.currency, "QAR")
        self.assert_entry_balances(item)

    def test_invalid_transfer_is_not_posted(self):
        item = Transaction(
            account=self.bank,
            kind=Transaction.Kind.TRANSFER,
            amount="10.00",
        )

        with self.assertRaises(ValidationError):
            item.save()
        self.assertEqual(Transaction.objects.count(), 0)
        self.assertEqual(JournalLine.objects.count(), 0)

    def test_future_dated_transactions_cannot_be_posted_to_the_ledger(self):
        item = Transaction(
            account=self.bank,
            category=self.groceries,
            kind=Transaction.Kind.EXPENSE,
            amount="10.00",
            occurred_at=timezone.now() + timedelta(days=1),
        )

        with self.assertRaises(ValidationError):
            item.save()
        self.assertEqual(Transaction.objects.count(), 0)
        self.assertEqual(JournalLine.objects.count(), 0)

    def test_foreign_currency_transaction_requires_an_exchange_rate(self):
        usd = Account.objects.create(
            workspace=self.workspace,
            name="USD wallet",
            account_type=Account.AccountType.CASH,
            currency="USD",
        )
        item = Transaction(
            account=usd,
            kind=Transaction.Kind.EXPENSE,
            amount="10.00",
        )

        with self.assertRaises(ValidationError):
            item.save()
        self.assertEqual(Transaction.objects.count(), 0)

    def test_foreign_currency_opening_balance_requires_an_exchange_rate(self):
        account = Account(
            workspace=self.workspace,
            name="USD wallet",
            account_type=Account.AccountType.CASH,
            currency="USD",
            opening_balance="10.00",
        )

        with self.assertRaises(ValidationError):
            account.save()

import csv
from decimal import Decimal
from zoneinfo import ZoneInfo
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.db.models import Q
from django.http import HttpResponse
from django.shortcuts import redirect, render
from django.utils import timezone
from django.views.decorators.http import require_http_methods
from accounts.forms import AccountForm
from accounts.models import Account
from categories.forms import CategoryForm
from categories.models import Category
from transactions.forms import TransactionForm
from transactions.models import Transaction, TransactionTemplate
from workspaces.models import Currency, Membership
from workspaces.permissions import ROLE_LEVELS
from workspaces.rates import ExchangeRateUnavailable
from workspaces.settings_forms import WorkspaceSettingsForm


def _active_membership(request):
    memberships = list(
        Membership.objects.filter(user=request.user)
        .select_related("workspace")
        .order_by("created_at")
    )
    active_id = request.session.get("active_workspace_id")
    return next(
        (item for item in memberships if str(item.workspace_id) == active_id),
        memberships[0] if memberships else None,
    )


def _can_write(membership):
    return bool(
        membership
        and ROLE_LEVELS.get(membership.role, -1)
        >= ROLE_LEVELS[Membership.Role.MEMBER]
    )


def _account_balance(account, transactions):
    balance = account.opening_balance
    for item in transactions:
        if item.kind == Transaction.Kind.INCOME and item.account_id == account.pk:
            balance += (
                -item.amount
                if account.account_type == Account.AccountType.CREDIT_CARD
                else item.amount
            )
        elif item.kind == Transaction.Kind.EXPENSE and item.account_id == account.pk:
            balance += (
                item.amount
                if account.account_type == Account.AccountType.CREDIT_CARD
                else -item.amount
            )
        elif item.kind == Transaction.Kind.TRANSFER:
            if item.account_id == account.pk:
                balance -= item.amount
            elif (
                item.destination_account_id == account.pk
                and account.account_type == Account.AccountType.CREDIT_CARD
            ):
                balance -= item.amount
            elif item.destination_account_id == account.pk:
                balance += item.amount
    return balance


def _csv_text(value):
    value = str(value)
    if value.lstrip(" \t\r\n").startswith(("=", "+", "-", "@")):
        return f"'{value}"
    return value


def _reporting_summary(workspace, *, since=None):
    accounts = list(Account.objects.filter(workspace=workspace))
    transactions = list(
        Transaction.objects.filter(account__workspace=workspace)
        .select_related(
            "account",
            "destination_account",
            "category",
            "refund_of__category",
        )
    )
    with timezone.override(ZoneInfo(workspace.timezone)):
        current_month = timezone.localdate().replace(day=1)
        if current_month.month == 12:
            next_month = current_month.replace(year=current_month.year + 1, month=1)
        else:
            next_month = current_month.replace(month=current_month.month + 1)
        if since is None:
            since = current_month
        month_transactions = [
            item
            for item in transactions
            if since <= timezone.localtime(item.occurred_at).date() < next_month
        ]

    account_balances = {}
    for account in accounts:
        account_balances[account.pk] = account.opening_balance * account.opening_balance_rate
    for item in transactions:
        amount = item.amount_in_base
        account = item.account
        if item.kind == Transaction.Kind.INCOME:
            account_balances[account.pk] += (
                -amount
                if account.account_type == Account.AccountType.CREDIT_CARD
                else amount
            )
        elif item.kind == Transaction.Kind.EXPENSE:
            account_balances[account.pk] += (
                amount
                if account.account_type == Account.AccountType.CREDIT_CARD
                else -amount
            )
        else:
            account_balances[account.pk] -= amount
            destination = item.destination_account
            account_balances[destination.pk] += (
                -amount
                if destination.account_type == Account.AccountType.CREDIT_CARD
                else amount
            )

    net_worth = sum(
        (
            balance
            if account.account_type != Account.AccountType.CREDIT_CARD
            else -balance
            for account in accounts
            for balance in [account_balances[account.pk]]
        ),
        Decimal("0.00"),
    )
    income = sum(
        (
            item.amount_in_base
            for item in month_transactions
            if item.kind == Transaction.Kind.INCOME and not item.refund_of_id
        ),
        Decimal("0.00"),
    )
    expenses = sum(
        (
            item.amount_in_base * (-1 if item.refund_of_id else 1)
            for item in month_transactions
            if item.kind == Transaction.Kind.EXPENSE or item.refund_of_id
        ),
        Decimal("0.00"),
    )
    return {
        "net_worth": net_worth,
        "income": income,
        "expenses": expenses,
        "currency": workspace.base_currency,
        "accounts": accounts,
        "account_balances": account_balances,
        "transactions": transactions,
    }


@login_required
@require_http_methods(["GET", "POST"])
def activity(request):
    membership = _active_membership(request)
    if request.method == "POST" and not _can_write(membership):
        return HttpResponse("Workspace write access is required.", status=403)
    form = None
    if membership and _can_write(membership):
        initial = {}
        suggestions = _transaction_suggestions(membership.workspace)
        quick_templates = list(
            TransactionTemplate.objects.filter(
                workspace=membership.workspace
            ).select_related("account", "destination_account", "category")
        )
        kind = request.GET.get("kind")
        if kind in Transaction.Kind.values:
            initial["kind"] = kind
        else:
            initial["kind"] = Transaction.Kind.EXPENSE
        first_account = Account.objects.filter(workspace=membership.workspace).first()
        if first_account:
            initial["account"] = str(first_account.pk)
        if request.GET.get("template") == "1" and (
            quick_templates or suggestions
        ):
            if quick_templates:
                template = quick_templates[0]
                initial.update(
                    kind=template.kind,
                    account=str(template.account_id),
                    destination_account=str(template.destination_account_id or ""),
                    category=str(template.category_id or ""),
                    amount=template.amount,
                    description=template.description,
                )
            else:
                suggestion = suggestions[0]
                initial.update(
                    kind=suggestion["kind"],
                    account=suggestion["account_id"],
                    category=suggestion["category_id"],
                    refund_of=suggestion["refund_of_id"],
                    amount=suggestion["amount"],
                    description=suggestion["description"],
                )
        form = TransactionForm(
            request.POST or None,
            workspace=membership.workspace,
            initial=initial,
        )
        if request.method == "POST" and form.is_valid():
            try:
                form.save()
            except ValidationError as error:
                form.add_error(None, error)
            else:
                messages.success(request, "Transaction recorded.")
                return redirect("dashboard:activity")

    search = request.GET.get("q", "").strip()
    transactions = Transaction.objects.none()
    if membership:
        transactions = Transaction.objects.filter(
            account__workspace=membership.workspace
        ).select_related(
            "account",
            "destination_account",
            "category",
            "refund_of__category",
        )
        if search:
            transactions = transactions.filter(
                Q(description__icontains=search)
                | Q(kind__icontains=search)
                | Q(account__name__icontains=search)
                | Q(destination_account__name__icontains=search)
                | Q(category__name__icontains=search)
            ).distinct()
    return render(
        request,
        "dashboard/activity.html",
        {
            "membership": membership,
            "transaction_form": form,
            "transactions": transactions,
            "search": search,
            "active_nav": "activity",
            "suggestions": (
                _transaction_suggestions(membership.workspace) if membership else []
            ),
            "category_groups": _category_groups(membership.workspace)
            if membership
            else [],
            "quick_templates": (
                quick_templates if membership and _can_write(membership) else []
            ),
            "account_currencies": {
                str(account.pk): account.currency
                for account in Account.objects.filter(workspace=membership.workspace)
                .only("pk", "currency")
            }
            if membership
            else {},
        },
    )


def _transaction_suggestions(workspace):
    recent = (
        Transaction.objects.filter(
            account__workspace=workspace,
            kind__in=(Transaction.Kind.INCOME, Transaction.Kind.EXPENSE),
        )
        .select_related("account", "category")
        .order_by("-occurred_at")[:30]
    )
    suggestions = []
    seen = set()
    for item in recent:
        key = (item.kind, item.account_id, item.category_id)
        if key in seen:
            continue
        seen.add(key)
        suggestions.append(
            {
                "kind": item.kind,
                "kind_label": item.get_kind_display(),
                "account_id": item.account_id,
                "account_name": item.account.name,
                "category_id": item.category_id,
                "refund_of_id": item.refund_of_id or "",
                "category_name": item.category.name if item.category else "Other",
                "amount": item.amount,
                "description": item.description,
                "exchange_rate": item.exchange_rate,
                "currency": item.account.currency,
            }
        )
        if len(suggestions) == 5:
            break
    return suggestions


def _category_groups(workspace):
    grouped = {}
    categories = Category.objects.filter(workspace=workspace).order_by(
        "kind", "group", "name"
    )
    for category in categories:
        group_name = category.group or "Other"
        grouped.setdefault((category.kind, group_name), []).append(category)
    return [
        {"kind": kind, "name": name, "categories": items}
        for (kind, name), items in grouped.items()
    ]


@login_required
@require_http_methods(["GET", "POST"])
def accounts_page(request):
    membership = _active_membership(request)
    if request.method == "POST" and not _can_write(membership):
        return HttpResponse("Workspace write access is required.", status=403)
    accounts = (
        list(Account.objects.filter(workspace=membership.workspace))
        if membership
        else []
    )
    transactions = (
        list(
            Transaction.objects.filter(account__workspace=membership.workspace)
            .select_related("account", "destination_account")
        )
        if membership
        else []
    )
    form = None
    if membership and _can_write(membership):
        form = AccountForm(request.POST or None, workspace=membership.workspace)
        if request.method == "POST" and form.is_valid():
            try:
                form.save()
            except ExchangeRateUnavailable as error:
                form.add_error(None, str(error))
            except ValidationError as error:
                form.add_error(None, error)
            else:
                messages.success(request, "Account added.")
                return redirect("dashboard:accounts")

    return render(
        request,
        "dashboard/accounts.html",
        {
            "membership": membership,
            "account_form": form,
            "account_rows": [
                (account, _account_balance(account, transactions))
                for account in accounts
            ],
            "active_nav": "accounts",
        },
    )


@login_required
def export_accounts(request):
    membership = _active_membership(request)
    if not membership:
        return HttpResponse("Workspace membership required.", status=403)
    response = HttpResponse(content_type="text/csv")
    response["Content-Disposition"] = 'attachment; filename="accounts.csv"'
    writer = csv.writer(response)
    writer.writerow(
        ["Name", "Type", "Currency", "Balance", "Opening balance"]
    )
    accounts = list(Account.objects.filter(workspace=membership.workspace))
    transactions = list(
        Transaction.objects.filter(account__workspace=membership.workspace)
        .select_related("account", "destination_account")
    )
    for account in accounts:
        writer.writerow(
            [
                _csv_text(account.name),
                _csv_text(account.get_account_type_display()),
                _csv_text(account.currency),
                _account_balance(account, transactions),
                account.opening_balance,
            ]
        )
    return response


@login_required
@require_http_methods(["GET", "POST"])
def categories_page(request):
    membership = _active_membership(request)
    if request.method == "POST" and not _can_write(membership):
        return HttpResponse("Workspace write access is required.", status=403)
    form = None
    if membership and _can_write(membership):
        form = CategoryForm(request.POST or None, workspace=membership.workspace)
        if request.method == "POST" and form.is_valid():
            form.save()
            messages.success(request, "Category added.")
            return redirect("dashboard:categories")
    categories = (
        Category.objects.filter(workspace=membership.workspace)
        if membership
        else Category.objects.none()
    )
    grouped_categories = _category_groups(membership.workspace) if membership else []
    return render(
        request,
        "dashboard/categories.html",
        {
            "membership": membership,
            "category_form": form,
            "categories": categories,
            "category_groups": grouped_categories,
            "active_nav": "more",
        },
    )


@login_required
def reports(request):
    membership = _active_membership(request)
    summary = _reporting_summary(membership.workspace) if membership else None
    expense_categories = []
    if summary:
        totals = {}
        for item in summary["transactions"]:
            if item.kind == Transaction.Kind.EXPENSE:
                name = item.category.name if item.category else "Uncategorized"
                totals[name] = totals.get(name, Decimal("0.00")) + item.amount_in_base
            elif item.refund_of_id:
                name = (
                    item.refund_of.category.name
                    if item.refund_of.category
                    else "Uncategorized"
                )
                totals[name] = totals.get(name, Decimal("0.00")) - item.amount_in_base
        expense_categories = sorted(totals.items(), key=lambda row: row[1], reverse=True)
    return render(
        request,
        "dashboard/reports.html",
        {
            "membership": membership,
            "summary": summary,
            "expense_categories": expense_categories,
            "active_nav": "reports",
        },
    )


@login_required
def goals(request):
    return render(
        request,
        "dashboard/goals.html",
        {"membership": _active_membership(request), "active_nav": "goals"},
    )


@login_required
def more(request):
    return render(
        request,
        "dashboard/more.html",
        {"membership": _active_membership(request), "active_nav": "more"},
    )


@login_required
@require_http_methods(["GET", "POST"])
def settings(request):
    membership = _active_membership(request)
    if not membership:
        return redirect("dashboard:home")
    if membership.role != Membership.Role.OWNER:
        return HttpResponse("Only the workspace owner can change settings.", status=403)
    form = WorkspaceSettingsForm(
        request.POST or None,
        instance=membership.workspace,
    )
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "Workspace settings updated.")
        return redirect("dashboard:settings")
    return render(
        request,
        "dashboard/settings.html",
        {
            "membership": membership,
            "settings_form": form,
            "active_nav": "more",
        },
    )

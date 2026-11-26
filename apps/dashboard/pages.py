import csv
from calendar import monthrange
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from math import ceil
from zoneinfo import ZoneInfo
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.db.models import Q
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_http_methods
from apps.accounts.access import accounts_for_membership
from apps.accounts.forms import AccountAccessForm, AccountForm
from apps.accounts.models import Account, AccountAccess
from apps.categories.forms import CategoryForm
from apps.categories.models import Category
from apps.dashboard.forms import GoalForm, GoalProgressForm
from apps.dashboard.models import Goal
from apps.transactions.forms import TransactionForm
from apps.transactions.models import Transaction, TransactionTemplate
from apps.transactions.access import transactions_for_membership
from apps.workspaces.models import Currency, ExchangeRate, Membership
from apps.workspaces.permissions import ROLE_LEVELS
from apps.workspaces.rates import ExchangeRateUnavailable, get_exchange_rate
from apps.workspaces.settings_forms import WorkspaceSettingsForm


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
                if account.is_liability
                else item.amount
            )
        elif item.kind == Transaction.Kind.EXPENSE and item.account_id == account.pk:
            balance += (
                item.amount
                if account.is_liability
                else -item.amount
            )
        elif item.kind == Transaction.Kind.TRANSFER:
            if item.account_id == account.pk:
                balance -= item.amount
            elif (
                item.destination_account_id == account.pk
                and account.is_liability
            ):
                balance -= item.amount
            elif item.destination_account_id == account.pk:
                balance += item.amount
    return balance


def _account_balance_in_base(account, transactions):
    balance = account.opening_balance * account.opening_balance_rate
    for item in transactions:
        if item.kind == Transaction.Kind.INCOME and item.account_id == account.pk:
            balance += -item.amount_in_base if account.is_liability else item.amount_in_base
        elif item.kind == Transaction.Kind.EXPENSE and item.account_id == account.pk:
            balance += item.amount_in_base if account.is_liability else -item.amount_in_base
        elif item.kind == Transaction.Kind.TRANSFER:
            if item.account_id == account.pk:
                balance -= item.amount_in_base
            elif item.destination_account_id == account.pk:
                balance += (
                    -item.amount_in_base if account.is_liability else item.amount_in_base
                )
    return balance


def _csv_text(value):
    value = str(value)
    if value.lstrip(" \t\r\n").startswith(("=", "+", "-", "@")):
        return f"'{value}"
    return value


def _reporting_summary(workspace, *, membership, since=None, until=None):
    accounts = list(accounts_for_membership(membership))
    transactions = list(
        transactions_for_membership(membership)
        .select_related(
            "account",
            "destination_account",
            "category",
            "refund_of__category",
        )
    )
    with timezone.override(ZoneInfo(workspace.timezone)):
        today = timezone.localdate()
        if since is None:
            since = today.replace(day=1)
        if until is None:
            until = today
        range_transactions = [
            item
            for item in transactions
            if since <= timezone.localtime(item.occurred_at).date() <= until
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
                if account.is_liability
                else amount
            )
        elif item.kind == Transaction.Kind.EXPENSE:
            account_balances[account.pk] += (
                amount
                if account.is_liability
                else -amount
            )
        else:
            destination = item.destination_account
            if account.pk in account_balances:
                account_balances[account.pk] -= amount
            if destination.pk in account_balances:
                account_balances[destination.pk] += (
                    -amount
                    if destination.is_liability
                    else amount
                )

    net_worth = sum(
        (
            balance
            if not account.is_liability
            else -balance
            for account in accounts
            for balance in [account_balances[account.pk]]
        ),
        Decimal("0.00"),
    )
    income = sum(
        (
            item.amount_in_base
            for item in range_transactions
            if item.kind == Transaction.Kind.INCOME and not item.refund_of_id
        ),
        Decimal("0.00"),
    )
    expenses = sum(
        (
            item.amount_in_base * (-1 if item.refund_of_id else 1)
            for item in range_transactions
            if item.kind == Transaction.Kind.EXPENSE or item.refund_of_id
        ),
        Decimal("0.00"),
    )
    available_cash = sum(
        (
            account_balances[account.pk]
            for account in accounts
            if account.account_type
            in (
                Account.AccountType.BANK,
                Account.AccountType.CASH,
                Account.AccountType.MOBILE_MONEY,
            )
        ),
        Decimal("0.00"),
    )
    short_term_debt = sum(
        (
            account_balances[account.pk]
            for account in accounts
            if account.is_liability and account.debt_term == Account.DebtTerm.SHORT
        ),
        Decimal("0.00"),
    )
    long_term_debt = sum(
        (
            account_balances[account.pk]
            for account in accounts
            if account.is_liability and account.debt_term == Account.DebtTerm.LONG
        ),
        Decimal("0.00"),
    )
    with timezone.override(ZoneInfo(workspace.timezone)):
        graph_periods = _cashflow_periods(since, until, range_transactions)
    max_amount = max(
        (max(item["income"], item["expenses"]) for item in graph_periods),
        default=Decimal("0.00"),
    )
    max_cashflow = max(
        (abs(item["cashflow"]) for item in graph_periods),
        default=Decimal("0.00"),
    )
    for item in graph_periods:
        item["income_height"] = _chart_height(item["income"], max_amount)
        item["expense_height"] = _chart_height(item["expenses"], max_amount)
        item["cashflow_height"] = _chart_height(abs(item["cashflow"]), max_cashflow)
    return {
        "net_worth": net_worth,
        "available_cash": available_cash - short_term_debt,
        "short_term_debt": short_term_debt,
        "long_term_debt": long_term_debt,
        "income": income,
        "expenses": expenses,
        "currency": workspace.base_currency,
        "since": since,
        "until": until,
        "accounts": accounts,
        "account_balances": account_balances,
        "transactions": transactions,
        "range_transactions": range_transactions,
        "graph_periods": graph_periods,
        "recent_transactions": transactions[:8],
    }


def _chart_height(value, maximum):
    if value <= 0 or maximum <= 0:
        return 0
    return max(3, round(value / maximum * 100))


def _cashflow_periods(since, until, transactions):
    days = (until - since).days + 1
    periods = []
    if days <= 31:
        current = since
        while current <= until:
            periods.append(
                {"start": current, "end": current, "label": current.strftime("%d %b")}
            )
            current += timedelta(days=1)
        bucket_for = lambda item_date: (item_date - since).days
    elif days <= 100:
        current = since
        while current <= until:
            end = min(current + timedelta(days=6), until)
            periods.append(
                {"start": current, "end": end, "label": current.strftime("%d %b")}
            )
            current = end + timedelta(days=1)
        bucket_for = lambda item_date: (item_date - since).days // 7
    else:
        current = since.replace(day=1)
        while current <= until:
            if current.month == 12:
                next_month = current.replace(year=current.year + 1, month=1)
            else:
                next_month = current.replace(month=current.month + 1)
            end = min(next_month - timedelta(days=1), until)
            periods.append(
                {"start": current, "end": end, "label": current.strftime("%b %y")}
            )
            current = next_month
        bucket_for = lambda item_date: (item_date.year - since.year) * 12 + item_date.month - since.month

    for period in periods:
        period.update(
            income=Decimal("0.00"),
            expenses=Decimal("0.00"),
            cashflow=Decimal("0.00"),
        )
    for item in transactions:
        item_date = timezone.localtime(item.occurred_at).date()
        index = bucket_for(item_date)
        if not 0 <= index < len(periods):
            continue
        if item.kind == Transaction.Kind.INCOME and not item.refund_of_id:
            periods[index]["income"] += item.amount_in_base
        elif item.kind == Transaction.Kind.EXPENSE:
            periods[index]["expenses"] += item.amount_in_base
        elif item.refund_of_id:
            periods[index]["expenses"] -= item.amount_in_base
    for period in periods:
        period["cashflow"] = period["income"] - period["expenses"]
    return periods


@login_required
@never_cache
@require_http_methods(["GET", "POST"])
def record_transaction(request):
    membership = _active_membership(request)
    if request.method == "POST" and not _can_write(membership):
        return HttpResponse("Workspace write access is required.", status=403)
    form = None
    suggestions = []
    quick_templates = []
    if membership and _can_write(membership):
        initial = {}
        writable_accounts = accounts_for_membership(membership, write=True)
        suggestions = _transaction_suggestions(membership, writable_accounts)
        quick_templates = list(
            TransactionTemplate.objects.filter(
                workspace=membership.workspace
            )
            .filter(account__in=writable_accounts)
            .filter(
                Q(destination_account__isnull=True)
                | Q(destination_account__in=writable_accounts)
            ).select_related("account", "destination_account", "category")
        )
        kind = request.GET.get("kind")
        if kind in Transaction.Kind.values:
            initial["kind"] = kind
        else:
            initial["kind"] = Transaction.Kind.EXPENSE
        first_account = writable_accounts.first()
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
            membership=membership,
            initial=initial,
        )
        base_currency = membership.workspace.base_currency
        account_rates = {}
        currency_rates = {base_currency: {"rate": "1", "fetched_at": None}}
        for currency in set(writable_accounts.values_list("currency", flat=True)):
            if currency == base_currency:
                continue
            try:
                rate = get_exchange_rate(currency, base_currency)
            except ExchangeRateUnavailable:
                continue
            exchange_rate = ExchangeRate.objects.get(
                from_currency_id=currency,
                to_currency_id=base_currency,
            )
            currency_rates[currency] = {
                "rate": str(rate),
                "fetched_at": exchange_rate.fetched_at.isoformat(),
            }
        account_rates = {
            str(account.pk): currency_rates[account.currency]
            for account in writable_accounts
            if account.currency in currency_rates
        }
        if request.method == "POST" and form.is_valid():
            try:
                form.save()
            except ValidationError as error:
                if hasattr(error, "error_dict"):
                    for field, errors in error.error_dict.items():
                        for err in errors:
                            if field in form.fields:
                                form.add_error(field, err)
                            else:
                                form.add_error(None, f"{field.replace('_', ' ').title()}: {err}")
                else:
                    form.add_error(None, error)
            except ExchangeRateUnavailable as error:
                form.add_error(None, str(error))
            else:
                messages.success(request, "Transaction added.")
                return redirect("dashboard:activity")
    return render(
        request,
        "dashboard/transaction_form.html",
        {
            "membership": membership,
            "transaction_form": form,
            "active_nav": "activity",
            "suggestions": suggestions,
            "category_groups": _category_groups(membership.workspace)
            if membership
            else [],
            "quick_templates": quick_templates,
            "account_currencies": {
                str(account.pk): account.currency
                for account in accounts_for_membership(membership, write=True).only(
                    "pk",
                    "currency",
                )
            }
            if membership and _can_write(membership)
            else {},
            "offline_transaction_config": {
                "endpoint": (
                    f"/api/workspaces/{membership.workspace_id}/transactions/"
                    if membership
                    else ""
                ),
                "success_url": "/transaction-history/",
                "user_id": str(request.user.pk),
                "rates": account_rates,
            },
        },
    )


@login_required
@require_http_methods(["GET"])
def activity(request):
    membership = _active_membership(request)
    search = request.GET.get("q", "").strip()
    date_from = request.GET.get("from", "").strip()
    date_to = request.GET.get("to", "").strip()
    accessible_account_ids = (
        set(
            accounts_for_membership(membership).values_list("pk", flat=True)
        )
        if membership
        else set()
    )
    transactions = Transaction.objects.none()
    if membership:
        transactions = transactions_for_membership(membership).select_related(
            "account",
            "destination_account",
            "category",
            "refund_of__category",
        )
        if search:
            transactions = transactions.filter(
                Q(description__icontains=search)
                | Q(kind__icontains=search)
                | Q(category__name__icontains=search)
                | (
                    Q(account_id__in=accessible_account_ids)
                    & Q(account__name__icontains=search)
                )
                | (
                    Q(destination_account_id__in=accessible_account_ids)
                    & Q(destination_account__name__icontains=search)
                )
            ).distinct()
        try:
            start_date = date.fromisoformat(date_from) if date_from else None
            end_date = date.fromisoformat(date_to) if date_to else None
        except ValueError:
            messages.error(request, "Choose valid transaction dates.")
            start_date = end_date = None
        if start_date and end_date and start_date > end_date:
            messages.error(request, "The transaction start date must be before its end date.")
            start_date = end_date = None
        workspace_tz = ZoneInfo(membership.workspace.timezone)
        if start_date:
            transactions = transactions.filter(
                occurred_at__gte=timezone.make_aware(
                    datetime.combine(start_date, time.min),
                    workspace_tz,
                )
            )
        if end_date:
            transactions = transactions.filter(
                occurred_at__lt=timezone.make_aware(
                    datetime.combine(end_date + timedelta(days=1), time.min),
                    workspace_tz,
                )
            )
    return render(
        request,
        "dashboard/activity.html",
        {
            "membership": membership,
            "transactions": transactions,
            "search": search,
            "date_from": date_from,
            "date_to": date_to,
            "active_nav": "activity",
            "accessible_account_ids": {
                str(account_id) for account_id in accessible_account_ids
            },
        },
    )


def _transaction_suggestions(membership, writable_accounts):
    recent = (
        transactions_for_membership(membership).filter(
            account__in=writable_accounts,
            kind__in=(Transaction.Kind.INCOME, Transaction.Kind.EXPENSE),
        )
        .select_related("account", "category")
        .order_by("-occurred_at")[:30]
    )
    suggestions = []
    seen = set()
    counts = {
        Transaction.Kind.INCOME: 0,
        Transaction.Kind.EXPENSE: 0,
    }
    for item in recent:
        key = (item.kind, item.account_id, item.category_id)
        if key in seen or counts[item.kind] >= 5:
            continue
        seen.add(key)
        counts[item.kind] += 1
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
        if all(count == 5 for count in counts.values()):
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
@require_http_methods(["GET"])
def accounts_page(request):
    membership = _active_membership(request)
    visible_accounts = (
        accounts_for_membership(membership)
        if membership
        else Account.objects.none()
    )
    currencies = sorted(set(visible_accounts.values_list("currency", flat=True)))
    accounts = visible_accounts
    search = request.GET.get("q", "").strip()
    account_type = request.GET.get("type", "").strip()
    currency = request.GET.get("currency", "").strip().upper()
    if search:
        accounts = accounts.filter(name__icontains=search)
    if account_type in Account.AccountType.values:
        accounts = accounts.filter(account_type=account_type)
    else:
        account_type = ""
    if currency:
        accounts = accounts.filter(currency=currency)
    accounts = list(accounts)
    transactions = (
        list(
            transactions_for_membership(membership)
            .select_related("account", "destination_account")
        )
        if membership
        else []
    )
    return render(
        request,
        "dashboard/accounts.html",
        {
            "membership": membership,
            "account_rows": [
                (account, _account_balance(account, transactions))
                for account in accounts
            ],
            "active_nav": "accounts",
            "can_write": _can_write(membership),
            "search": search,
            "selected_type": account_type,
            "selected_currency": currency,
            "account_types": Account.AccountType.choices,
            "currencies": currencies,
        },
    )


def _account_transaction_history(account, membership):
    return (
        transactions_for_membership(membership)
        .filter(Q(account=account) | Q(destination_account=account))
        .select_related(
            "account",
            "destination_account",
            "category",
            "refund_of__category",
        )
        .order_by("-occurred_at", "-id")
    )


def _filter_account_transactions(request, transactions, workspace):
    search = request.GET.get("q", "").strip()
    kind = request.GET.get("kind", "").strip()
    date_from = request.GET.get("from", "").strip()
    date_to = request.GET.get("to", "").strip()
    if search:
        transactions = transactions.filter(
            Q(description__icontains=search)
            | Q(category__name__icontains=search)
        )
    if kind in Transaction.Kind.values:
        transactions = transactions.filter(kind=kind)
    else:
        kind = ""

    try:
        start_date = date.fromisoformat(date_from) if date_from else None
        end_date = date.fromisoformat(date_to) if date_to else None
    except ValueError:
        messages.error(request, "Choose valid transaction dates.")
        start_date = end_date = None
    if start_date and end_date and start_date > end_date:
        messages.error(request, "The transaction start date must be before its end date.")
        start_date = end_date = None
    workspace_tz = ZoneInfo(workspace.timezone)
    if start_date:
        transactions = transactions.filter(
            occurred_at__gte=timezone.make_aware(
                datetime.combine(start_date, time.min),
                workspace_tz,
            )
        )
    if end_date:
        transactions = transactions.filter(
            occurred_at__lt=timezone.make_aware(
                datetime.combine(end_date + timedelta(days=1), time.min),
                workspace_tz,
            )
        )
    return transactions, {
        "search": search,
        "kind": kind,
        "date_from": date_from,
        "date_to": date_to,
        "transaction_kinds": Transaction.Kind.choices,
    }


@login_required
@require_http_methods(["GET"])
def account_detail(request, account_id):
    membership = _active_membership(request)
    if not membership:
        return HttpResponse("Workspace membership required.", status=403)
    account = get_object_or_404(accounts_for_membership(membership), pk=account_id)
    all_transactions = list(
        _account_transaction_history(account, membership)
    )
    filtered_transactions, filter_context = _filter_account_transactions(
        request,
        _account_transaction_history(account, membership),
        membership.workspace,
    )
    visible_account_ids = {
        str(value)
        for value in accounts_for_membership(membership).values_list("pk", flat=True)
    }
    return render(
        request,
        "dashboard/account_detail.html",
        {
            "membership": membership,
            "account": account,
            "balance": _account_balance(account, all_transactions),
            "balance_in_base": _account_balance_in_base(account, all_transactions),
            "opening_balance_base": (
                account.opening_balance * account.opening_balance_rate
            ),
            "transactions": filtered_transactions,
            "visible_account_ids": visible_account_ids,
            "active_nav": "accounts",
            **filter_context,
        },
    )


@login_required
@require_http_methods(["GET"])
def export_account_transactions(request, account_id):
    membership = _active_membership(request)
    if not membership:
        return HttpResponse("Workspace membership required.", status=403)
    account = get_object_or_404(accounts_for_membership(membership), pk=account_id)
    transactions = list(_account_transaction_history(account, membership))
    visible_account_ids = {
        str(value)
        for value in accounts_for_membership(membership).values_list("pk", flat=True)
    }
    response = HttpResponse(content_type="text/csv")
    response["Content-Disposition"] = (
        f'attachment; filename="account-{account.pk}-transactions.csv"'
    )
    writer = csv.writer(response)
    writer.writerow(
        [
            "Date",
            "Type",
            "Description",
            "Category",
            "From account",
            "To account",
            "Amount",
            "Currency",
            "Reporting amount",
            "Exchange rate",
        ]
    )
    for item in transactions:
        writer.writerow(
            [
                timezone.localtime(item.occurred_at, ZoneInfo(membership.workspace.timezone)).isoformat(),
                _csv_text(item.get_kind_display()),
                _csv_text(item.description),
                _csv_text(
                    item.refund_of.category.name
                    if item.refund_of_id and item.refund_of.category_id
                    else item.category.name if item.category_id else ""
                ),
                _csv_text(
                    item.account.name
                    if str(item.account_id) in visible_account_ids
                    else "Private account"
                ),
                _csv_text(
                    item.destination_account.name
                    if item.destination_account_id
                    and str(item.destination_account_id) in visible_account_ids
                    else (
                        "Private account"
                        if item.destination_account_id
                        else ""
                    )
                ),
                item.amount,
                _csv_text(item.account.currency),
                item.amount_in_base,
                item.exchange_rate,
            ]
        )
    return response


@login_required
@require_http_methods(["GET", "POST"])
def account_management(request):
    membership = _active_membership(request)
    if not membership:
        return redirect("dashboard:settings")

    account_form = None
    access_form = None
    if request.method == "POST":
        if not _can_write(membership):
            return HttpResponse("Workspace write access is required.", status=403)
        if request.POST.get("action") == "remove-access":
            manageable_accounts = accounts_for_membership(membership, manage=True)
            grant = get_object_or_404(
                AccountAccess.objects.filter(
                    account__in=manageable_accounts,
                    can_manage=False,
                ),
                pk=request.POST.get("grant_id"),
            )
            grant.delete()
            messages.success(request, "Account access removed.")
            return redirect("dashboard:settings_accounts")
        if request.POST.get("action") == "share":
            access_form = AccountAccessForm(
                request.POST,
                membership=membership,
            )
            if access_form.is_valid():
                access_form.save()
                messages.success(request, "Account access updated.")
                return redirect("dashboard:settings_accounts")
        else:
            account_form = AccountForm(
                request.POST,
                workspace=membership.workspace,
                membership=membership,
            )
            if account_form.is_valid():
                try:
                    account_form.save()
                except ExchangeRateUnavailable as error:
                    account_form.add_error(None, str(error))
                except ValidationError as error:
                    account_form.add_error(None, error)
                else:
                    messages.success(request, "Account added.")
                    return redirect("dashboard:settings_accounts")
    elif _can_write(membership):
        account_form = AccountForm(
            workspace=membership.workspace,
            membership=membership,
        )
        access_form = AccountAccessForm(membership=membership)

    visible_accounts = list(accounts_for_membership(membership))
    manageable_accounts = accounts_for_membership(membership, manage=True)
    transactions = transactions_for_membership(membership).select_related(
        "account",
        "destination_account",
    )
    return render(
        request,
        "dashboard/account_management.html",
        {
            "membership": membership,
            "account_form": account_form,
            "access_form": access_form,
            "account_rows": [
                (account, _account_balance(account, transactions))
                for account in visible_accounts
            ],
            "grants": AccountAccess.objects.filter(
                account__in=manageable_accounts,
            ).select_related("account", "membership__user"),
            "can_write": _can_write(membership),
            "active_nav": "settings",
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
    accounts = list(accounts_for_membership(membership))
    transactions = list(
        transactions_for_membership(membership)
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
    if not membership:
        return render(
            request,
            "dashboard/reports.html",
            {"membership": None, "summary": None, "active_nav": "reports"},
        )
    context = _analytics_context(request, membership)
    return render(
        request,
        "dashboard/reports.html",
        {
            "membership": membership,
            **context,
            "active_nav": "reports",
        },
    )


def _analytics_context(request, membership):
    workspace = membership.workspace
    with timezone.override(ZoneInfo(workspace.timezone)):
        today = timezone.localdate()
    period = request.GET.get("period", "month")
    if period == "week":
        since = today - timedelta(days=today.weekday())
        until = today
    elif period == "year":
        since = today.replace(month=1, day=1)
        until = today
    elif period == "custom":
        try:
            since = date.fromisoformat(request.GET.get("from", ""))
            until = date.fromisoformat(request.GET.get("to", ""))
            if since > until:
                raise ValueError
        except ValueError:
            messages.error(request, "Choose a valid report date range.")
            period = "month"
            since = today.replace(day=1)
            until = today
    else:
        period = "month"
        since = today.replace(day=1)
        until = today

    summary = _reporting_summary(
        workspace,
        membership=membership,
        since=since,
        until=until,
    )
    period_days = (until - since).days + 1
    previous_until = since - timedelta(days=1)
    previous_since = previous_until - timedelta(days=period_days - 1)
    category_options = list(
        Category.objects.filter(
            workspace=workspace,
            kind=Category.Kind.EXPENSE,
        ).order_by("group", "name")
    )
    selected_category = request.GET.get("category", "")
    if selected_category and not any(
        str(category.pk) == selected_category for category in category_options
    ):
        selected_category = ""
    period_transactions = [
        item
        for item in summary["transactions"]
        if since <= timezone.localtime(
            item.occurred_at,
            ZoneInfo(workspace.timezone),
        ).date() <= until
    ]
    transactions = period_transactions
    if selected_category:
        category_id = int(selected_category)
        transactions = [
            item
            for item in transactions
            if item.category_id == category_id
            or (
                item.refund_of_id
                and item.refund_of.category_id == category_id
            )
        ]
    previous_period_transactions = [
        item
        for item in summary["transactions"]
        if previous_since
        <= timezone.localtime(
            item.occurred_at,
            ZoneInfo(workspace.timezone),
        ).date()
        <= previous_until
    ]
    previous_transactions = previous_period_transactions
    if selected_category:
        category_id = int(selected_category)
        previous_transactions = [
            item
            for item in previous_transactions
            if item.category_id == category_id
            or (
                item.refund_of_id
                and item.refund_of.category_id == category_id
            )
        ]
    income = sum(
        (
            item.amount_in_base
            for item in period_transactions
            if item.kind == Transaction.Kind.INCOME and not item.refund_of_id
        ),
        Decimal("0.00"),
    )
    expenses = sum(
        (
            item.amount_in_base * (-1 if item.refund_of_id else 1)
            for item in transactions
            if item.kind == Transaction.Kind.EXPENSE or item.refund_of_id
        ),
        Decimal("0.00"),
    )
    previous_income = sum(
        (
            item.amount_in_base
            for item in previous_period_transactions
            if item.kind == Transaction.Kind.INCOME and not item.refund_of_id
        ),
        Decimal("0.00"),
    )
    previous_expenses = sum(
        (
            item.amount_in_base * (-1 if item.refund_of_id else 1)
            for item in previous_transactions
            if item.kind == Transaction.Kind.EXPENSE or item.refund_of_id
        ),
        Decimal("0.00"),
    )
    previous_category_totals = {}
    for item in previous_transactions:
        if item.kind == Transaction.Kind.EXPENSE:
            category_name = item.category.name if item.category else "Uncategorized"
            previous_category_totals[category_name] = (
                previous_category_totals.get(category_name, Decimal("0.00"))
                + item.amount_in_base
            )
        elif item.refund_of_id:
            category_name = (
                item.refund_of.category.name
                if item.refund_of.category
                else "Uncategorized"
            )
            previous_category_totals[category_name] = (
                previous_category_totals.get(category_name, Decimal("0.00"))
                - item.amount_in_base
            )
    previous_accounts = list(accounts_for_membership(membership))
    previous_balances = {
        account.pk: account.opening_balance * account.opening_balance_rate
        for account in previous_accounts
    }
    for item in summary["transactions"]:
        item_date = timezone.localtime(
            item.occurred_at,
            ZoneInfo(workspace.timezone),
        ).date()
        if item_date > previous_until:
            continue
        amount = item.amount_in_base
        account = item.account
        if item.kind == Transaction.Kind.INCOME:
            if account.pk in previous_balances:
                previous_balances[account.pk] += (
                    -amount if account.is_liability else amount
                )
        elif item.kind == Transaction.Kind.EXPENSE:
            if account.pk in previous_balances:
                previous_balances[account.pk] += (
                    amount if account.is_liability else -amount
                )
        else:
            if account.pk in previous_balances:
                previous_balances[account.pk] -= amount
            destination = item.destination_account
            if destination.pk in previous_balances:
                previous_balances[destination.pk] += (
                    -amount if destination.is_liability else amount
                )
    previous_net_worth = sum(
        (
            -previous_balances[account.pk]
            if account.is_liability
            else previous_balances[account.pk]
            for account in previous_accounts
        ),
        Decimal("0.00"),
    )
    balance_change = summary["net_worth"] - previous_net_worth
    balance_change_percent = (
        (balance_change / abs(previous_net_worth) * 100).quantize(Decimal("0.1"))
        if previous_net_worth
        else None
    )
    summary["income"] = income
    summary["expenses"] = expenses
    net_savings = income - expenses
    savings_rate = (
        (net_savings / income * 100).quantize(Decimal("0.1"))
        if income > 0
        else Decimal("0.0")
    )

    category_totals = {}
    intent_totals = {value: Decimal("0.00") for value, _ in Transaction.ExpenseIntent.choices}
    for item in transactions:
        if item.kind == Transaction.Kind.EXPENSE:
            name = item.category.name if item.category else "Uncategorized"
            category_totals[name] = (
                category_totals.get(name, Decimal("0.00")) + item.amount_in_base
            )
            intent_totals[item.expense_intent] += item.amount_in_base
        elif item.refund_of_id:
            name = (
                item.refund_of.category.name
                if item.refund_of.category
                else "Uncategorized"
            )
            category_totals[name] = (
                category_totals.get(name, Decimal("0.00")) - item.amount_in_base
            )
    expense_categories = sorted(
        category_totals.items(),
        key=lambda row: row[1],
        reverse=True,
    )
    max_category = max(
        Decimal("0"),
        max((amount for _, amount in expense_categories), default=Decimal("0")),
    )
    expense_category_rows = []
    category_total = sum(
        (max(amount, Decimal("0.00")) for _, amount in expense_categories),
        Decimal("0.00"),
    )
    for name, amount in expense_categories:
        expense_category_rows.append(
            {
                "name": name,
                "amount": amount,
                "percent": (
                    (amount / category_total * 100).quantize(Decimal("0.1"))
                    if category_total > 0 and amount > 0
                    else Decimal("0.0")
                ),
                "width": (
                    min(100, max(0, int(amount / max_category * 100)))
                    if max_category > 0 and amount > 0
                    else 0
                ),
            }
        )

    six_months = []
    month_cursor = today.replace(day=1)
    for offset in range(5, -1, -1):
        month_index = month_cursor.month - offset
        year = month_cursor.year
        while month_index <= 0:
            month_index += 12
            year -= 1
        month_start = date(year, month_index, 1)
        month_end = date(
            year,
            month_index,
            monthrange(year, month_index)[1],
        )
        month_items = [
            item
            for item in summary["transactions"]
            if month_start
            <= timezone.localtime(
                item.occurred_at,
                ZoneInfo(workspace.timezone),
            ).date()
            <= month_end
        ]
        month_expense_items = month_items
        if selected_category:
            month_expense_items = [
                item
                for item in month_items
                if item.category_id == int(selected_category)
                or (
                    item.refund_of_id
                    and item.refund_of.category_id == int(selected_category)
                )
            ]
        month_income = sum(
            (
                item.amount_in_base
                for item in month_items
                if item.kind == Transaction.Kind.INCOME and not item.refund_of_id
            ),
            Decimal("0.00"),
        )
        month_expenses = sum(
            (
                item.amount_in_base * (-1 if item.refund_of_id else 1)
                for item in month_expense_items
                if item.kind == Transaction.Kind.EXPENSE or item.refund_of_id
            ),
            Decimal("0.00"),
        )
        six_months.append(
            {
                "label": month_start.strftime("%b"),
                "income": month_income,
                "expenses": month_expenses,
            }
        )
    max_monthly = max(
        (
            max(item["income"], item["expenses"])
            for item in six_months
        ),
        default=Decimal("0"),
    )
    for item in six_months:
        item["income_width"] = min(
            100,
            int(item["income"] / max_monthly * 100) if max_monthly else 0,
        )
        item["expense_width"] = min(
            100,
            max(0, int(item["expenses"] / max_monthly * 100))
            if max_monthly
            else 0,
        )

    insights = []
    if income > 0 and net_savings > 0:
        insights.append(
            {
                "kind": "positive",
                "title": "Positive savings trend",
                "text": f"You saved {savings_rate}% of your income in this period.",
            }
        )
    if expense_categories:
        top_name, top_amount = expense_categories[0]
        insights.append(
            {
                "kind": "notice",
                "title": f"Top spending: {top_name}",
                "text": f"{top_amount:.2f} {summary['currency']} was recorded in this category.",
            }
        )
    for name, amount in expense_categories:
        previous_amount = previous_category_totals.get(name, Decimal("0.00"))
        if previous_amount > 0 and amount >= previous_amount * Decimal("1.2"):
            increase = ((amount - previous_amount) / previous_amount * 100).quantize(
                Decimal("0.1")
            )
            insights.append(
                {
                    "kind": "warning",
                    "title": f"{name} spending is up",
                    "text": f"Spending rose {increase}% compared with the previous period.",
                }
            )
    if income and expenses > income:
        insights.append(
            {
                "kind": "warning",
                "title": "Spending exceeded income",
                "text": "Review your expense categories and consider a savings target.",
            }
        )

    return {
        "summary": summary,
        "expense_categories": expense_categories,
        "expense_category_rows": expense_category_rows,
        "category_percentages": category_totals,
        "max_category": max_category,
        "intent_totals": [
            (label, intent_totals[value])
            for value, label in Transaction.ExpenseIntent.choices
        ],
        "six_months": six_months,
        "net_savings": net_savings,
        "savings_rate": savings_rate,
        "previous_since": previous_since,
        "previous_until": previous_until,
        "previous_income": previous_income,
        "previous_expenses": previous_expenses,
        "previous_net_worth": previous_net_worth,
        "balance_change": balance_change,
        "balance_change_percent": balance_change_percent,
        "insights": insights,
        "category_options": category_options,
        "selected_category": selected_category,
        "period": period,
        "since": since,
        "until": until,
        "report_types": (
            ("summary", "Monthly financial report"),
            ("expenses", "Expense report"),
            ("income", "Income report"),
            ("savings", "Savings and goals report"),
            ("transactions", "Transaction report"),
        ),
        "report_type": request.GET.get("report", "summary"),
        "user_goals": Goal.objects.filter(
            workspace=workspace,
            created_by=request.user,
        ),
    }


@login_required
def export_reports_csv(request):
    membership = _active_membership(request)
    if not membership:
        return HttpResponse("Workspace membership required.", status=403)
    context = _analytics_context(request, membership)
    report_type = context["report_type"]
    if report_type not in {value for value, _ in context["report_types"]}:
        report_type = "summary"
    response = HttpResponse(content_type="text/csv; charset=utf-8")
    response["Content-Disposition"] = (
        f'attachment; filename="pocketfinance-{report_type}-report.csv"'
    )
    writer = csv.writer(response)
    summary = context["summary"]
    if report_type == "transactions":
        writer.writerow(
            [
                "Date",
                "Type",
                "Description",
                "Category",
                "Expense intent",
                "Amount",
                "Currency",
                "Reporting amount",
            ]
        )
        for item in summary["transactions"]:
            occurred_date = timezone.localtime(
                item.occurred_at,
                ZoneInfo(membership.workspace.timezone),
            ).date()
            if not context["since"] <= occurred_date <= context["until"]:
                continue
            selected_category = context["selected_category"]
            if selected_category and item.category_id != int(selected_category) and not (
                item.refund_of_id
                and item.refund_of.category_id == int(selected_category)
            ):
                continue
            writer.writerow(
                [
                    occurred_date.isoformat(),
                    item.get_kind_display(),
                    _csv_text(item.description),
                    _csv_text(item.category.name if item.category else ""),
                    item.get_expense_intent_display(),
                    item.amount,
                    item.account.currency,
                    item.amount_in_base,
                ]
            )
    elif report_type == "expenses":
        writer.writerow(["Expense category", "Amount", "Currency", "Percent of spending"])
        for item in context["expense_category_rows"]:
            writer.writerow(
                [
                    item["name"],
                    item["amount"],
                    summary["currency"],
                    item["percent"],
                ]
            )
    elif report_type == "income":
        writer.writerow(["Income this period", summary["income"], summary["currency"]])
    elif report_type == "savings":
        writer.writerow(["Net savings", context["net_savings"], summary["currency"]])
        writer.writerow(["Savings rate", context["savings_rate"], "%"])
        writer.writerow([])
        writer.writerow(["Goal", "Category", "Current savings", "Target", "Target date", "Progress"])
        for goal in Goal.objects.filter(
            workspace=membership.workspace,
            created_by=request.user,
        ):
            writer.writerow(
                [
                    _csv_text(goal.name),
                    goal.get_category_display(),
                    goal.current_savings,
                    goal.target_amount,
                    goal.target_date or "",
                    f"{goal.progress_percent}%",
                ]
            )
    else:
        writer.writerow(["Metric", "Amount", "Currency"])
        writer.writerow(["Income", summary["income"], summary["currency"]])
        writer.writerow(["Expenses", summary["expenses"], summary["currency"]])
        writer.writerow(["Net savings", context["net_savings"], summary["currency"]])
        writer.writerow(["Savings rate", context["savings_rate"], "%"])
        writer.writerow(["Net worth", summary["net_worth"], summary["currency"]])
        writer.writerow([])
        writer.writerow(["Expense category", "Amount", "Currency"])
        for name, amount in context["expense_categories"]:
            writer.writerow([name, amount, summary["currency"]])
    return response


@login_required
@require_http_methods(["GET", "POST"])
def goals(request):
    membership = _active_membership(request)
    if not membership:
        return render(
            request,
            "dashboard/goals.html",
            {"membership": None, "goal_form": None, "goal_cards": []},
        )
    goal_queryset = Goal.objects.filter(
        workspace=membership.workspace,
        created_by=request.user,
    )
    if request.method == "POST":
        if not _can_write(membership):
            return HttpResponse("Workspace write access is required.", status=403)
        if request.POST.get("action") == "progress":
            goal = get_object_or_404(goal_queryset, pk=request.POST.get("goal_id"))
            goal_form = GoalForm()
            progress_form = GoalProgressForm(request.POST)
            if progress_form.is_valid():
                previous = goal.progress_percent
                goal.current_savings = progress_form.cleaned_data["current_savings"]
                try:
                    goal.full_clean()
                    goal.save()
                except ValidationError as error:
                    for field, errors in error.error_dict.items():
                        for item in errors:
                            messages.error(request, str(item))
                else:
                    if previous < 75 <= goal.progress_percent < 100:
                        messages.success(
                            request,
                            f"Great progress! You have reached {goal.progress_percent}% of “{goal.name}”.",
                        )
                    elif previous < 100 <= goal.progress_percent:
                        messages.success(
                            request,
                            f"Congratulations! You achieved “{goal.name}”. 🏆",
                        )
                    else:
                        messages.success(request, "Goal savings updated.")
                    return redirect("dashboard:goals")
            else:
                for errors in progress_form.errors.values():
                    for error in errors:
                        messages.error(request, error)
        else:
            goal_form = GoalForm(request.POST)
            if goal_form.is_valid():
                goal = goal_form.save(commit=False)
                goal.workspace = membership.workspace
                goal.created_by = request.user
                try:
                    goal.full_clean()
                    goal.save()
                except ValidationError as error:
                    for field, errors in error.error_dict.items():
                        for item in errors:
                            if field in goal_form.fields:
                                goal_form.add_error(field, item)
                            else:
                                goal_form.add_error(None, item)
                else:
                    if goal.achieved:
                        messages.success(
                            request,
                            f"Congratulations! You already achieved “{goal.name}”. 🏆",
                        )
                    elif goal.progress_percent >= 75:
                        messages.success(
                            request,
                            f"Great start! You are already {goal.progress_percent}% of the way to “{goal.name}”.",
                        )
                    else:
                        messages.success(
                            request,
                            f"Your goal “{goal.name}” is ready to track.",
                        )
                    return redirect("dashboard:goals")
    else:
        goal_form = GoalForm() if _can_write(membership) else None

    icons = {
        Goal.Category.TRAVEL: "✈️",
        Goal.Category.HOME: "🏡",
        Goal.Category.EDUCATION: "📚",
        Goal.Category.EMERGENCY: "🛟",
        Goal.Category.VEHICLE: "🚗",
        Goal.Category.TECHNOLOGY: "💻",
        Goal.Category.WEDDING: "💍",
        Goal.Category.OTHER: "🎯",
    }
    goal_cards = []
    for goal in goal_queryset:
        days_remaining = (
            (goal.target_date - timezone.localdate()).days
            if goal.target_date
            else None
        )
        if days_remaining is not None and days_remaining > 0:
            periods = {
                Goal.Frequency.DAILY: days_remaining,
                Goal.Frequency.WEEKLY: ceil(days_remaining / 7),
                Goal.Frequency.MONTHLY: max(1, ceil(days_remaining / 30.44)),
            }[goal.contribution_frequency]
            suggested_contribution = (
                goal.remaining_amount / periods
            ).quantize(Decimal("0.01"))
        else:
            suggested_contribution = None
        goal_cards.append(
            {
                "goal": goal,
                "icon": icons[goal.category],
                "suggested_contribution": suggested_contribution,
            }
        )
    return render(
        request,
        "dashboard/goals.html",
        {
            "membership": membership,
            "goal_form": goal_form,
            "goal_cards": goal_cards,
            "can_write": _can_write(membership),
            "active_nav": "goals",
        },
    )


@login_required
def bill_payments(request):
    plan_options = (
        ("recurring", "Recurring payment"),
        ("future", "Future payment"),
        ("budget", "Budget"),
    )
    selected_plan = request.GET.get("plan")
    if selected_plan not in {value for value, _ in plan_options}:
        selected_plan = ""
    return render(
        request,
        "dashboard/bills_payments.html",
        {
            "membership": _active_membership(request),
            "active_nav": "bills",
            "plan_options": plan_options,
            "selected_plan": selected_plan,
            "date_policies": (
                (
                    "Expense entry",
                    False,
                    "Records spending that has already happened.",
                    "Available",
                ),
                (
                    "Income entry",
                    False,
                    "Records income that has already been received.",
                    "Available",
                ),
                (
                    "Money transfer history",
                    False,
                    "A transfer must have occurred.",
                    "Available",
                ),
                (
                    "Transaction history",
                    False,
                    "History contains completed transactions.",
                    "Available",
                ),
                (
                    "Bill payment history",
                    False,
                    "A payment date cannot be in the future.",
                    "Planned",
                ),
                (
                    "Budget planning",
                    True,
                    "Budgets can be planned for future periods.",
                    "Planned",
                ),
                ("Bill due date", True, "Bills may be due in the future.", "Planned"),
                (
                    "Savings goal",
                    True,
                    "A goal may have a future target date.",
                    "Planned",
                ),
                (
                    "Scheduled payments",
                    True,
                    "A payment may be scheduled for a future date.",
                    "Planned",
                ),
                (
                    "Investment maturity date",
                    True,
                    "Maturity is a future event.",
                    "Planned",
                ),
            ),
        },
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
            "active_nav": "settings",
        },
    )

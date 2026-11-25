from calendar import monthrange
from datetime import date
from decimal import Decimal
from uuid import UUID
from zoneinfo import ZoneInfo

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db.models import Q, Sum
from django.http import HttpResponseNotAllowed, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_GET, require_POST

from accounts.models import Account
from transactions.models import Transaction
from workspaces.models import Membership
from dashboard.pages import _reporting_summary
from workspaces.permissions import require_workspace_role


def workspace_balances(workspace):
    account_totals = (
        Account.objects.filter(workspace=workspace)
        .values("currency")
        .annotate(opening_balance=Sum("opening_balance"))
    )
    transaction_totals = (
        Transaction.objects.filter(account__workspace=workspace)
        .values("account__currency")
        .annotate(
            income=Sum(
                "amount",
                filter=Q(kind=Transaction.Kind.INCOME, refund_of__isnull=True),
            ),
            expenses=Sum("amount", filter=Q(kind=Transaction.Kind.EXPENSE)),
            refunds=Sum("amount", filter=Q(refund_of__isnull=False)),
        )
    )
    totals_by_currency = {
        item["account__currency"]: item for item in transaction_totals
    }

    balances = []
    for item in account_totals:
        currency = item["currency"]
        transactions = totals_by_currency.get(currency, {})
        opening = item["opening_balance"] or Decimal("0.00")
        income = transactions.get("income") or Decimal("0.00")
        expenses = (transactions.get("expenses") or Decimal("0.00")) - (
            transactions.get("refunds") or Decimal("0.00")
        )
        balances.append(
            {
                "currency": currency,
                "opening_balance": f"{opening:.2f}",
                "income": f"{income:.2f}",
                "expenses": f"{expenses:.2f}",
                "balance": f"{opening + income - expenses:.2f}",
            }
        )
    return balances


@require_GET
@require_workspace_role(write_role=None)
def dashboard(request, workspace_id):
    return JsonResponse({"balances": workspace_balances(request.workspace)})


@login_required
def home(request):
    if request.method != "GET":
        return HttpResponseNotAllowed(["GET"])

    memberships = list(
        Membership.objects.filter(user=request.user)
        .select_related("workspace")
        .order_by("created_at")
    )
    active_id = request.session.get("active_workspace_id")
    membership = next(
        (item for item in memberships if str(item.workspace_id) == active_id),
        memberships[0] if memberships else None,
    )
    balances = workspace_balances(membership.workspace) if membership else []

    if membership:
        with timezone.override(ZoneInfo(membership.workspace.timezone)):
            today = timezone.localdate()
            month_start = date(today.year, today.month, 1)
            month_end = date(
                today.year,
                today.month,
                monthrange(today.year, today.month)[1],
            )
            monthly_totals = list(
                Transaction.objects.filter(
                    account__workspace=membership.workspace,
                    occurred_at__date__gte=month_start,
                    occurred_at__date__lte=month_end,
                )
                .values("account__currency")
                .annotate(
                    income=Sum(
                        "amount",
                        filter=Q(kind=Transaction.Kind.INCOME, refund_of__isnull=True),
                    ),
                    spending=Sum("amount", filter=Q(kind=Transaction.Kind.EXPENSE)),
                    refunds=Sum("amount", filter=Q(refund_of__isnull=False)),
                )
            )
    else:
        monthly_totals = []
    monthly_by_currency = {
        row["account__currency"]: row for row in monthly_totals
    }
    for balance in balances:
        month_data = monthly_by_currency.get(balance["currency"], {})
        balance["month_income"] = f"{month_data.get('income') or Decimal('0.00'):.2f}"
        balance["month_spending"] = (
            f"{(month_data.get('spending') or Decimal('0.00')) - (month_data.get('refunds') or Decimal('0.00')):.2f}"
        )

    return render(
        request,
        "dashboard/home.html",
        {
            "membership": membership,
            "active_workspace": membership.workspace if membership else None,
            "balances": balances,
            "has_accounts": bool(balances),
            "reporting_summary": (
                _reporting_summary(membership.workspace) if membership else None
            ),
            "active_nav": "home",
        },
    )


@login_required
@require_POST
def switch_workspace(request):
    try:
        workspace_id = UUID(request.POST.get("workspace_id", ""))
    except (TypeError, ValueError):
        messages.error(request, "Choose a valid workspace.")
        return redirect("dashboard:home")

    membership = get_object_or_404(
        Membership,
        workspace_id=workspace_id,
        user=request.user,
    )
    request.session["active_workspace_id"] = str(membership.workspace_id)
    messages.success(request, f"Switched to {membership.workspace.name}.")
    return redirect("dashboard:home")

from calendar import monthrange
from datetime import date, timedelta
from decimal import Decimal
import json
from pathlib import Path
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db.models import Q, Sum
from django.http import HttpResponse, HttpResponseNotAllowed, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_GET, require_POST

from apps.accounts.access import accounts_for_membership
from apps.transactions.access import transactions_for_membership
from apps.transactions.models import Transaction
from apps.workspaces.models import Membership
from apps.dashboard.pages import _active_membership, _reporting_summary
from apps.workspaces.permissions import require_workspace_role


def workspace_balances(workspace, membership):
    account_totals = (
        accounts_for_membership(membership)
        .values("currency")
        .annotate(opening_balance=Sum("opening_balance"))
    )
    transaction_totals = (
        transactions_for_membership(membership)
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
    return JsonResponse(
        {"balances": workspace_balances(request.workspace, request.workspace_membership)}
    )


@login_required
@require_POST
def sync_timezone(request):
    try:
        payload = json.loads(request.body)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return JsonResponse({"detail": "Request body must be valid JSON."}, status=400)
    if not isinstance(payload, dict):
        return JsonResponse({"detail": "Request body must be a JSON object."}, status=400)

    timezone_name = payload.get("timezone")
    if not isinstance(timezone_name, str) or not timezone_name or len(timezone_name) > 64:
        return JsonResponse({"detail": "A valid timezone is required."}, status=400)
    try:
        ZoneInfo(timezone_name)
    except (ValueError, ZoneInfoNotFoundError):
        return JsonResponse({"detail": "A valid IANA timezone is required."}, status=400)

    changed = request.user.timezone != timezone_name
    if changed:
        request.user.timezone = timezone_name
        request.user.save(update_fields=["timezone"])
    return JsonResponse({"changed": changed, "timezone": timezone_name})


@require_GET
def offline_context(request):
    if not request.user.is_authenticated:
        return JsonResponse({"detail": "Authentication is required."}, status=401)

    from django.middleware.csrf import get_token

    return JsonResponse(
        {"user_id": str(request.user.pk), "csrf_token": get_token(request)},
        headers={"Cache-Control": "no-store"},
    )


@require_GET
def service_worker(request):
    script_path = (
        Path(__file__).parent / "static" / "js" / "service-worker.js"
    )
    response = HttpResponse(
        script_path.read_text(encoding="utf-8"),
        content_type="application/javascript",
    )
    response["Service-Worker-Allowed"] = "/"
    response["Cache-Control"] = "no-cache"
    return response


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
    balances = workspace_balances(membership.workspace, membership) if membership else []
    selected_range = request.GET.get("range", "30d")
    if membership:
        with timezone.override(ZoneInfo(membership.workspace.timezone)):
            today = timezone.localdate()
    else:
        today = timezone.localdate()
    range_end = today
    if selected_range == "custom":
        try:
            range_start = date.fromisoformat(request.GET.get("from", ""))
            range_end = date.fromisoformat(request.GET.get("to", ""))
            if range_start > range_end:
                raise ValueError
        except ValueError:
            messages.error(request, "Choose a valid date range.")
            selected_range = "30d"
            range_start = today - timedelta(days=29)
            range_end = today
    elif selected_range == "7d":
        range_start = today - timedelta(days=6)
    elif selected_range == "90d":
        range_start = today - timedelta(days=89)
    elif selected_range == "year":
        range_start = today.replace(month=1, day=1)
    else:
        selected_range = "30d"
        range_start = today - timedelta(days=29)

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
                transactions_for_membership(membership).filter(
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
                _reporting_summary(
                    membership.workspace,
                    membership=membership,
                    since=range_start,
                    until=range_end,
                )
                if membership
                else None
            ),
            "date_range": selected_range,
            "range_start": range_start,
            "range_end": range_end,
            "visible_account_ids": {
                str(account_id)
                for account_id in (
                    accounts_for_membership(membership).values_list("pk", flat=True)
                    if membership
                    else []
                )
            },
            "range_options": (
                ("7d", "Last 7 days"),
                ("30d", "Last 30 days"),
                ("90d", "Last 90 days"),
                ("year", "This year"),
                ("custom", "Custom range"),
            ),
            "active_nav": "overview",
        },
    )


@login_required
def home_cards(request):
    membership = _active_membership(request)
    summary = (
        _reporting_summary(membership.workspace, membership=membership)
        if membership
        else None
    )
    return render(
        request,
        "dashboard/home_cards.html",
        {
            "membership": membership,
            "active_workspace": membership.workspace if membership else None,
            "reporting_summary": summary,
            "active_nav": "home_cards",
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

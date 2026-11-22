from decimal import Decimal

from django.db.models import Q, Sum
from django.http import JsonResponse
from django.views.decorators.http import require_GET

from accounts.models import Account
from transactions.models import Transaction
from workspaces.permissions import require_workspace_role


@require_GET
@require_workspace_role(write_role=None)
def dashboard(request, workspace_id):
    account_totals = (
        Account.objects.filter(workspace=request.workspace)
        .values("currency")
        .annotate(opening_balance=Sum("opening_balance"))
    )
    transaction_totals = (
        Transaction.objects.filter(account__workspace=request.workspace)
        .values("account__currency")
        .annotate(
            income=Sum("amount", filter=Q(kind=Transaction.Kind.INCOME)),
            expenses=Sum("amount", filter=Q(kind=Transaction.Kind.EXPENSE)),
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
        expenses = transactions.get("expenses") or Decimal("0.00")
        balances.append(
            {
                "currency": currency,
                "opening_balance": f"{opening:.2f}",
                "income": f"{income:.2f}",
                "expenses": f"{expenses:.2f}",
                "balance": f"{opening + income - expenses:.2f}",
            }
        )

    return JsonResponse({"balances": balances})

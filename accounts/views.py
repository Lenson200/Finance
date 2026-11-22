import json
from decimal import Decimal, InvalidOperation

from django.core.exceptions import ValidationError
from django.http import JsonResponse
from django.core.validators import DecimalValidator
from django.views.decorators.http import require_http_methods

from accounts.models import Account
from workspaces.permissions import require_workspace_role


def account_data(account):
    return {
        "id": str(account.pk),
        "name": account.name,
        "type": account.account_type,
        "currency": account.currency,
        "opening_balance": str(account.opening_balance),
    }


@require_http_methods(["GET", "POST"])
@require_workspace_role()
def accounts(request, workspace_id):
    if request.method == "GET":
        items = Account.objects.filter(workspace=request.workspace)
        return JsonResponse({"accounts": [account_data(account) for account in items]})

    try:
        payload = json.loads(request.body)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return JsonResponse({"detail": "Request body must be valid JSON."}, status=400)
    if not isinstance(payload, dict):
        return JsonResponse({"detail": "Request body must be a JSON object."}, status=400)

    name = payload.get("name")
    account_type = payload.get("type", Account.AccountType.BANK)
    currency = payload.get("currency")
    try:
        opening_balance = Decimal(str(payload.get("opening_balance", "0")))
    except (InvalidOperation, ValueError):
        return JsonResponse(
            {"detail": "Opening balance must be a decimal amount."},
            status=400,
        )

    if not isinstance(name, str) or not name.strip() or len(name.strip()) > 150:
        return JsonResponse({"detail": "A valid account name is required."}, status=400)
    if account_type not in Account.AccountType.values:
        return JsonResponse({"detail": "Invalid account type."}, status=400)
    if (
        not isinstance(currency, str)
        or len(currency) != 3
        or not currency.isalpha()
        or not currency.isascii()
    ):
        return JsonResponse({"detail": "Currency must be a three-letter code."}, status=400)
    if not opening_balance.is_finite():
        return JsonResponse({"detail": "Opening balance must be finite."}, status=400)
    try:
        DecimalValidator(14, 2)(opening_balance)
    except ValidationError:
        return JsonResponse({"detail": "Opening balance supports at most 12 digits and 2 decimals."}, status=400)

    account = Account.objects.create(
        workspace=request.workspace,
        name=name.strip(),
        account_type=account_type,
        currency=currency.upper(),
        opening_balance=opening_balance,
    )
    return JsonResponse(account_data(account), status=201)

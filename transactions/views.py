import json
from decimal import Decimal, InvalidOperation
from uuid import UUID

from django.core.exceptions import ValidationError
from django.core.validators import DecimalValidator
from django.http import JsonResponse
from django.views.decorators.http import require_http_methods

from accounts.models import Account
from transactions.models import Transaction
from workspaces.permissions import require_workspace_role


def transaction_data(item):
    return {
        "id": str(item.pk),
        "account_id": str(item.account_id),
        "destination_account_id": (
            str(item.destination_account_id) if item.destination_account_id else None
        ),
        "kind": item.kind,
        "amount": str(item.amount),
        "description": item.description,
        "occurred_at": item.occurred_at.isoformat(),
    }


@require_http_methods(["GET", "POST"])
@require_workspace_role()
def transactions(request, workspace_id):
    if request.method == "GET":
        items = Transaction.objects.filter(account__workspace=request.workspace).select_related(
            "account", "destination_account"
        )
        return JsonResponse({"transactions": [transaction_data(item) for item in items]})

    try:
        payload = json.loads(request.body)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return JsonResponse({"detail": "Request body must be valid JSON."}, status=400)
    if not isinstance(payload, dict):
        return JsonResponse({"detail": "Request body must be a JSON object."}, status=400)

    kind = payload.get("kind")
    description = payload.get("description", "")
    if kind not in Transaction.Kind.values:
        return JsonResponse({"detail": "Invalid transaction kind."}, status=400)
    if not isinstance(description, str) or len(description) > 255:
        return JsonResponse(
            {"detail": "Description must be at most 255 characters."},
            status=400,
        )
    try:
        amount = Decimal(str(payload.get("amount", "")))
        account_id = UUID(str(payload.get("account_id", "")))
    except (InvalidOperation, ValueError):
        return JsonResponse({"detail": "A valid amount and account are required."}, status=400)
    try:
        account = Account.objects.get(pk=account_id, workspace=request.workspace)
    except Account.DoesNotExist:
        return JsonResponse({"detail": "Account not found in this workspace."}, status=404)
    if not amount.is_finite() or amount <= 0:
        return JsonResponse({"detail": "Amount must be greater than zero."}, status=400)
    try:
        DecimalValidator(14, 2)(amount)
    except ValidationError:
        return JsonResponse({"detail": "Amount supports at most 12 digits and 2 decimals."}, status=400)

    destination = None
    if kind == Transaction.Kind.TRANSFER:
        try:
            destination_id = UUID(str(payload.get("destination_account_id", "")))
            destination = Account.objects.get(
                pk=destination_id,
                workspace=request.workspace,
            )
        except (ValueError, Account.DoesNotExist):
            return JsonResponse(
                {"detail": "A destination account in this workspace is required."},
                status=400,
            )
        if destination.pk == account.pk or destination.currency != account.currency:
            return JsonResponse(
                {"detail": "Transfer accounts must be different and use the same currency."},
                status=400,
            )
    elif payload.get("destination_account_id") is not None:
        return JsonResponse(
            {"detail": "A destination account is only valid for a transfer."},
            status=400,
        )

    item = Transaction.objects.create(
        account=account,
        destination_account=destination,
        kind=kind,
        amount=amount,
        description=description,
    )
    return JsonResponse(transaction_data(item), status=201)

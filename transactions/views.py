import json
from decimal import Decimal, InvalidOperation
from uuid import UUID

from django.core.exceptions import ValidationError
from django.core.validators import DecimalValidator
from django.db import IntegrityError
from django.http import JsonResponse
from django.views.decorators.http import require_http_methods

from accounts.models import Account
from categories.models import Category
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
        "amount_in_base": str(item.amount_in_base),
        "exchange_rate": str(item.exchange_rate),
        "category_id": str(item.category_id) if item.category_id else None,
        "refund_of_id": str(item.refund_of_id) if item.refund_of_id else None,
        "description": item.description,
        "occurred_at": item.occurred_at.isoformat(),
    }


def _matches_posting(
    item,
    *,
    account,
    destination,
    category,
    refund_of,
    kind,
    amount,
    exchange_rate,
    description,
):
    return (
        item.account_id == account.pk
        and item.destination_account_id == (destination.pk if destination else None)
        and item.category_id == (category.pk if category else None)
        and item.refund_of_id == (refund_of.pk if refund_of else None)
        and item.kind == kind
        and item.amount == amount
        and item.exchange_rate == exchange_rate
        and item.description == description
    )


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
        exchange_rate = Decimal(str(payload.get("exchange_rate", "1")))
    except (InvalidOperation, ValueError):
        return JsonResponse({"detail": "A valid amount and account are required."}, status=400)
    try:
        account = Account.objects.get(pk=account_id, workspace=request.workspace)
    except Account.DoesNotExist:
        return JsonResponse({"detail": "Account not found in this workspace."}, status=404)
    if not amount.is_finite() or amount <= 0:
        return JsonResponse({"detail": "Amount must be greater than zero."}, status=400)
    if not exchange_rate.is_finite() or exchange_rate <= 0:
        return JsonResponse({"detail": "Exchange rate must be positive."}, status=400)
    if account.currency != request.workspace.base_currency and "exchange_rate" not in payload:
        return JsonResponse(
            {"detail": "Exchange rate is required for a non-base-currency account."},
            status=400,
        )
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

    category = None
    category_id = payload.get("category_id")
    if kind in (Transaction.Kind.INCOME, Transaction.Kind.EXPENSE) and category_id:
        try:
            category_pk = int(category_id)
            category = Category.objects.get(
                pk=category_pk,
                workspace=request.workspace,
                kind=kind,
            )
        except (TypeError, ValueError, Category.DoesNotExist):
            return JsonResponse(
                {"detail": "Category not found or does not match the transaction."},
                status=400,
            )
    elif kind == Transaction.Kind.TRANSFER and category_id is not None:
        return JsonResponse({"detail": "Transfers cannot have a category."}, status=400)

    refund_of = None
    if payload.get("refund_of_id"):
        try:
            refund_of = Transaction.objects.get(
                pk=UUID(str(payload["refund_of_id"])),
                account__workspace=request.workspace,
                kind=Transaction.Kind.EXPENSE,
            )
        except (TypeError, ValueError, Transaction.DoesNotExist):
            return JsonResponse(
                {"detail": "Original expense not found in this workspace."},
                status=400,
            )

    if category is None and kind in (Transaction.Kind.INCOME, Transaction.Kind.EXPENSE):
        default_name = "Other income" if kind == Transaction.Kind.INCOME else "Other expense"
        category = Category.objects.filter(
            workspace=request.workspace,
            name=default_name,
            kind=kind,
        ).first()

    if not payload.get("idempotency_key"):
        return JsonResponse(
            {"detail": "An idempotency key is required for transaction posting."},
            status=400,
        )
    try:
        idempotency_key = UUID(str(payload["idempotency_key"]))
    except (TypeError, ValueError):
        return JsonResponse({"detail": "Invalid idempotency key."}, status=400)
    if kind in (Transaction.Kind.INCOME, Transaction.Kind.EXPENSE) and category is None:
        default_name = "Other income" if kind == Transaction.Kind.INCOME else "Other expense"
        category = Category.objects.filter(
            workspace=request.workspace,
            name=default_name,
            kind=kind,
        ).first()
    existing = Transaction.objects.filter(idempotency_key=idempotency_key).first()
    if existing:
        if _matches_posting(
            existing,
            account=account,
            destination=destination,
            category=category,
            refund_of=refund_of,
            kind=kind,
            amount=amount,
            exchange_rate=exchange_rate,
            description=description,
        ):
            return JsonResponse(transaction_data(existing), status=200)
        return JsonResponse(
            {"detail": "This idempotency key was already used for another transaction."},
            status=409,
        )

    item = Transaction(
        account=account,
        destination_account=destination,
        category=category,
        refund_of=refund_of,
        kind=kind,
        amount=amount,
        exchange_rate=exchange_rate,
        idempotency_key=idempotency_key,
        description=description,
    )
    try:
        item.save()
    except IntegrityError:
        existing = Transaction.objects.filter(idempotency_key=idempotency_key).first()
        if existing and _matches_posting(
            existing,
            account=account,
            destination=destination,
            category=category,
            refund_of=refund_of,
            kind=kind,
            amount=amount,
            exchange_rate=exchange_rate,
            description=description,
        ):
            return JsonResponse(transaction_data(existing), status=200)
        if existing:
            return JsonResponse(
                {"detail": "This idempotency key was already used for another transaction."},
                status=409,
            )
        raise
    except ValidationError as error:
        return JsonResponse({"detail": error.messages}, status=400)
    return JsonResponse(transaction_data(item), status=201)

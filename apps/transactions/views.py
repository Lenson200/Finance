import json
from datetime import datetime, time
from decimal import Decimal, InvalidOperation
from uuid import UUID
from django.core.exceptions import ValidationError
from django.core.validators import DecimalValidator
from django.db import IntegrityError
from django.http import JsonResponse
from django.utils import timezone
from django.views.decorators.http import require_http_methods
from apps.accounts.access import account_access_for, accounts_for_membership
from apps.accounts.models import Account
from apps.categories.models import Category
from apps.transactions.models import Transaction
from apps.transactions.access import transactions_for_membership
from apps.workspaces.permissions import require_workspace_role


def transaction_data(item, visible_account_ids):
    source_visible = item.account_id in visible_account_ids
    destination_visible = (
        item.destination_account_id in visible_account_ids
        if item.destination_account_id
        else None
    )
    return {
        "id": str(item.pk),
        "account_id": str(item.account_id) if source_visible else None,
        "account_private": not source_visible,
        "destination_account_id": (
            str(item.destination_account_id)
            if item.destination_account_id and destination_visible
            else None
        ),
        "destination_account_private": (
            not destination_visible if destination_visible is not None else False
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
    occurred_at=None,
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
        and (occurred_at is None or item.occurred_at == occurred_at)
    )


@require_http_methods(["GET", "POST"])
@require_workspace_role()
def transactions(request, workspace_id):
    if request.method == "GET":
        items = transactions_for_membership(request.workspace_membership).select_related(
            "account", "destination_account"
        )
        visible_account_ids = set(
            accounts_for_membership(request.workspace_membership).values_list(
                "pk",
                flat=True,
            )
        )
        return JsonResponse(
            {
                "transactions": [
                    transaction_data(item, visible_account_ids)
                    for item in items
                ]
            }
        )

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
        account = accounts_for_membership(
            request.workspace_membership
        ).get(pk=account_id)
    except Account.DoesNotExist:
        return JsonResponse({"detail": "Account not found or access denied."}, status=404)
    if not account_access_for(request.workspace_membership, account).can_write:
        return JsonResponse({"detail": "Write access to this account is required."}, status=403)
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

    occurred_at = None
    occurred_date = payload.get("occurred_date")
    if occurred_date is not None:
        if not isinstance(occurred_date, str):
            return JsonResponse({"detail": "A valid transaction date is required."}, status=400)
        try:
            local_date = datetime.strptime(occurred_date, "%Y-%m-%d").date()
        except ValueError:
            return JsonResponse({"detail": "A valid transaction date is required."}, status=400)
        if local_date > timezone.localdate():
            return JsonResponse(
                {"detail": "Completed transactions cannot use a future date."},
                status=400,
            )
        occurred_at = timezone.make_aware(
            datetime.combine(local_date, time.min),
            timezone.get_current_timezone(),
        )

    destination = None
    if kind == Transaction.Kind.TRANSFER:
        try:
            destination_id = UUID(str(payload.get("destination_account_id", "")))
            destination = accounts_for_membership(
                request.workspace_membership,
                write=True,
            ).get(pk=destination_id)
        except (ValueError, Account.DoesNotExist):
            return JsonResponse(
                {"detail": "A destination account with write access is required."},
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
                account__in=accounts_for_membership(
                    request.workspace_membership,
                    write=True,
                ),
                kind=Transaction.Kind.EXPENSE,
            )
        except (TypeError, ValueError, Transaction.DoesNotExist):
            return JsonResponse(
                {"detail": "Original expense not found or access denied."},
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
            occurred_at=occurred_at,
        ):
            return JsonResponse(
                transaction_data(
                    existing,
                    set(
                        accounts_for_membership(
                            request.workspace_membership,
                        ).values_list("pk", flat=True)
                    ),
                ),
                status=200,
            )
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
    if occurred_at is not None:
        item.occurred_at = occurred_at
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
            occurred_at=occurred_at,
        ):
            return JsonResponse(
                transaction_data(
                    existing,
                    set(
                        accounts_for_membership(
                            request.workspace_membership,
                        ).values_list("pk", flat=True)
                    ),
                ),
                status=200,
            )
        if existing:
            return JsonResponse(
                {"detail": "This idempotency key was already used for another transaction."},
                status=409,
            )
        raise
    except ValidationError as error:
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
            occurred_at=occurred_at,
        ):
            return JsonResponse(
                transaction_data(
                    existing,
                    set(
                        accounts_for_membership(
                            request.workspace_membership,
                        ).values_list("pk", flat=True)
                    ),
                ),
                status=200,
            )
        if existing:
            return JsonResponse(
                {"detail": "This idempotency key was already used for another transaction."},
                status=409,
            )
        return JsonResponse({"detail": error.messages}, status=400)
    return JsonResponse(
        transaction_data(
            item,
            set(
                accounts_for_membership(
                    request.workspace_membership,
                ).values_list("pk", flat=True)
            ),
        ),
        status=201,
    )

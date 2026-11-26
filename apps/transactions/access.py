from django.db.models import Q

from apps.transactions.models import Transaction


def transactions_for_membership(membership):
    return Transaction.objects.filter(
        Q(account__access_grants__membership=membership)
        | Q(destination_account__access_grants__membership=membership)
    ).filter(
        Q(refund_of__isnull=True)
        | Q(refund_of__account__access_grants__membership=membership)
    ).distinct()

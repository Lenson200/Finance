from apps.accounts.models import Account, AccountAccess


def accounts_for_membership(membership, *, write=False, manage=False):
    grants = AccountAccess.objects.filter(membership=membership)
    if write:
        grants = grants.filter(can_write=True)
    if manage:
        grants = grants.filter(can_manage=True)
    return Account.objects.filter(
        workspace=membership.workspace,
        access_grants__in=grants,
    )


def account_access_for(membership, account):
    return AccountAccess.objects.filter(
        membership=membership,
        account=account,
    ).first()

from workspaces.models import Membership


def workspace_context(request):
    if not request.user.is_authenticated:
        return {}

    memberships = list(
        Membership.objects.filter(user=request.user)
        .select_related("workspace")
        .order_by("created_at")
    )
    active_id = request.session.get("active_workspace_id")
    active_membership = next(
        (item for item in memberships if str(item.workspace_id) == active_id),
        memberships[0] if memberships else None,
    )
    return {
        "workspace_memberships": memberships,
        "membership": active_membership,
        "active_workspace": (
            active_membership.workspace if active_membership else None
        ),
    }

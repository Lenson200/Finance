from functools import wraps

from django.http import JsonResponse

from workspaces.models import Membership


ROLE_LEVELS = {
    Membership.Role.VIEWER: 0,
    Membership.Role.MEMBER: 1,
    Membership.Role.ADMIN: 2,
    Membership.Role.OWNER: 3,
}


def require_workspace_role(
    minimum_role=Membership.Role.VIEWER,
    *,
    write_role=Membership.Role.MEMBER,
):
    roles = (minimum_role,) if write_role is None else (minimum_role, write_role)
    for role in roles:
        if role not in ROLE_LEVELS:
            raise ValueError(f"Unknown workspace role: {role}")

    def decorator(view_func):
        @wraps(view_func)
        def wrapped(request, *args, workspace_id, **kwargs):
            if not request.user.is_authenticated:
                return JsonResponse({"detail": "Authentication required."}, status=401)

            membership = (
                Membership.objects.select_related("workspace")
                .filter(workspace_id=workspace_id, user=request.user)
                .first()
            )
            if membership is None:
                return JsonResponse({"detail": "Workspace access denied."}, status=403)
            required_role = (
                minimum_role
                if write_role is None or request.method in {"GET", "HEAD", "OPTIONS"}
                else write_role
            )
            if ROLE_LEVELS.get(membership.role, -1) < ROLE_LEVELS[required_role]:
                return JsonResponse({"detail": "Insufficient workspace role."}, status=403)

            request.workspace = membership.workspace
            request.workspace_membership = membership
            return view_func(request, *args, workspace_id=workspace_id, **kwargs)

        return wrapped

    return decorator

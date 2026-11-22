import json

from django.db import transaction
from django.http import JsonResponse
from django.views.decorators.http import require_http_methods

from workspaces.models import Membership, Workspace


@require_http_methods(["GET", "POST"])
def workspaces(request):
    if not request.user.is_authenticated:
        return JsonResponse({"detail": "Authentication required."}, status=401)

    if request.method == "GET":
        results = [
            {
                "id": str(membership.workspace_id),
                "name": membership.workspace.name,
                "role": membership.role,
            }
            for membership in Membership.objects.filter(user=request.user).select_related(
                "workspace"
            )
        ]
        return JsonResponse({"workspaces": results})

    try:
        payload = json.loads(request.body)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return JsonResponse({"detail": "Request body must be valid JSON."}, status=400)
    if not isinstance(payload, dict) or not isinstance(payload.get("name"), str):
        return JsonResponse({"detail": "A workspace name is required."}, status=400)

    name = payload["name"].strip()
    if not name or len(name) > 150:
        return JsonResponse(
            {"detail": "Workspace name must be between 1 and 150 characters."},
            status=400,
        )

    with transaction.atomic():
        workspace = Workspace.objects.create(name=name, created_by=request.user)
        Membership.objects.create(
            workspace=workspace,
            user=request.user,
            role=Membership.Role.OWNER,
        )
    return JsonResponse(
        {"id": str(workspace.pk), "name": workspace.name, "role": Membership.Role.OWNER},
        status=201,
    )

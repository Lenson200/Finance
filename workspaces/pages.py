from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.shortcuts import redirect, render
from django.views.decorators.http import require_http_methods

from categories.defaults import seed_default_categories
from workspaces.forms import SharedWorkspaceForm
from workspaces.models import Membership, Workspace


@login_required
@require_http_methods(["GET", "POST"])
def create_shared_workspace(request):
    form = SharedWorkspaceForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        with transaction.atomic():
            workspace = form.save(commit=False)
            workspace.workspace_type = Workspace.WorkspaceType.SHARED
            workspace.created_by = request.user
            workspace.save()
            Membership.objects.create(
                workspace=workspace,
                user=request.user,
                role=Membership.Role.OWNER,
            )
            seed_default_categories(workspace)
        request.session["active_workspace_id"] = str(workspace.pk)
        messages.success(request, f"{workspace.name} is ready to use.")
        return redirect("dashboard:home")

    return render(
        request,
        "workspaces/create.html",
        {"form": form},
    )

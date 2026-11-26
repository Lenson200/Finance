from django.contrib.auth import login
from django.db import transaction
from django.shortcuts import redirect, render
from django.views.decorators.http import require_http_methods

from apps.users.forms import RegistrationForm
from apps.categories.defaults import seed_default_categories
from apps.workspaces.models import Membership, Workspace


@require_http_methods(["GET", "POST"])
def register(request):
    if request.user.is_authenticated:
        return redirect("dashboard:home")

    form = RegistrationForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        with transaction.atomic():
            user = form.save()
            workspace = Workspace.objects.create(
                name="Personal",
                workspace_type=Workspace.WorkspaceType.PERSONAL,
                created_by=user,
            )
            Membership.objects.create(
                workspace=workspace,
                user=user,
                role=Membership.Role.OWNER,
            )
            seed_default_categories(workspace)
        login(request, user)
        request.session["active_workspace_id"] = str(workspace.pk)
        return redirect("dashboard:home")

    return render(request, "users/register.html", {"form": form})

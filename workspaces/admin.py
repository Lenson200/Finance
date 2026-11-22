from django.contrib import admin

from workspaces.models import Membership, Workspace


admin.site.register(Workspace)
admin.site.register(Membership)

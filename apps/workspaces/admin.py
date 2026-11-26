from django.contrib import admin

from apps.workspaces.models import Currency, Membership, Workspace


admin.site.register(Currency)
admin.site.register(Workspace)
admin.site.register(Membership)

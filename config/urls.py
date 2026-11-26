"""
URL configuration for config project.

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/5.2/topics/http/urls/
Examples:
Function views
    1. Add an import:  from my_app import views
    2. Add a URL to urlpatterns:  path('', views.home, name='home')
Class-based views
    1. Add an import:  from other_app.views import Home
    2. Add a URL to urlpatterns:  path('', Home.as_view(), name='home')
Including another URLconf
    1. Import the include() function: from django.urls import include, path
    2. Add a URL to urlpatterns:  path('blog/', include('blog.urls'))
"""

from django.contrib import admin
from django.contrib.auth import views as auth_views
from django.urls import include, path

from apps.dashboard.views import dashboard
from apps.users.views import register

urlpatterns = [
    path("admin/", admin.site.urls),
    path("", include("apps.dashboard.urls")),
    path(
        "login/",
        auth_views.LoginView.as_view(
            template_name="users/login.html",
            redirect_authenticated_user=True,
        ),
        name="login",
    ),
    path("logout/", auth_views.LogoutView.as_view(), name="logout"),
    path("register/", register, name="register"),
    path("workspaces/", include("apps.workspaces.web_urls")),
    path("api/workspaces/", include("apps.workspaces.urls")),
    path("api/workspaces/<uuid:workspace_id>/accounts/", include("apps.accounts.urls")),
    path("api/workspaces/<uuid:workspace_id>/transactions/", include("apps.transactions.urls")),
    path(
        "api/workspaces/<uuid:workspace_id>/dashboard/",
        dashboard,
        name="api_dashboard",
    ),
]

"""Admin host only: the platform console. Nothing customer-facing is served here."""
from django.http import HttpResponse
from django.urls import path

from platform_admin.site import admin_site

urlpatterns = [
    path("healthz", lambda r: HttpResponse("ok"), name="healthz"),
    path("", admin_site.urls),
]

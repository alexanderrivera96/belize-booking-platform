"""Public host: customer marketplace, provider dashboard and /api/v1. No /admin route here."""
from django.http import HttpResponse
from django.urls import include, path

urlpatterns = [
    path("healthz", lambda r: HttpResponse("ok"), name="healthz"),
    path("", include("accounts.urls")),
    path("", include("customer_site.urls")),
]

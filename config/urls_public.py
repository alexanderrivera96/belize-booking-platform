"""Public host: customer marketplace, provider dashboard and /api/v1. No /admin route here."""
from django.http import HttpResponse
from django.urls import include, path

from booking.feeds import staff_feed

urlpatterns = [
    path("healthz", lambda r: HttpResponse("ok"), name="healthz"),
    path("api/v1/", include("api.urls")),
    path("feeds/<str:token>.ics", staff_feed, name="staff_feed"),
    path("", include("accounts.urls")),
    path("provider/", include("provider_site.urls")),
    path("", include("customer_site.urls")),
]

from django.http import HttpResponse
from django.urls import path

urlpatterns = [
    path("healthz", lambda r: HttpResponse("ok"), name="healthz"),
]

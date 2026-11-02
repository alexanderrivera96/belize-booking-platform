from django.urls import include, path
from rest_framework.routers import SimpleRouter

from . import views

app_name = "api"

business = SimpleRouter()
business.register("services", views.ServiceViewSet, basename="service")
business.register("staff", views.StaffViewSet, basename="staff")
business.register("schedules", views.ScheduleViewSet, basename="schedule")
business.register("appointments", views.AppointmentViewSet, basename="appointment")

me = SimpleRouter()
me.register("appointments", views.MyAppointments, basename="my-appointment")

urlpatterns = [
    path("public/categories", views.categories),
    path("public/search", views.search),
    path("public/businesses/<slug:slug>", views.business_detail),
    path("public/businesses/<slug:slug>/availability", views.availability),
    path("public/holds", views.HoldView.as_view()),
    path("public/holds/<uuid:hold_id>", views.release_hold),
    path("public/holds/<uuid:hold_id>/confirm", views.confirm_hold),
    path("auth/otp/start", views.OtpStart.as_view()),
    path("auth/otp/verify", views.OtpVerify.as_view()),
    path("auth/logout", views.auth_logout),
    path("me/memberships", views.my_memberships),
    path("me/invites/<uuid:member_id>/accept", views.accept_invite),
    path("me/", include(me.urls)),
    path("businesses/<uuid:business_id>/", include(business.urls)),
]

from django.urls import path

from . import views

app_name = "customer"

urlpatterns = [
    path("", views.home, name="home"),
    path("search/", views.search, name="search"),
    path("for-business/", views.for_business, name="for_business"),
    path("privacy/", views.privacy, name="privacy"),
    path("b/<slug:slug>/", views.business_detail, name="business"),
    path("b/<slug:slug>/book/", views.book, name="book"),
    path("b/<slug:slug>/hold/", views.hold, name="hold"),
    path("checkout/<uuid:hold_id>/", views.checkout, name="checkout"),
    path("checkout/<uuid:hold_id>/cancel/", views.cancel_hold, name="cancel_hold"),
    path("me/", views.my_bookings, name="my_bookings"),
    path("me/a/<uuid:appt_id>/", views.my_booking, name="my_booking"),
    path("me/a/<uuid:appt_id>/done/", views.booked, name="booked"),
    path("me/a/<uuid:appt_id>/cancel/", views.do_cancel, name="cancel"),
    path("me/a/<uuid:appt_id>/reschedule/", views.do_reschedule, name="reschedule"),
    path("m/<str:token>/", views.manage_by_token, name="manage_token"),
    path("m/<str:token>/cancel/", views.do_cancel, name="cancel_token"),
    path("m/<str:token>/reschedule/", views.do_reschedule, name="reschedule_token"),
]

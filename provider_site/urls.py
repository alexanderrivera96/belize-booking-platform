from django.urls import path

from . import views

app_name = "provider"

B = "<uuid:business_id>/"

urlpatterns = [
    path("", views.home, name="home"),
    path("invites/<uuid:member_id>/accept/", views.accept_invite, name="accept_invite"),
    path(B, views.dashboard, name="dashboard"),
    path(B + "calendar/", views.calendar, name="calendar"),
    path(B + "calendar/feed/<uuid:staff_id>/", views.calendar_feed, name="calendar_feed"),
    path(B + "appointments/", views.appointments, name="appointments"),
    path(B + "appointments/new/", views.new_booking, name="new_booking"),
    path(B + "a/<uuid:appt_id>/", views.appointment_detail, name="appointment"),
    path(B + "a/<uuid:appt_id>/<slug:action>/", views.appointment_action, name="appointment_action"),
    path(B + "clients/", views.clients, name="clients"),
    path(B + "clients/<uuid:client_id>/", views.client_detail, name="client"),
    path(B + "services/", views.services, name="services"),
    path(B + "services/new/", views.service_edit, name="service_new"),
    path(B + "services/<uuid:service_id>/", views.service_edit, name="service_edit"),
    path(B + "team/", views.team, name="team"),
    path(B + "team/new/", views.staff_edit, name="staff_new"),
    path(B + "team/<uuid:staff_id>/", views.staff_edit, name="staff_edit"),
    path(B + "team/invite/", views.invite, name="invite"),
    path(B + "team/members/<uuid:member_id>/remove/", views.remove_member, name="remove_member"),
    path(B + "hours/", views.hours, name="hours"),
    path(B + "time-off/", views.time_off, name="time_off"),
    path(B + "time-off/<int:item_id>/delete/", views.time_off_delete, name="time_off_delete"),
    path(B + "settings/", views.settings_view, name="settings"),
]

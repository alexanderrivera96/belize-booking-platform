"""Capabilities mapped to roles in one place (accounts design §2)."""
from enum import StrEnum


class Cap(StrEnum):
    VIEW_ALL_CALENDARS = "view_all_calendars"
    VIEW_OWN_CALENDAR = "view_own_calendar"
    MANAGE_ALL_APPOINTMENTS = "manage_all_appointments"
    MANAGE_OWN_APPOINTMENTS = "manage_own_appointments"
    CREATE_BOOKING_FOR_SELF = "create_booking_for_self"
    BLOCK_OWN_TIME = "block_own_time"
    VIEW_CLIENTS = "view_clients"
    EDIT_CLIENT_NOTES = "edit_client_notes"
    RECORD_BALANCE_PAYMENT = "record_balance_payment"
    MANAGE_SERVICES = "manage_services"
    MANAGE_STAFF = "manage_staff"
    MANAGE_HOURS_ALL = "manage_hours_all"
    MANAGE_POLICIES = "manage_policies"
    MANAGE_PAYMENT_ACCOUNTS = "manage_payment_accounts"
    MANAGE_SUBSCRIPTION = "manage_subscription"
    MANAGE_PROFILE = "manage_profile"
    VIEW_PAYMENTS_REPORT = "view_payments_report"


ROLE_CAPS = {
    "owner": set(Cap),
    "staff": {
        Cap.VIEW_OWN_CALENDAR, Cap.MANAGE_OWN_APPOINTMENTS, Cap.CREATE_BOOKING_FOR_SELF,
        Cap.BLOCK_OWN_TIME, Cap.VIEW_CLIENTS, Cap.EDIT_CLIENT_NOTES, Cap.RECORD_BALANCE_PAYMENT,
    },
}

SHARED_CALENDAR_CAPS = {Cap.VIEW_ALL_CALENDARS, Cap.MANAGE_ALL_APPOINTMENTS}


def has_cap(membership, cap: Cap) -> bool:
    if membership is None or membership.status != "active":
        return False
    if cap in ROLE_CAPS.get(membership.role, set()):
        return True
    # D6: owner may let staff see and manage colleagues' calendars
    if cap in SHARED_CALENDAR_CAPS:
        policy = getattr(membership.business, "booking_policy", None)
        return bool(policy and policy.staff_can_view_all)
    return False

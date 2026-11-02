"""Messages and reminder scheduling (architecture plan: notifications; booking-engine design §7)."""
import logging
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

from django.conf import settings
from django.utils import timezone

from booking.models import AppointmentAccessToken, S

from .models import MessageLog, Reminder
from .senders import send

log = logging.getLogger("notifications")

QUIET_START, QUIET_END = time(21, 0), time(8, 0)


# ---------------------------------------------------------------- formatting helpers

def local_when(appt, tz_name=None):
    tz = ZoneInfo(tz_name or appt.business.timezone)
    t = appt.starts_at.astimezone(tz)
    return t.strftime("%a %-d %b, %-I:%M %p")


def manage_link(appt):
    raw = AppointmentAccessToken.issue(appt)
    return f"{settings.PUBLIC_BASE_URL.rstrip('/')}/m/{raw}"


def recipient(appt):
    """(phone, channel) for the customer. Linked account → verified phone + preference."""
    c = appt.customer
    if c is None:
        return None, None
    if c.user_id and c.user and c.user.deleted_at is None:
        return str(c.user.phone), c.user.preferred_channel
    return (str(c.phone) if c.phone else None), "whatsapp"


def provider_recipients(appt):
    """Staff member with a login, else the business owner."""
    phones = []
    member = appt.staff.member
    if member and member.status == "active":
        phones.append(str(member.user.phone))
    owner = appt.business.owner_membership
    if owner and str(owner.user.phone) not in phones:
        phones.append(str(owner.user.phone))
    return phones


def _log_send(appt, audience, template, to, body, channel="whatsapp"):
    if not to:
        return
    try:
        result = send(to, body, channel)
        MessageLog.objects.create(appointment=appt, business=appt.business, audience=audience, template=template,
                                  channel=result.channel, to=to, body=body,
                                  provider_message_id=result.message_id)
    except Exception as exc:  # never break the booking because a message failed
        log.exception("Message %s to %s failed", template, to)
        MessageLog.objects.create(appointment=appt, business=appt.business, audience=audience, template=template,
                                  channel=channel, to=to, body=body, status=f"failed: {exc}"[:20])


# ---------------------------------------------------------------- customer messages

def customer_message(appt, template):
    b, when = appt.business, local_when(appt)
    what = f"{appt.service.name} with {appt.staff.display_name} at {b.name}"
    link = manage_link(appt)
    texts = {
        "confirmed": f"Booked! {what}, {when}. {b.address}\nManage your booking: {link}",
        "received": f"Request received: {what}, {when}. {b.name} will confirm shortly.\nManage: {link}",
        "declined": f"Sorry, {b.name} couldn't take your request for {when}. Book another time: "
                    f"{settings.PUBLIC_BASE_URL}/b/{b.slug}",
        "expired": f"Your request with {b.name} for {when} wasn't confirmed in time, so the slot was freed. "
                   f"Pick another time: {settings.PUBLIC_BASE_URL}/b/{b.slug}",
        "rescheduled": f"Your booking moved: {what}, now {when}.\nManage: {link}",
        "cancelled_by_provider": f"{b.name} cancelled your appointment on {when}. "
                                 f"{('Reason: ' + appt.cancel_reason + '. ') if appt.cancel_reason else ''}"
                                 f"Book again: {settings.PUBLIC_BASE_URL}/b/{b.slug}",
        "cancelled_by_client": f"Your appointment with {b.name} on {when} is cancelled.",
        "reminder_24h": f"Reminder: {what} tomorrow, {when}. {b.address}\nConfirm, reschedule or cancel: {link}",
        "reminder_2h": f"See you soon: {what} at {when}. {b.address}\nNeed to change? {link}",
    }
    return texts[template]


def notify_customer(appt, template):
    to, channel = recipient(appt)
    _log_send(appt, MessageLog.Audience.CUSTOMER, template, to, customer_message(appt, template), channel)


def notify_provider(appt, template):
    when = local_when(appt)
    who = appt.customer.name if appt.customer else "A client"
    texts = {
        "new_booking": f"New booking: {who} — {appt.service.name} with {appt.staff.display_name}, {when}.",
        "new_request": f"New request to approve: {who} — {appt.service.name}, {when}. "
                       f"It expires in {appt.business.booking_policy.approval_timeout_min} minutes.",
        "client_cancelled": f"{who} cancelled {appt.service.name} on {when}"
                            f"{' (late cancellation)' if appt.late_cancellation else ''}.",
        "client_rescheduled": f"{who} moved {appt.service.name} to {when}.",
    }
    for phone in provider_recipients(appt):
        _log_send(appt, MessageLog.Audience.PROVIDER, template, phone, texts[template])


# ---------------------------------------------------------------- reminders

def adjust_for_quiet_hours(send_at, starts_at, tz, offset, now):
    local = send_at.astimezone(tz)
    if QUIET_END <= local.time() < QUIET_START:
        return send_at
    if offset >= 12 * 60:
        if local.time() >= QUIET_START:
            moved = datetime.combine(local.date(), time(20, 30), tzinfo=tz)
        else:
            moved = datetime.combine(local.date(), QUIET_END, tzinfo=tz)
    else:
        day = local.date() if local.time() < QUIET_END else local.date() + timedelta(days=1)
        moved = datetime.combine(day, QUIET_END, tzinfo=tz)
        if starts_at - moved < timedelta(minutes=30):
            return None
    return moved if moved > now else None


def schedule_before_start(appt, now=None):
    now = now or timezone.now()
    Reminder.objects.filter(appointment=appt, status=Reminder.Status.SCHEDULED).update(
        status=Reminder.Status.CANCELLED)
    user = appt.customer.user if appt.customer and appt.customer.user_id else None
    tz = ZoneInfo(user.timezone if user else appt.business.timezone)
    created = []
    for offset in appt.business.booking_policy.reminder_offsets_min:
        send_at = appt.starts_at - timedelta(minutes=offset)
        if send_at <= now:
            continue
        send_at = adjust_for_quiet_hours(send_at, appt.starts_at, tz, offset, now)
        if send_at is None:
            continue
        created.append(Reminder.objects.create(appointment=appt, kind=Reminder.Kind.BEFORE_START,
                                               offset_min=offset, for_version=appt.version, send_at=send_at))
    return created


def cancel_reminders(appt):
    Reminder.objects.filter(appointment=appt, status=Reminder.Status.SCHEDULED).update(
        status=Reminder.Status.CANCELLED)


# ---------------------------------------------------------------- event fan-out

def handle_appointment_event(appt, event, meta):
    by_provider = appt.source != "online"
    if event == "appointment.confirmed":
        schedule_before_start(appt)
        if appt.customer and (appt.customer.phone or appt.customer.user_id):
            notify_customer(appt, "confirmed")
        if not by_provider:
            notify_provider(appt, "new_booking")
    elif event == "appointment.pending_approval":
        notify_customer(appt, "received")
        notify_provider(appt, "new_request")
    elif event in ("appointment.declined", "appointment.expired"):
        notify_customer(appt, event.split(".")[1])
    elif event == "appointment.rescheduled":
        if appt.status == S.CONFIRMED:
            schedule_before_start(appt)
        notify_customer(appt, "rescheduled")
        if meta.get("actor") and appt.booked_by_user_id and meta["actor"] == str(appt.booked_by_user_id):
            notify_provider(appt, "client_rescheduled")
    elif event == "appointment.cancelled_by_client":
        cancel_reminders(appt)
        notify_customer(appt, "cancelled_by_client")
        notify_provider(appt, "client_cancelled")
    elif event == "appointment.cancelled_by_provider":
        cancel_reminders(appt)
        notify_customer(appt, "cancelled_by_provider")
    elif event in ("appointment.completed", "appointment.no_show", "appointment.released"):
        cancel_reminders(appt)


def reminder_template(r):
    return "reminder_24h" if (r.offset_min or 0) >= 12 * 60 else "reminder_2h"

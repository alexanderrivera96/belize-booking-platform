"""Reminder sending loop: claim in a short transaction, send outside it (booking-engine design §7)."""
import logging
from datetime import timedelta

from celery import shared_task
from django.db import transaction
from django.db.models import F
from django.utils import timezone

from booking.models import S

from .models import MessageLog, Reminder
from .senders import TransientSendError, send
from .services import customer_message, recipient, reminder_template

log = logging.getLogger("notifications.tasks")


@shared_task
def send_due_reminders(batch: int = 200):
    now = timezone.now()
    with transaction.atomic():
        ids = list(Reminder.objects.select_for_update(skip_locked=True)
                   .filter(status=Reminder.Status.SCHEDULED, send_at__lte=now)
                   .order_by("send_at").values_list("id", flat=True)[:batch])
        Reminder.objects.filter(id__in=ids).update(status=Reminder.Status.SENDING, claimed_at=now)
    for rid in ids:
        deliver_reminder.delay(rid)
    # crash recovery: rows stuck in "sending" for 10+ minutes go back to "scheduled"
    Reminder.objects.filter(status=Reminder.Status.SENDING, claimed_at__lt=now - timedelta(minutes=10)) \
        .update(status=Reminder.Status.SCHEDULED)
    return len(ids)


@shared_task(bind=True, max_retries=3, default_retry_delay=120)
def deliver_reminder(self, reminder_id):
    r = (Reminder.objects.select_related("appointment", "appointment__business", "appointment__business__booking_policy",
                                         "appointment__service", "appointment__staff", "appointment__customer",
                                         "appointment__customer__user")
         .get(id=reminder_id))
    appt = r.appointment
    still_valid = (r.kind == Reminder.Kind.BEFORE_START and appt.status == S.CONFIRMED
                   and appt.version == r.for_version) or \
                  (r.kind == Reminder.Kind.DEPOSIT_DUE and appt.status == S.AWAITING_DEPOSIT)
    to, channel = recipient(appt)
    if not still_valid or not to:
        Reminder.objects.filter(id=r.id).update(status=Reminder.Status.SKIPPED)
        return "skipped"
    template = reminder_template(r)
    body = customer_message(appt, template)
    try:
        result = send(to, body, channel)
    except TransientSendError as exc:
        Reminder.objects.filter(id=r.id).update(attempts=F("attempts") + 1, last_error=str(exc))
        if self.request.retries >= self.max_retries:
            Reminder.objects.filter(id=r.id).update(status=Reminder.Status.FAILED)
            log.error("Reminder %s failed permanently", r.id)
            return "failed"
        raise self.retry(exc=exc)
    except Exception as exc:
        Reminder.objects.filter(id=r.id).update(status=Reminder.Status.FAILED, last_error=str(exc))
        log.exception("Reminder %s failed", r.id)
        return "failed"
    Reminder.objects.filter(id=r.id).update(status=Reminder.Status.SENT, sent_at=timezone.now(),
                                            channel_used=result.channel, provider_message_id=result.message_id)
    MessageLog.objects.create(appointment=appt, business=appt.business, audience="customer", template=template,
                              channel=result.channel, to=to, body=body, provider_message_id=result.message_id)
    return "sent"

"""Celery tasks: event fan-out and the every-minute sweeps (booking-engine design §8).
Sweeps lock rows with skip_locked and change state only through transition()."""
import logging
from datetime import timedelta

from celery import shared_task
from django.db import transaction
from django.utils import timezone

from .models import Appointment, S

log = logging.getLogger("booking.tasks")


@shared_task
def dispatch_event(appt_id, event, version, meta=None):
    from notifications.services import handle_appointment_event

    appt = (Appointment.objects.select_related("business", "business__booking_policy", "staff", "service",
                                               "customer", "customer__user")
            .filter(id=appt_id).first())
    if appt is None:
        return
    if appt.version > version + 1 and event != "appointment.rescheduled":
        log.info("Skipping stale event %s for %s (v%s, now v%s)", event, appt_id, version, appt.version)
        return
    handle_appointment_event(appt, event, meta or {})


@shared_task
def sweep_expired_holds():
    cutoff = timezone.now() - timedelta(minutes=1)
    n, _ = Appointment.objects.filter(status=S.HELD, hold_expires_at__lt=cutoff).delete()
    return n


@shared_task
def expire_pending_approvals(batch: int = 200):
    from .lifecycle import transition

    now = timezone.now()
    n = 0
    with transaction.atomic():
        for appt in (Appointment.objects.select_for_update(skip_locked=True)
                     .filter(status=S.PENDING_APPROVAL, approval_expires_at__lte=now)[:batch]):
            transition(appt, S.EXPIRED, reason="not approved in time")
            n += 1
    return n


@shared_task
def auto_complete(batch: int = 500):
    """B8: confirmed appointments that ended 12+ hours ago and weren't marked → completed."""
    from .lifecycle import transition

    cutoff = timezone.now() - timedelta(hours=12)
    n = 0
    with transaction.atomic():
        for appt in (Appointment.objects.select_for_update(skip_locked=True)
                     .filter(status=S.CONFIRMED, ends_at__lte=cutoff)[:batch]):
            transition(appt, S.COMPLETED, reason="auto")
            n += 1
    return n

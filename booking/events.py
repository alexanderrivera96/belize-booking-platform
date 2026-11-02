"""Every state change commits first, then enqueues work (architecture plan: on_commit → Celery)."""
from django.db import transaction


def emit(appt, event, **meta):
    from .tasks import dispatch_event

    appt_id, version = str(appt.id), appt.version
    transaction.on_commit(lambda: dispatch_event.delay(appt_id, event, version, meta))

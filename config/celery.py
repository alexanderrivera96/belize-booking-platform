import os

from celery import Celery
from celery.schedules import crontab

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

app = Celery("booking_platform")
app.config_from_object("django.conf:settings", namespace="CELERY")
app.autodiscover_tasks()

# Run exactly ONE beat process (booking-engine design §8).
app.conf.beat_schedule = {
    "sweep-expired-holds": {"task": "booking.tasks.sweep_expired_holds", "schedule": 60},
    "expire-pending-approvals": {"task": "booking.tasks.expire_pending_approvals", "schedule": 60},
    "send-due-reminders": {"task": "notifications.tasks.send_due_reminders", "schedule": 60},
    "auto-complete-appointments": {"task": "booking.tasks.auto_complete", "schedule": crontab(minute=7)},
    # Payment sweeps (release_unpaid_deposits, poll_pending_payments) are added when PAYMENTS_ENABLED.
}

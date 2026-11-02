from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from django.test import TestCase, override_settings
from django.utils import timezone

from booking import lifecycle
from booking.models import S
from notifications.models import MessageLog, Reminder
from notifications.services import adjust_for_quiet_hours
from notifications.tasks import deliver_reminder, send_due_reminders

from .factories import at, customer, future_day, make_business

BZ = ZoneInfo("America/Belize")


class QuietHoursTests(TestCase):
    """Booking-engine design §7 examples (Belize time)."""

    def run_case(self, start_local, offset):
        start = start_local.replace(tzinfo=BZ)
        now = start - timedelta(days=3)
        out = adjust_for_quiet_hours(start - timedelta(minutes=offset), start, BZ, offset, now)
        return out.astimezone(BZ).strftime("%a %H:%M") if out else None

    def test_examples(self):
        fri = lambda h, m=0: datetime(2026, 11, 13, h, m)  # noqa: E731  (a Friday)
        self.assertEqual(self.run_case(fri(10), 1440), "Thu 10:00")
        self.assertEqual(self.run_case(fri(10), 120), "Fri 08:00")
        self.assertEqual(self.run_case(fri(7, 30), 1440), "Thu 08:00")
        self.assertIsNone(self.run_case(fri(7, 30), 120))
        self.assertEqual(self.run_case(fri(22), 1440), "Thu 20:30")
        self.assertEqual(self.run_case(fri(22), 120), "Fri 20:00")
        self.assertEqual(self.run_case(fri(9), 120), "Fri 08:00")


@override_settings(CELERY_TASK_ALWAYS_EAGER=True)
class ReminderFlowTests(TestCase):
    def setUp(self):
        self.b, self.svc, (self.st,), _ = make_business()
        h = lifecycle.create_hold(business=self.b, service=self.svc, staff_candidates=[self.st],
                                  starts_at=at(future_day(3), 11), checkout_token="t")
        with self.captureOnCommitCallbacks(execute=True):
            self.appt, _ = lifecycle.confirm_hold(hold_id=h.id, checkout_token="t", user=customer())

    def test_confirmation_schedules_two_reminders_and_messages(self):
        self.assertEqual(Reminder.objects.filter(appointment=self.appt, status="scheduled").count(), 2)
        self.assertTrue(MessageLog.objects.filter(appointment=self.appt, template="confirmed").exists())
        self.assertTrue(MessageLog.objects.filter(appointment=self.appt, template="new_booking").exists())

    def test_reschedule_replaces_reminders(self):
        with self.captureOnCommitCallbacks(execute=True):
            lifecycle.reschedule(self.appt, at(future_day(4), 11))
        self.assertEqual(Reminder.objects.filter(appointment=self.appt, status="scheduled").count(), 2)
        self.assertEqual(Reminder.objects.filter(appointment=self.appt, status="cancelled").count(), 2)

    def test_cancel_cancels_reminders(self):
        with self.captureOnCommitCallbacks(execute=True):
            lifecycle.cancel_by_client(self.appt)
        self.assertEqual(Reminder.objects.filter(appointment=self.appt, status="scheduled").count(), 0)

    def test_due_reminder_is_sent_once(self):
        r = Reminder.objects.filter(appointment=self.appt).first()
        Reminder.objects.filter(id=r.id).update(send_at=timezone.now() - timedelta(minutes=1))
        send_due_reminders()
        r.refresh_from_db()
        self.assertEqual(r.status, "sent")
        send_due_reminders()
        self.assertEqual(MessageLog.objects.filter(template__startswith="reminder").count(), 1)

    def test_stale_version_is_skipped(self):
        r = Reminder.objects.filter(appointment=self.appt).first()
        Reminder.objects.filter(id=r.id).update(for_version=0, status="sending")
        self.assertEqual(deliver_reminder(r.id), "skipped")

"""Booking engine tests that must exist before launch (booking-engine design §11)."""
import threading
from datetime import timedelta

from django.db import connection, IntegrityError
from django.test import TestCase, TransactionTestCase, override_settings
from django.utils import timezone

from booking import lifecycle
from booking.availability import available_slots, subtract
from booking.lifecycle import BookingError, InvalidTransition, SlotTaken
from booking.models import Appointment, S

from .factories import at, customer, future_day, make_business


@override_settings(CELERY_TASK_ALWAYS_EAGER=True)
class HoldAndConstraintTests(TestCase):
    def setUp(self):
        self.b, self.svc, (self.st,), _ = make_business()
        self.day = future_day()

    def hold(self, start, token="t", staff=None):
        return lifecycle.create_hold(business=self.b, service=self.svc, staff_candidates=[staff or self.st],
                                     starts_at=start, checkout_token=token)

    def test_hold_blocks_the_slot_for_others(self):
        self.hold(at(self.day, 10), "a")
        with self.assertRaises(SlotTaken):
            self.hold(at(self.day, 10), "b")

    def test_overlap_by_buffer_minutes_is_refused(self):
        self.svc.buffer_after_min = 10
        self.svc.save()
        self.hold(at(self.day, 10), "a")                 # blocks 10:00–10:40
        with self.assertRaises(SlotTaken):
            self.hold(at(self.day, 10, 30), "b")

    def test_back_to_back_is_allowed(self):
        self.hold(at(self.day, 10), "a")
        self.hold(at(self.day, 10, 30), "b")             # half-open ranges
        self.assertEqual(Appointment.objects.filter(status=S.HELD).count(), 2)

    def test_one_hold_per_checkout(self):
        self.hold(at(self.day, 10), "a")
        self.hold(at(self.day, 11), "a")
        self.assertEqual(Appointment.objects.filter(checkout_token="a").count(), 1)

    def test_lapsed_hold_is_free_and_original_confirm_conflicts(self):
        h = self.hold(at(self.day, 10), "a")
        Appointment.objects.filter(id=h.id).update(hold_expires_at=timezone.now() - timedelta(minutes=1))
        self.hold(at(self.day, 10), "b")                 # replaces the lapsed hold
        with self.assertRaises(SlotTaken):
            lifecycle.confirm_hold(hold_id=h.id, checkout_token="a", user=customer())

    def test_confirm_is_idempotent(self):
        h = self.hold(at(self.day, 10), "a")
        u = customer()
        a1, _ = lifecycle.confirm_hold(hold_id=h.id, checkout_token="a", user=u, idempotency_key="k")
        a2, _ = lifecycle.confirm_hold(hold_id=h.id, checkout_token="a", user=u, idempotency_key="k")
        self.assertEqual(a1.id, a2.id)
        self.assertEqual(a1.status, S.CONFIRMED)         # payments off → deposit 0 → confirmed

    def test_constraint_rejects_direct_overlap(self):
        lifecycle.create_provider_booking(business=self.b, staff=self.st, service=self.svc,
                                          customer=self._client(), starts_at=at(self.day, 12), member=None)
        with self.assertRaises(SlotTaken):
            lifecycle.create_provider_booking(business=self.b, staff=self.st, service=self.svc,
                                              customer=self._client(), starts_at=at(self.day, 12, 15), member=None)

    def test_cancelled_booking_frees_the_slot(self):
        a = lifecycle.create_provider_booking(business=self.b, staff=self.st, service=self.svc,
                                              customer=self._client(), starts_at=at(self.day, 12), member=None)
        lifecycle.cancel_by_provider(a.id, business=self.b, actor=None)
        self.hold(at(self.day, 12), "z")

    def _client(self):
        from clients.models import Customer
        return Customer.objects.create(business=self.b, name="Walk in", source="walk_in")


@override_settings(CELERY_TASK_ALWAYS_EAGER=True)
class AvailabilityTests(TestCase):
    def test_subtract(self):
        from datetime import datetime, timezone as tz
        d = lambda h: datetime(2026, 1, 1, h, tzinfo=tz.utc)  # noqa: E731
        self.assertEqual(subtract([(d(9), d(17))], [(d(10), d(11)), (d(12), d(13))]),
                         [(d(9), d(10)), (d(11), d(12)), (d(13), d(17))])

    def test_slots_respect_hours_and_bookings(self):
        b, svc, (st,), _ = make_business(hours=(9, 11))
        day = future_day()
        starts = [s for s, _ in available_slots(b, svc, st, day)]
        self.assertEqual(starts[0], at(day, 9))
        self.assertEqual(starts[-1], at(day, 10, 30))   # 30-min service must end by 11:00
        lifecycle.create_hold(business=b, service=svc, staff_candidates=[st], starts_at=at(day, 9, 30),
                              checkout_token="x")
        starts = [s for s, _ in available_slots(b, svc, st, day)]
        self.assertNotIn(at(day, 9, 15), starts)
        self.assertNotIn(at(day, 9, 45), starts)
        self.assertIn(at(day, 10), starts)

    def test_time_off_removes_slots(self):
        from businesses.models import TimeOff
        b, svc, (st,), _ = make_business(hours=(9, 12))
        day = future_day()
        TimeOff.objects.create(staff=st, starts_at=at(day, 9), ends_at=at(day, 11))
        self.assertEqual([s for s, _ in available_slots(b, svc, st, day)][0], at(day, 11))

    def test_any_available_merges_staff(self):
        b, svc, staff, _ = make_business(staff_names=("A", "B"), hours=(9, 10))
        day = future_day()
        lifecycle.create_hold(business=b, service=svc, staff_candidates=[staff[0]], starts_at=at(day, 9),
                              checkout_token="x")
        slots = dict(available_slots(b, svc, None, day))
        self.assertEqual(slots[at(day, 9)], [staff[1].id])

    def test_dst_zone_is_handled(self):
        """Engine correctness beyond Belize: New York on the November 2026 change date."""
        from datetime import date
        b, svc, (st,), _ = make_business(hours=(9, 10))
        b.timezone = "America/New_York"
        b.save()
        b.booking_policy.max_advance_days = 3650
        b.booking_policy.save()
        from datetime import datetime
        from zoneinfo import ZoneInfo
        d = date(2027, 11, 7)
        now = datetime(2027, 11, 1, tzinfo=ZoneInfo("UTC"))
        starts = [s for s, _ in available_slots(b, svc, st, d, now=now)]
        self.assertEqual(starts[0], datetime(2027, 11, 7, 9, tzinfo=ZoneInfo("America/New_York")))
        self.assertEqual(starts[0].astimezone(ZoneInfo("UTC")).hour, 14)   # EST (UTC-5) after the change


@override_settings(CELERY_TASK_ALWAYS_EAGER=True)
class StateMachineTests(TestCase):
    def setUp(self):
        self.b, self.svc, (self.st,), self.owner = make_business(approval=True)
        self.day = future_day()

    def booked(self):
        h = lifecycle.create_hold(business=self.b, service=self.svc, staff_candidates=[self.st],
                                  starts_at=at(self.day, 10), checkout_token="t")
        a, _ = lifecycle.confirm_hold(hold_id=h.id, checkout_token="t", user=customer())
        return a

    def test_approval_flow(self):
        a = self.booked()
        self.assertEqual(a.status, S.PENDING_APPROVAL)
        a = lifecycle.approve(a.id, business=self.b, actor=self.owner)
        self.assertEqual(a.status, S.CONFIRMED)

    def test_decline_frees_slot(self):
        a = self.booked()
        lifecycle.decline(a.id, business=self.b, actor=self.owner, reason="Fully booked")
        lifecycle.create_hold(business=self.b, service=self.svc, staff_candidates=[self.st],
                              starts_at=at(self.day, 10), checkout_token="t2")

    def test_disallowed_transitions_raise(self):
        a = self.booked()
        with self.assertRaises(BookingError):
            lifecycle.mark_completed(a.id, business=self.b, actor=self.owner)  # pending + in the future
        lifecycle.decline(a.id, business=self.b, actor=self.owner)
        with self.assertRaises(InvalidTransition):
            lifecycle.approve(a.id, business=self.b, actor=self.owner)

    def test_expiry_sweep(self):
        from booking.tasks import expire_pending_approvals
        a = self.booked()
        Appointment.objects.filter(id=a.id).update(approval_expires_at=timezone.now() - timedelta(minutes=1))
        self.assertEqual(expire_pending_approvals(), 1)
        a.refresh_from_db()
        self.assertEqual(a.status, S.EXPIRED)

    def test_other_business_cannot_act(self):
        a = self.booked()
        other, *_ = make_business()
        with self.assertRaises(Appointment.DoesNotExist):
            lifecycle.approve(a.id, business=other, actor=self.owner)

    def test_customer_cancel_inside_window_is_flagged(self):
        b, svc, (st,), _ = make_business()
        start = timezone.now() + timedelta(hours=3)
        start = start.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
        c = customer()
        from clients.models import Customer
        cl = Customer.objects.create(business=b, name="X", user=c, source="online", linked_at=timezone.now())
        a = lifecycle.create_provider_booking(business=b, staff=st, service=svc, customer=cl, starts_at=start,
                                              member=None)
        a = lifecycle.cancel_by_client(a, actor=c)
        self.assertTrue(a.late_cancellation)
        self.assertEqual(a.status, S.CANCELLED_BY_CLIENT)


class RaceTest(TransactionTestCase):
    """20 concurrent holds for the same staff and slot: exactly one wins."""

    def test_concurrent_holds(self):
        b, svc, (st,), _ = make_business()
        start = at(future_day(), 10)
        wins, losses, errors = [], [], []

        def worker(i):
            try:
                lifecycle.create_hold(business=b, service=svc, staff_candidates=[st], starts_at=start,
                                      checkout_token=f"tok{i}")
                wins.append(i)
            except SlotTaken:
                losses.append(i)
            except Exception as e:      # pragma: no cover
                errors.append(e)
            finally:
                connection.close()

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(errors, [])
        self.assertEqual(len(wins), 1)
        self.assertEqual(len(losses), 19)

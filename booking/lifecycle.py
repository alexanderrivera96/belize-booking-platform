"""Appointment state machine and every operation that changes a booking
(booking-engine design §3, §5, §6). All status changes go through transition()."""
import random
from datetime import timedelta
from zoneinfo import ZoneInfo

from django.conf import settings
from django.db import IntegrityError, transaction
from django.utils import timezone

from businesses.models import StaffService
from clients.services import client_for_online_booking

from .availability import booked_minutes_on, slot_is_offered
from .events import emit
from .models import (BLOCKING, Appointment, AppointmentAccessToken, S, Source, blocked,
                     is_overlap_violation)

HOLD_MINUTES = 10
MAX_UNPAID_PLATFORM = 2      # B2
MAX_PENDING_PER_BUSINESS = 3

ALLOWED = {
    S.HELD: {S.PENDING_APPROVAL, S.AWAITING_DEPOSIT, S.CONFIRMED},
    S.PENDING_APPROVAL: {S.AWAITING_DEPOSIT, S.CONFIRMED, S.DECLINED, S.EXPIRED, S.CANCELLED_BY_CLIENT,
                         S.CANCELLED_BY_PROVIDER},
    S.AWAITING_DEPOSIT: {S.CONFIRMED, S.RELEASED, S.CANCELLED_BY_CLIENT, S.CANCELLED_BY_PROVIDER},
    S.CONFIRMED: {S.COMPLETED, S.NO_SHOW, S.CANCELLED_BY_CLIENT, S.CANCELLED_BY_PROVIDER},
    S.RELEASED: {S.CONFIRMED},
    S.NO_SHOW: {S.COMPLETED},
}


class BookingError(Exception):
    """A rule stopped the action; the message is safe to show."""


class SlotTaken(BookingError):
    def __init__(self, msg="Sorry, that time was just taken. Please pick another time."):
        super().__init__(msg)


class InvalidTransition(BookingError):
    pass


def transition(appt: Appointment, to: str, *, actor=None, reason: str = "") -> None:
    """Caller holds a row lock (select_for_update) inside transaction.atomic()."""
    if to not in ALLOWED.get(appt.status, set()):
        raise InvalidTransition(f"This booking can't go from {appt.get_status_display().lower()} "
                                f"to {S(to).label.lower()}.")
    now = timezone.now()
    previous, appt.status = appt.status, to
    if to == S.CONFIRMED:
        appt.confirmed_at = now
    if to in (S.CANCELLED_BY_CLIENT, S.CANCELLED_BY_PROVIDER, S.DECLINED):
        appt.cancelled_at, appt.cancel_reason = now, reason[:200]
    appt.version += 1
    appt.save()
    emit(appt, f"appointment.{to}", previous=previous, actor=str(actor.pk) if actor else None)


# ---------------------------------------------------------------- deposits (payments off for now)

def deposit_amount(price_cents: int, policy) -> int:
    if not settings.PAYMENTS_ENABLED:
        return 0
    if policy.deposit_type == "fixed":
        return min(policy.deposit_value, price_cents)
    return (price_cents * policy.deposit_value + 50) // 100


def start_deposit_clock(appt, now, policy) -> None:
    appt.deposit_clock_started_at = now
    due = min(now + timedelta(hours=policy.deposit_window_hours),
              appt.starts_at - timedelta(minutes=policy.deposit_cutoff_before_start_min))
    if due - now < timedelta(minutes=60):
        due = min(now + timedelta(minutes=15), appt.starts_at)
    appt.deposit_due_at = due


def _after_acceptance(appt, now, policy, actor):
    """Instant booking or provider approval: confirm, or wait for the deposit."""
    if appt.deposit_cents == 0:
        transition(appt, S.CONFIRMED, actor=actor)
    else:
        start_deposit_clock(appt, now, policy)
        transition(appt, S.AWAITING_DEPOSIT, actor=actor)


# ---------------------------------------------------------------- holds

def order_candidates(business, service, candidates, starts_at):
    """B7: fewest booked minutes that day first; ties broken randomly."""
    if len(candidates) < 2:
        return candidates
    tz = ZoneInfo(business.timezone)
    load = booked_minutes_on([c.id for c in candidates], starts_at.astimezone(tz).date(), tz)
    shuffled = candidates[:]
    random.shuffle(shuffled)
    return sorted(shuffled, key=lambda c: load.get(c.id, 0))


def create_hold(*, business, service, staff_candidates, starts_at, checkout_token, now=None) -> Appointment:
    now = now or timezone.now()
    if not business.is_bookable:
        raise BookingError("This business isn't taking online bookings right now.")
    previous = Appointment.objects.filter(status=S.HELD, checkout_token=checkout_token).values_list("id", flat=True)
    previous_id = previous.first()
    policy = business.booking_policy
    for staff in order_candidates(business, service, list(staff_candidates), starts_at):
        try:
            ss = StaffService.objects.select_related("service").get(staff=staff, service=service)
        except StaffService.DoesNotExist:
            continue
        if not slot_is_offered(staff, service, starts_at, now, exclude_appointment_id=previous_id):
            continue
        ends_at = starts_at + timedelta(minutes=ss.duration_min)
        rng = blocked(starts_at, ends_at, service.buffer_before_min, service.buffer_after_min)
        try:
            with transaction.atomic():
                Appointment.objects.filter(status=S.HELD, checkout_token=checkout_token).delete()
                Appointment.objects.filter(staff=staff, status=S.HELD, hold_expires_at__lt=now,
                                           blocked_range__overlap=rng).delete()
                return Appointment.objects.create(
                    business=business, staff=staff, service=service, status=S.HELD,
                    starts_at=starts_at, ends_at=ends_at, blocked_range=rng,
                    duration_min=ss.duration_min, price_cents=ss.price_cents,
                    buffer_before_min=service.buffer_before_min, buffer_after_min=service.buffer_after_min,
                    deposit_cents=deposit_amount(ss.price_cents, policy),
                    hold_expires_at=now + timedelta(minutes=HOLD_MINUTES), checkout_token=checkout_token,
                )
        except IntegrityError as exc:
            if is_overlap_violation(exc):
                continue
            raise
    raise SlotTaken()


def release_hold(*, hold_id, checkout_token):
    Appointment.objects.filter(id=hold_id, status=S.HELD, checkout_token=checkout_token).delete()


def enforce_unpaid_limits(user, business):
    if Appointment.objects.filter(booked_by_user=user, status=S.AWAITING_DEPOSIT).count() >= MAX_UNPAID_PLATFORM:
        raise BookingError("You have bookings waiting for a deposit. Please pay or cancel one first.")
    if Appointment.objects.filter(booked_by_user=user, business=business,
                                  status=S.PENDING_APPROVAL).count() >= MAX_PENDING_PER_BUSINESS:
        raise BookingError("You already have several requests waiting for this business to approve.")


def confirm_hold(*, hold_id, checkout_token, user, customer_notes="", idempotency_key=None, now=None):
    """Turns a hold into a booking. Returns (appointment, raw_access_token or None)."""
    now = now or timezone.now()
    with transaction.atomic():
        if idempotency_key:
            prior = Appointment.objects.filter(booked_by_user=user, idempotency_key=idempotency_key).first()
            if prior:
                return prior, None
        appt = (Appointment.objects.select_for_update(of=("self",)).select_related("business__booking_policy")
                .filter(id=hold_id, status=S.HELD, checkout_token=checkout_token).first())
        if appt is None:
            raise SlotTaken("Your hold on that time ran out and someone else booked it. Please pick another time.")
        enforce_unpaid_limits(user, appt.business)
        policy = appt.business.booking_policy
        appt.customer = client_for_online_booking(appt.business, user)
        appt.booked_by_user, appt.source = user, Source.ONLINE
        appt.hold_expires_at, appt.checkout_token = None, ""
        appt.idempotency_key, appt.customer_notes = idempotency_key, customer_notes[:1000]
        if policy.requires_approval:
            appt.approval_expires_at = min(now + timedelta(minutes=policy.approval_timeout_min), appt.starts_at)
            transition(appt, S.PENDING_APPROVAL, actor=user)
        else:
            _after_acceptance(appt, now, policy, user)
        raw = AppointmentAccessToken.issue(appt)
        return appt, raw


# ---------------------------------------------------------------- provider actions

def _locked(appt_id, business=None):
    qs = Appointment.objects.select_for_update(of=("self",)).select_related("business__booking_policy")
    if business is not None:
        qs = qs.filter(business=business)
    return qs.get(id=appt_id)


def approve(appt_id, *, business, actor):
    with transaction.atomic():
        appt = _locked(appt_id, business)
        if appt.status != S.PENDING_APPROVAL:
            raise InvalidTransition("Only requests awaiting approval can be approved.")
        _after_acceptance(appt, timezone.now(), appt.business.booking_policy, actor)
        return appt


def decline(appt_id, *, business, actor, reason=""):
    with transaction.atomic():
        appt = _locked(appt_id, business)
        transition(appt, S.DECLINED, actor=actor, reason=reason)
        return appt


def cancel_by_provider(appt_id, *, business, actor, reason=""):
    with transaction.atomic():
        appt = _locked(appt_id, business)
        transition(appt, S.CANCELLED_BY_PROVIDER, actor=actor, reason=reason)
        return appt


def mark_no_show(appt_id, *, business, actor):
    with transaction.atomic():
        appt = _locked(appt_id, business)
        if appt.starts_at > timezone.now():
            raise BookingError("You can only mark a no-show after the start time.")
        transition(appt, S.NO_SHOW, actor=actor)
        return appt


def mark_completed(appt_id, *, business, actor):
    with transaction.atomic():
        appt = _locked(appt_id, business)
        if appt.starts_at > timezone.now():
            raise BookingError("You can only complete an appointment after it has started.")
        transition(appt, S.COMPLETED, actor=actor)
        return appt


def cancel_by_client(appt: Appointment, *, actor=None, reason=""):
    with transaction.atomic():
        appt = _locked(appt.id)
        if appt.starts_at <= timezone.now():
            raise BookingError("This appointment has already started.")
        appt.late_cancellation = appt.inside_cancel_window()
        transition(appt, S.CANCELLED_BY_CLIENT, actor=actor, reason=reason)
        return appt


def reschedule(appt: Appointment, new_start, *, actor=None, by_provider=False, new_staff=None):
    """Customers must pick an offered slot; providers may go outside working hours but never
    past the exclusion constraint."""
    with transaction.atomic():
        appt = _locked(appt.id)
        if appt.status not in (S.PENDING_APPROVAL, S.AWAITING_DEPOSIT, S.CONFIRMED):
            raise BookingError("This booking can't be moved.")
        if not by_provider and appt.starts_at <= timezone.now():
            raise BookingError("This appointment has already started.")
        staff = new_staff or appt.staff
        if not by_provider and not slot_is_offered(staff, appt.service, new_start, exclude_appointment_id=appt.id):
            raise SlotTaken("That time isn't available. Please pick another time.")
        appt.staff = staff
        appt.starts_at = new_start
        appt.ends_at = new_start + timedelta(minutes=appt.duration_min)
        appt.blocked_range = blocked(appt.starts_at, appt.ends_at, appt.buffer_before_min, appt.buffer_after_min)
        if appt.deposit_due_at and appt.deposit_due_at > appt.starts_at:
            appt.deposit_due_at = appt.starts_at
        appt.version += 1
        try:
            with transaction.atomic():
                appt.save()
        except IntegrityError as exc:
            if is_overlap_violation(exc):
                raise SlotTaken("That time overlaps another booking.")
            raise
        emit(appt, "appointment.rescheduled", actor=str(actor.pk) if actor else None)
        return appt


def create_provider_booking(*, business, staff, service, customer, starts_at, member, source=Source.WALK_IN,
                            allow_overlap=False, notes=""):
    """Walk-in / phone bookings: created directly as confirmed, no deposit (B9)."""
    ss = StaffService.objects.filter(staff=staff, service=service).first()
    duration = ss.duration_min if ss else service.duration_min
    price = ss.price_cents if ss else service.price_cents
    ends_at = starts_at + timedelta(minutes=duration)
    if allow_overlap and not business.booking_policy.allow_provider_overlap:
        allow_overlap = False
    try:
        with transaction.atomic():
            appt = Appointment.objects.create(
                business=business, staff=staff, service=service, customer=customer, status=S.CONFIRMED,
                source=source, created_by_member=member, starts_at=starts_at, ends_at=ends_at,
                blocked_range=blocked(starts_at, ends_at, service.buffer_before_min, service.buffer_after_min),
                allow_overlap=allow_overlap, duration_min=duration, price_cents=price,
                buffer_before_min=service.buffer_before_min, buffer_after_min=service.buffer_after_min,
                confirmed_at=timezone.now(), provider_notes=notes,
            )
    except IntegrityError as exc:
        if is_overlap_violation(exc):
            raise SlotTaken("That time overlaps another booking for this staff member.")
        raise
    emit(appt, "appointment.confirmed", actor=str(member.user_id))
    return appt


def clashes_with_time_off(appt) -> bool:
    return appt.staff.time_off.filter(starts_at__lt=appt.ends_at, ends_at__gt=appt.starts_at).exists()


__all__ = ["BLOCKING"]

"""Availability engine (booking-engine design §4). Slots are computed on request, never stored.

free time = working hours − time off − blocking appointments and live holds (with buffers)
"""
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone as dt_tz
from zoneinfo import ZoneInfo

from django.db.backends.postgresql.psycopg_any import DateTimeTZRange
from django.utils import timezone

from businesses.models import Schedule, Staff, StaffService, TimeOff

from .models import BLOCKING, Appointment, S

UTC = dt_tz.utc
Interval = tuple[datetime, datetime]


def working_windows(schedules, day: date, tz: ZoneInfo) -> list[Interval]:
    out = []
    for r in schedules:
        if r.weekday == day.weekday():
            s = datetime.combine(day, r.start_time, tzinfo=tz).astimezone(UTC)
            e = datetime.combine(day, r.end_time, tzinfo=tz).astimezone(UTC)
            out.append((s, e))
    return sorted(out)


def subtract(windows: list[Interval], busy: list[Interval]) -> list[Interval]:
    busy = sorted(busy)
    free = []
    for ws, we in windows:
        cur = ws
        for bs, be in busy:
            if be <= cur or bs >= we:
                continue
            if bs > cur:
                free.append((cur, bs))
            cur = max(cur, be)
            if cur >= we:
                break
        if cur < we:
            free.append((cur, we))
    return free


def aligned_starts(window: Interval, step_min: int, tz: ZoneInfo):
    """Start times on a local-clock grid (e.g. :00, :15, :30, :45)."""
    local = window[0].astimezone(tz)
    midnight = local.replace(hour=0, minute=0, second=0, microsecond=0)
    minutes = (local - midnight).total_seconds() / 60
    k = -(-minutes // step_min)
    t = (midnight + timedelta(minutes=k * step_min)).astimezone(UTC)
    while t < window[1]:
        yield t
        t += timedelta(minutes=step_min)


@dataclass(frozen=True)
class SlotRequest:
    duration_min: int
    buffer_before_min: int
    buffer_after_min: int


def slots_for_day(*, schedules, busy, day, tz, policy, req: SlotRequest, now: datetime,
                  enforce_policy: bool = True) -> list[datetime]:
    free = subtract(working_windows(schedules, day, tz), busy)
    earliest = now + timedelta(minutes=policy.min_notice_min) if enforce_policy else now
    latest = now + timedelta(days=policy.max_advance_days) if enforce_policy else now + timedelta(days=3650)
    dur, bb, ba = (timedelta(minutes=m) for m in (req.duration_min, req.buffer_before_min, req.buffer_after_min))
    out = []
    for w in free:
        for start in aligned_starts((w[0] + bb, w[1]), policy.slot_interval_min, tz):
            if start - bb >= w[0] and start + dur + ba <= w[1] and earliest <= start <= latest:
                out.append(start)
    return out


def day_bounds(day: date, tz: ZoneInfo) -> Interval:
    start = datetime.combine(day, time.min, tzinfo=tz)
    return start.astimezone(UTC), (start + timedelta(days=1)).astimezone(UTC)


def busy_intervals(staff_ids, day_start_utc, day_end_utc, now, exclude_appointment_id=None):
    """{staff_id: [(start, end), ...]} from appointments + live holds and time off."""
    rng = DateTimeTZRange(day_start_utc, day_end_utc, bounds="[)")
    out = defaultdict(list)
    appts = (Appointment.objects
             .filter(staff_id__in=staff_ids, status__in=BLOCKING, blocked_range__overlap=rng)
             .exclude(status=S.HELD, hold_expires_at__lt=now))
    if exclude_appointment_id:
        appts = appts.exclude(id=exclude_appointment_id)
    for staff_id, r in appts.values_list("staff_id", "blocked_range"):
        out[staff_id].append((r.lower, r.upper))
    for staff_id, s, e in TimeOff.objects.filter(staff_id__in=staff_ids, starts_at__lt=day_end_utc,
                                                  ends_at__gt=day_start_utc).values_list("staff_id", "starts_at",
                                                                                         "ends_at"):
        out[staff_id].append((s, e))
    return out


def bookable_staff(business, service):
    return list(Staff.objects.filter(business=business, active=True, bookable_online=True,
                                     staffservice__service=service).distinct())


def staff_slots(business, service, staff_list, day: date, now=None, *, enforce_policy=True,
                exclude_appointment_id=None) -> dict:
    """{staff_id: [start datetimes (UTC)]} for one day."""
    now = now or timezone.now()
    tz = ZoneInfo(business.timezone)
    policy = business.booking_policy
    ids = [s.id for s in staff_list]
    if not ids:
        return {}
    d0, d1 = day_bounds(day, tz)
    busy = busy_intervals(ids, d0, d1, now, exclude_appointment_id)
    schedules = defaultdict(list)
    for sch in Schedule.objects.filter(staff_id__in=ids, weekday=day.weekday()):
        schedules[sch.staff_id].append(sch)
    links = {ss.staff_id: ss for ss in StaffService.objects.select_related("service")
             .filter(staff_id__in=ids, service=service)}
    out = {}
    for sid in ids:
        ss = links.get(sid)
        if ss is None:
            continue
        req = SlotRequest(ss.duration_min, service.buffer_before_min, service.buffer_after_min)
        out[sid] = slots_for_day(schedules=schedules[sid], busy=busy[sid], day=day, tz=tz, policy=policy,
                                 req=req, now=now, enforce_policy=enforce_policy)
    return out


def available_slots(business, service, staff=None, day: date | None = None, now=None):
    """Union of start times for one staff member or 'any available'.
    Returns a sorted list of (start_utc, [staff_ids that can take it])."""
    now = now or timezone.now()
    staff_list = [staff] if staff else bookable_staff(business, service)
    by_staff = staff_slots(business, service, staff_list, day, now)
    merged = defaultdict(list)
    for sid, starts in by_staff.items():
        for st in starts:
            merged[st].append(sid)
    return sorted(merged.items())


def slot_is_offered(staff, service, starts_at, now=None, *, exclude_appointment_id=None) -> bool:
    now = now or timezone.now()
    tz = ZoneInfo(staff.business.timezone)
    day = starts_at.astimezone(tz).date()
    starts = staff_slots(staff.business, service, [staff], day, now,
                         exclude_appointment_id=exclude_appointment_id).get(staff.id, [])
    return starts_at in starts


def booked_minutes_on(staff_ids, day: date, tz: ZoneInfo) -> dict:
    d0, d1 = day_bounds(day, tz)
    out = defaultdict(int)
    for sid, dur in Appointment.objects.filter(staff_id__in=staff_ids, status__in=BLOCKING,
                                               starts_at__gte=d0, starts_at__lt=d1).values_list("staff_id",
                                                                                                "duration_min"):
        out[sid] += dur
    return out

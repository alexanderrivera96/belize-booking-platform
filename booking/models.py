"""Appointment and the exclusion constraint that makes double-booking impossible
(booking-engine design §2). Checkout holds are Appointment rows with status `held`."""
import hashlib
import secrets
import uuid
from datetime import timedelta

from django.contrib.postgres.constraints import ExclusionConstraint
from django.contrib.postgres.fields import DateTimeRangeField, RangeOperators
from django.db import IntegrityError, models
from django.db.backends.postgresql.psycopg_any import DateTimeTZRange
from django.db.models import F, Q
from django.utils import timezone


class AppointmentStatus(models.TextChoices):
    HELD = "held", "Held (checkout)"
    PENDING_APPROVAL = "pending_approval", "Awaiting approval"
    AWAITING_DEPOSIT = "awaiting_deposit", "Awaiting deposit"
    CONFIRMED = "confirmed", "Confirmed"
    COMPLETED = "completed", "Completed"
    NO_SHOW = "no_show", "No-show"
    DECLINED = "declined", "Declined"
    EXPIRED = "expired", "Expired (not approved in time)"
    RELEASED = "released", "Released (deposit not paid)"
    CANCELLED_BY_CLIENT = "cancelled_by_client", "Cancelled by client"
    CANCELLED_BY_PROVIDER = "cancelled_by_provider", "Cancelled by business"


S = AppointmentStatus
BLOCKING = [S.HELD, S.PENDING_APPROVAL, S.AWAITING_DEPOSIT, S.CONFIRMED, S.COMPLETED, S.NO_SHOW]
ACTIVE = [S.PENDING_APPROVAL, S.AWAITING_DEPOSIT, S.CONFIRMED]
CANCELLED = [S.CANCELLED_BY_CLIENT, S.CANCELLED_BY_PROVIDER]


class Source(models.TextChoices):
    ONLINE = "online", "Online"
    WALK_IN = "walk_in", "Walk-in"
    PHONE = "phone", "Phone"


def blocked(starts_at, ends_at, before_min, after_min) -> DateTimeTZRange:
    """Half-open [start - buffer_before, end + buffer_after): back-to-back bookings don't overlap."""
    return DateTimeTZRange(starts_at - timedelta(minutes=before_min),
                           ends_at + timedelta(minutes=after_min), bounds="[)")


def is_overlap_violation(exc: IntegrityError) -> bool:
    cause = exc.__cause__
    return getattr(cause, "sqlstate", None) == "23P01" or getattr(cause, "pgcode", None) == "23P01"


class Appointment(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    business = models.ForeignKey("businesses.Business", on_delete=models.PROTECT, related_name="appointments")
    staff = models.ForeignKey("businesses.Staff", on_delete=models.PROTECT, related_name="appointments")
    service = models.ForeignKey("businesses.Service", on_delete=models.PROTECT, related_name="appointments")
    customer = models.ForeignKey("clients.Customer", null=True, blank=True, on_delete=models.PROTECT,
                                 related_name="appointments")
    booked_by_user = models.ForeignKey("accounts.User", null=True, blank=True, on_delete=models.SET_NULL,
                                       related_name="bookings_made")
    created_by_member = models.ForeignKey("businesses.BusinessMember", null=True, blank=True,
                                          on_delete=models.SET_NULL, related_name="+")
    source = models.CharField(max_length=10, choices=Source.choices, default=Source.ONLINE)
    status = models.CharField(max_length=24, choices=AppointmentStatus.choices, db_index=True)

    starts_at = models.DateTimeField()
    ends_at = models.DateTimeField()
    blocked_range = DateTimeRangeField()
    allow_overlap = models.BooleanField(default=False)

    # Snapshot at booking time
    duration_min = models.PositiveSmallIntegerField()
    buffer_before_min = models.PositiveSmallIntegerField(default=0)
    buffer_after_min = models.PositiveSmallIntegerField(default=0)
    price_cents = models.PositiveIntegerField()
    deposit_cents = models.PositiveIntegerField(default=0)

    hold_expires_at = models.DateTimeField(null=True, blank=True)
    checkout_token = models.CharField(max_length=64, blank=True)
    approval_expires_at = models.DateTimeField(null=True, blank=True)
    deposit_clock_started_at = models.DateTimeField(null=True, blank=True)
    deposit_due_at = models.DateTimeField(null=True, blank=True)
    confirmed_at = models.DateTimeField(null=True, blank=True)
    cancelled_at = models.DateTimeField(null=True, blank=True)
    cancel_reason = models.CharField(max_length=200, blank=True)
    late_cancellation = models.BooleanField(default=False, help_text="Cancelled inside the business's window.")

    idempotency_key = models.CharField(max_length=80, null=True, blank=True)
    customer_notes = models.TextField(blank=True)
    provider_notes = models.TextField(blank=True)
    version = models.PositiveIntegerField(default=1)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["starts_at"]
        constraints = [
            ExclusionConstraint(
                name="appt_no_overlap_per_staff",
                expressions=[("staff", RangeOperators.EQUAL), ("blocked_range", RangeOperators.OVERLAPS)],
                condition=Q(status__in=BLOCKING) & Q(allow_overlap=False),
            ),
            models.CheckConstraint(condition=Q(starts_at__lt=F("ends_at")), name="appt_start_before_end"),
            models.CheckConstraint(condition=~Q(status="held") | Q(hold_expires_at__isnull=False),
                                   name="appt_hold_has_expiry"),
            models.CheckConstraint(condition=Q(status="held") | Q(customer__isnull=False),
                                   name="appt_customer_after_hold"),
            models.UniqueConstraint(fields=["booked_by_user", "idempotency_key"],
                                    condition=Q(idempotency_key__isnull=False), name="appt_idempotency"),
        ]
        indexes = [
            models.Index(fields=["business", "starts_at"]),
            models.Index(fields=["staff", "starts_at"]),
            models.Index(fields=["status", "hold_expires_at"]),
            models.Index(fields=["status", "approval_expires_at"]),
            models.Index(fields=["status", "deposit_due_at"]),
        ]

    def __str__(self):
        return f"{self.service} with {self.staff} at {self.starts_at:%Y-%m-%d %H:%M}"

    @property
    def is_active(self):
        return self.status in ACTIVE

    @property
    def is_upcoming(self):
        return self.is_active and self.starts_at > timezone.now()

    @property
    def is_cancelled(self):
        return self.status in CANCELLED

    def inside_cancel_window(self, now=None):
        now = now or timezone.now()
        hours = self.business.booking_policy.cancel_window_hours
        return self.starts_at - now < timedelta(hours=hours)


class AppointmentAccessToken(models.Model):
    """Manage-booking links without login. Only the SHA-256 hash is stored."""
    appointment = models.ForeignKey(Appointment, on_delete=models.CASCADE, related_name="access_tokens")
    token_hash = models.CharField(max_length=64, unique=True)
    expires_at = models.DateTimeField()
    revoked_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    @staticmethod
    def hash(raw: str) -> str:
        return hashlib.sha256(raw.encode()).hexdigest()

    @classmethod
    def issue(cls, appt) -> str:
        raw = secrets.token_urlsafe(32)
        cls.objects.create(appointment=appt, token_hash=cls.hash(raw), expires_at=appt.ends_at + timedelta(days=1))
        return raw

    @classmethod
    def lookup(cls, raw: str):
        tok = (cls.objects.select_related("appointment", "appointment__business", "appointment__service",
                                          "appointment__staff")
               .filter(token_hash=cls.hash(raw), revoked_at__isnull=True, expires_at__gt=timezone.now()).first())
        return tok.appointment if tok else None

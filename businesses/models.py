"""Businesses, who can log in to them (BusinessMember), who can be booked (Staff),
what they offer (Service), when (Schedule, TimeOff) and their rules (BookingPolicy)."""
import uuid

from django.contrib.postgres.fields import ArrayField
from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.db.models import F, Q
from django.utils import timezone
from django.utils.text import slugify
from phonenumber_field.modelfields import PhoneNumberField


class Category(models.Model):
    name = models.CharField(max_length=80, unique=True)
    slug = models.SlugField(unique=True)
    parent = models.ForeignKey("self", null=True, blank=True, on_delete=models.SET_NULL, related_name="children")
    sort_order = models.PositiveSmallIntegerField(default=0)
    active = models.BooleanField(default=True)

    class Meta:
        ordering = ["sort_order", "name"]
        verbose_name_plural = "categories"

    def __str__(self):
        return self.name


class Town(models.Model):
    name = models.CharField(max_length=80, unique=True)
    slug = models.SlugField(unique=True)
    district = models.CharField(max_length=40)
    active = models.BooleanField(default=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class Business(models.Model):
    class Status(models.TextChoices):
        DRAFT = "draft", "Draft (not visible)"
        LISTED = "listed", "Listed"
        SUSPENDED = "suspended", "Suspended"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=150)
    slug = models.SlugField(unique=True, max_length=160)
    category = models.ForeignKey(Category, on_delete=models.PROTECT, related_name="businesses")
    town = models.ForeignKey(Town, on_delete=models.PROTECT, related_name="businesses")
    tagline = models.CharField(max_length=160, blank=True)
    description = models.TextField(blank=True)
    address = models.CharField(max_length=255, blank=True)
    phone = PhoneNumberField(region="BZ", blank=True)
    email = models.EmailField(blank=True)
    timezone = models.CharField(max_length=64, default="America/Belize")
    latitude = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    longitude = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.DRAFT)
    featured = models.BooleanField(default=False, help_text="Shown at the top of the home page.")
    currency = models.CharField(max_length=3, default="BZD", editable=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name_plural = "businesses"
        indexes = [models.Index(fields=["status", "category", "town"])]

    def __str__(self):
        return self.name

    def save(self, *args, **kwargs):
        if not self.slug:
            base = slugify(self.name)[:150] or "business"
            slug, n = base, 2
            while Business.objects.filter(slug=slug).exclude(pk=self.pk).exists():
                slug, n = f"{base}-{n}", n + 1
            self.slug = slug
        super().save(*args, **kwargs)

    @property
    def is_bookable(self):
        return self.status == self.Status.LISTED

    @property
    def initials(self):
        return "".join(w[0] for w in self.name.split()[:2]).upper()

    @property
    def owner_membership(self):
        return self.members.filter(role=Role.OWNER, status=BusinessMember.Status.ACTIVE).select_related("user").first()


class Role(models.TextChoices):
    OWNER = "owner", "Owner"
    STAFF = "staff", "Staff"


class BusinessMember(models.Model):
    """Who can log in to a business, and as what."""

    class Status(models.TextChoices):
        INVITED = "invited"
        ACTIVE = "active"
        REMOVED = "removed"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    business = models.ForeignKey(Business, on_delete=models.CASCADE, related_name="members")
    user = models.ForeignKey("accounts.User", on_delete=models.PROTECT, related_name="memberships")
    role = models.CharField(max_length=10, choices=Role.choices)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.INVITED)
    invited_by = models.ForeignKey("accounts.User", null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)
    joined_at = models.DateTimeField(null=True, blank=True)
    removed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["business", "user"], name="uniq_member_per_business"),
            models.UniqueConstraint(fields=["business"], condition=Q(role="owner", status="active"),
                                    name="one_active_owner_per_business"),
        ]

    def __str__(self):
        return f"{self.user} @ {self.business} ({self.role})"

    def accept(self):
        self.status, self.joined_at = self.Status.ACTIVE, timezone.now()
        self.save(update_fields=["status", "joined_at"])


class Service(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    business = models.ForeignKey(Business, on_delete=models.CASCADE, related_name="services")
    name = models.CharField(max_length=120)
    description = models.CharField(max_length=300, blank=True)
    duration_min = models.PositiveSmallIntegerField(validators=[MinValueValidator(5), MaxValueValidator(720)])
    price_cents = models.PositiveIntegerField(help_text="BZD cents (BZ$45.00 = 4500)")
    buffer_before_min = models.PositiveSmallIntegerField(default=0, validators=[MaxValueValidator(240)])
    buffer_after_min = models.PositiveSmallIntegerField(default=0, validators=[MaxValueValidator(240)])
    active = models.BooleanField(default=True)
    sort_order = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ["sort_order", "name"]

    def __str__(self):
        return self.name


class Staff(models.Model):
    """Someone who can be booked. Optionally linked to a login (BusinessMember)."""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    business = models.ForeignKey(Business, on_delete=models.CASCADE, related_name="staff")
    member = models.OneToOneField(BusinessMember, null=True, blank=True, on_delete=models.SET_NULL,
                                  related_name="staff_profile")
    display_name = models.CharField(max_length=100)
    title = models.CharField(max_length=80, blank=True)
    active = models.BooleanField(default=True)
    bookable_online = models.BooleanField(default=True)
    services = models.ManyToManyField(Service, through="StaffService", related_name="staff")
    sort_order = models.PositiveSmallIntegerField(default=0)
    feed_token_hash = models.CharField(max_length=64, blank=True, db_index=True, editable=False,
                                       help_text="SHA-256 of the private calendar-feed token.")

    class Meta:
        ordering = ["sort_order", "display_name"]
        verbose_name_plural = "staff"

    def __str__(self):
        return self.display_name

    def clean(self):
        if self.member_id and self.member.business_id != self.business_id:
            raise ValidationError("Staff member must belong to the same business.")

    @property
    def initials(self):
        return "".join(w[0] for w in self.display_name.split()[:2]).upper()

    def rotate_feed_token(self) -> str:
        """New private iCal link; the old one stops working. Only the hash is stored."""
        import hashlib
        import secrets
        raw = secrets.token_urlsafe(32)
        self.feed_token_hash = hashlib.sha256(raw.encode()).hexdigest()
        self.save(update_fields=["feed_token_hash"])
        return raw


class StaffService(models.Model):
    staff = models.ForeignKey(Staff, on_delete=models.CASCADE)
    service = models.ForeignKey(Service, on_delete=models.CASCADE)
    custom_duration_min = models.PositiveSmallIntegerField(null=True, blank=True)
    custom_price_cents = models.PositiveIntegerField(null=True, blank=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["staff", "service"], name="uniq_staff_service")]

    @property
    def duration_min(self):
        return self.custom_duration_min or self.service.duration_min

    @property
    def price_cents(self):
        return self.custom_price_cents if self.custom_price_cents is not None else self.service.price_cents


WEEKDAYS = [(0, "Monday"), (1, "Tuesday"), (2, "Wednesday"), (3, "Thursday"),
            (4, "Friday"), (5, "Saturday"), (6, "Sunday")]


class Schedule(models.Model):
    """Recurring weekly hours. Monday = 0 … Sunday = 6. Several rows per day allowed (breaks)."""
    staff = models.ForeignKey(Staff, on_delete=models.CASCADE, related_name="schedules")
    weekday = models.PositiveSmallIntegerField(choices=WEEKDAYS, validators=[MaxValueValidator(6)])
    start_time = models.TimeField()
    end_time = models.TimeField()

    class Meta:
        ordering = ["weekday", "start_time"]
        constraints = [models.CheckConstraint(condition=Q(start_time__lt=F("end_time")),
                                              name="schedule_start_before_end")]


class TimeOff(models.Model):
    staff = models.ForeignKey(Staff, on_delete=models.CASCADE, related_name="time_off")
    starts_at = models.DateTimeField()
    ends_at = models.DateTimeField()
    reason = models.CharField(max_length=120, blank=True)        # private to the business

    class Meta:
        ordering = ["starts_at"]
        constraints = [models.CheckConstraint(condition=Q(starts_at__lt=F("ends_at")), name="timeoff_start_before_end")]


def default_offsets():
    return [1440, 120]


class BookingPolicy(models.Model):
    business = models.OneToOneField(Business, on_delete=models.CASCADE, related_name="booking_policy")
    slot_interval_min = models.PositiveSmallIntegerField(default=15, validators=[MinValueValidator(5)])
    min_notice_min = models.PositiveIntegerField(default=60)
    max_advance_days = models.PositiveSmallIntegerField(default=60, validators=[MinValueValidator(1),
                                                                                 MaxValueValidator(365)])
    cancel_window_hours = models.PositiveSmallIntegerField(default=24)
    requires_approval = models.BooleanField(default=False)
    approval_timeout_min = models.PositiveSmallIntegerField(default=60)
    # Deposits — stored now, enforced only when PAYMENTS_ENABLED is on.
    deposit_type = models.CharField(max_length=10, choices=[("percent", "Percent"), ("fixed", "Fixed")],
                                    default="percent")
    deposit_value = models.PositiveIntegerField(default=25)
    deposit_window_hours = models.PositiveSmallIntegerField(default=24)
    deposit_cutoff_before_start_min = models.PositiveIntegerField(default=60)
    reminder_offsets_min = ArrayField(models.PositiveIntegerField(), default=default_offsets)
    allow_provider_overlap = models.BooleanField(default=False)
    staff_can_view_all = models.BooleanField(default=False)

    class Meta:
        verbose_name_plural = "booking policies"

    def __str__(self):
        return f"Policy for {self.business}"

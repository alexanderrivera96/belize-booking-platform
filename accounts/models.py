"""Accounts (accounts design §1).

One login for everyone. A User is an identity only — no role column. Business roles come from
BusinessMember rows; platform admins are users with is_staff=True on the separate admin site.
"""
import uuid

from django.contrib.auth.models import AbstractBaseUser, BaseUserManager, PermissionsMixin
from django.db import models
from django.db.models import Q
from django.db.models.functions import Lower
from django.utils import timezone as dj_tz
from phonenumber_field.modelfields import PhoneNumberField


class Channel(models.TextChoices):
    WHATSAPP = "whatsapp", "WhatsApp"
    SMS = "sms", "SMS"


class CodeChannel(models.TextChoices):
    WHATSAPP = "whatsapp", "WhatsApp"
    SMS = "sms", "SMS"
    EMAIL = "email", "Email"


class UserManager(BaseUserManager):
    use_in_migrations = True

    def create_user(self, phone, password=None, **extra):
        if not phone:
            raise ValueError("A phone number is required")
        user = self.model(phone=phone, **extra)
        if password:
            user.set_password(password)
        else:
            user.set_unusable_password()
        user.save(using=self._db)
        return user

    def create_superuser(self, phone, password, **extra):
        extra.update(is_staff=True, is_superuser=True)
        return self.create_user(phone, password, **extra)


class User(AbstractBaseUser, PermissionsMixin):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    phone = PhoneNumberField(region="BZ", unique=True)
    phone_verified_at = models.DateTimeField(null=True, blank=True)

    email = models.EmailField(null=True, blank=True)              # verified address only
    email_verified_at = models.DateTimeField(null=True, blank=True)
    pending_email = models.EmailField(null=True, blank=True)      # typed, not yet verified
    pending_email_set_at = models.DateTimeField(null=True, blank=True)
    email_prompt_count = models.PositiveSmallIntegerField(default=0)

    full_name = models.CharField(max_length=150, blank=True)
    timezone = models.CharField(max_length=64, default="America/Belize")
    preferred_channel = models.CharField(max_length=10, choices=Channel.choices, default=Channel.WHATSAPP)

    is_active = models.BooleanField(default=True)
    is_staff = models.BooleanField(default=False, help_text="Platform admin: may use the admin console.")
    date_joined = models.DateTimeField(default=dj_tz.now)
    deleted_at = models.DateTimeField(null=True, blank=True)
    session_epoch = models.PositiveIntegerField(default=0)

    USERNAME_FIELD = "phone"
    REQUIRED_FIELDS = []
    objects = UserManager()

    class Meta:
        constraints = [
            models.UniqueConstraint(Lower("email"), condition=Q(email__isnull=False), name="uniq_user_email_ci"),
        ]
        indexes = [models.Index(Lower("pending_email"), name="idx_user_pending_email_ci")]

    def __str__(self):
        return self.full_name or str(self.phone)

    @property
    def is_platform_admin(self) -> bool:
        return self.is_active and self.is_staff

    def sign_out_everywhere(self):
        self.session_epoch += 1
        self.save(update_fields=["session_epoch"])


class OtpRequest(models.Model):
    """Log of code requests for rate limiting, abuse detection and cost tracking.
    Codes themselves live in Twilio Verify (or, in development, the console backend's cache)."""

    class Purpose(models.TextChoices):
        LOGIN = "login"
        PHONE_CHANGE = "phone_change"
        EMAIL_VERIFY = "email_verify"
        STEP_UP = "step_up"

    class Status(models.TextChoices):
        PENDING = "pending"
        APPROVED = "approved"
        FAILED = "failed"
        EXPIRED = "expired"

    phone = PhoneNumberField(region="BZ", null=True, blank=True, db_index=True)
    email = models.EmailField(null=True, blank=True, db_index=True)
    channel = models.CharField(max_length=10, choices=CodeChannel.choices)
    purpose = models.CharField(max_length=20, choices=Purpose.choices, default=Purpose.LOGIN)
    user = models.ForeignKey("accounts.User", null=True, blank=True, on_delete=models.SET_NULL)
    ip = models.GenericIPAddressField(null=True, blank=True)
    provider_sid = models.CharField(max_length=64, blank=True)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.PENDING)
    attempts = models.PositiveSmallIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    verified_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=Q(phone__isnull=False, email__isnull=True) | Q(phone__isnull=True, email__isnull=False),
                name="otp_exactly_one_target",
            ),
        ]


class MessagingConsent(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="consents")
    channel = models.CharField(max_length=10, choices=CodeChannel.choices)
    consented_at = models.DateTimeField(default=dj_tz.now)
    revoked_at = models.DateTimeField(null=True, blank=True)
    source = models.CharField(max_length=30, default="checkout")

from django.db import models


class Reminder(models.Model):
    class Kind(models.TextChoices):
        BEFORE_START = "before_start"
        DEPOSIT_DUE = "deposit_due"

    class Status(models.TextChoices):
        SCHEDULED = "scheduled"
        SENDING = "sending"
        SENT = "sent"
        SKIPPED = "skipped"
        CANCELLED = "cancelled"
        FAILED = "failed"

    appointment = models.ForeignKey("booking.Appointment", on_delete=models.CASCADE, related_name="reminders")
    kind = models.CharField(max_length=16, choices=Kind.choices)
    offset_min = models.PositiveIntegerField(null=True, blank=True)
    for_version = models.PositiveIntegerField()
    send_at = models.DateTimeField(db_index=True)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.SCHEDULED)
    claimed_at = models.DateTimeField(null=True, blank=True)
    attempts = models.PositiveSmallIntegerField(default=0)
    channel_used = models.CharField(max_length=10, blank=True)
    provider_message_id = models.CharField(max_length=100, blank=True)
    sent_at = models.DateTimeField(null=True, blank=True)
    last_error = models.TextField(blank=True)

    class Meta:
        ordering = ["send_at"]
        indexes = [models.Index(fields=["status", "send_at"])]


class MessageLog(models.Model):
    class Audience(models.TextChoices):
        CUSTOMER = "customer"
        PROVIDER = "provider"
        INVITE = "invite"

    appointment = models.ForeignKey("booking.Appointment", null=True, blank=True, on_delete=models.SET_NULL,
                                    related_name="messages")
    business = models.ForeignKey("businesses.Business", null=True, blank=True, on_delete=models.SET_NULL,
                                 related_name="messages")
    audience = models.CharField(max_length=10, choices=Audience.choices)
    template = models.CharField(max_length=40)
    channel = models.CharField(max_length=10)
    to = models.CharField(max_length=40)
    body = models.TextField()
    provider_message_id = models.CharField(max_length=100, blank=True)
    status = models.CharField(max_length=20, default="sent")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

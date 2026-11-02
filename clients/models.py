"""Each business's own client list (accounts design §4)."""
import uuid

from django.db import models
from django.db.models import Q
from phonenumber_field.modelfields import PhoneNumberField


class Customer(models.Model):
    class Source(models.TextChoices):
        ONLINE = "online"
        WALK_IN = "walk_in", "Walk-in"
        PHONE = "phone"
        IMPORT = "import"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    business = models.ForeignKey("businesses.Business", on_delete=models.CASCADE, related_name="clients")
    user = models.ForeignKey("accounts.User", null=True, blank=True, on_delete=models.SET_NULL,
                             related_name="client_records")
    name = models.CharField(max_length=150)
    phone = PhoneNumberField(region="BZ", null=True, blank=True)
    email = models.EmailField(null=True, blank=True)
    notes = models.TextField(blank=True, help_text="Private to the business. Do not enter medical information.")
    source = models.CharField(max_length=10, choices=Source.choices)
    linked_at = models.DateTimeField(null=True, blank=True)
    created_by = models.ForeignKey("businesses.BusinessMember", null=True, blank=True, on_delete=models.SET_NULL,
                                   related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["name"]
        constraints = [
            models.UniqueConstraint(fields=["business", "user"], condition=Q(user__isnull=False),
                                    name="uniq_client_user_per_business"),
        ]
        indexes = [models.Index(fields=["business", "phone"])]

    def __str__(self):
        return self.name

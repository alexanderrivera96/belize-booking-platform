from django.db import IntegrityError, transaction
from django.utils import timezone

from .models import Customer


def client_for_online_booking(business, user) -> Customer:
    """Find or create this user's client record at this business (accounts design §4)."""
    with transaction.atomic():
        existing = Customer.objects.select_for_update().filter(business=business, user=user).first()
        if existing:
            return existing
        # A walk-in / phone record with the same (now code-verified) phone can be claimed,
        # but only if exactly one unlinked record matches.
        candidates = list(Customer.objects.select_for_update()
                          .filter(business=business, user__isnull=True, phone=user.phone)[:2])
        if len(candidates) == 1:
            c = candidates[0]
            c.user, c.linked_at = user, timezone.now()
            c.save(update_fields=["user", "linked_at"])
            return c
        try:
            with transaction.atomic():
                return Customer.objects.create(
                    business=business, user=user, name=user.full_name or str(user.phone), phone=user.phone,
                    email=user.email or user.pending_email, source=Customer.Source.ONLINE,
                    linked_at=timezone.now(),
                )
        except IntegrityError:
            return Customer.objects.get(business=business, user=user)


def client_for_provider_booking(business, *, name, phone=None, email=None, member=None, source="walk_in"):
    """Walk-in / phone bookings: reuse an existing client with the same phone, else create one.
    Never attaches a platform account — only the phone's owner can link by verifying it."""
    if phone:
        existing = Customer.objects.filter(business=business, phone=phone).order_by("created_at").first()
        if existing:
            return existing
    return Customer.objects.create(business=business, name=name, phone=phone or None, email=email or None,
                                   source=source, created_by=member)

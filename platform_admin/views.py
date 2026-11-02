from datetime import timedelta

from django.conf import settings
from django.contrib import messages
from django.db import transaction
from django.shortcuts import redirect, render
from django.utils import timezone

from accounts.models import User
from booking.models import ACTIVE, Appointment, S
from businesses.models import (BookingPolicy, Business, BusinessMember, Role, Schedule, Service, Staff,
                               StaffService)
from notifications.models import MessageLog, Reminder

from .forms import DEFAULT_HOURS, OnboardProviderForm
from .models import AdminActionLog


def dashboard_context():
    now = timezone.now()
    week = now - timedelta(days=7)
    return {
        "stats": [
            ("Listed businesses", Business.objects.filter(status="listed").count()),
            ("Draft businesses", Business.objects.filter(status="draft").count()),
            ("Customers", User.objects.filter(is_staff=False, client_records__isnull=False).distinct().count()),
            ("Bookings, last 7 days", Appointment.objects.filter(created_at__gte=week).exclude(status=S.HELD).count()),
            ("Upcoming bookings", Appointment.objects.filter(status__in=ACTIVE, starts_at__gte=now).count()),
            ("Reminders failed, 7 days", Reminder.objects.filter(status="failed", send_at__gte=week).count()),
        ],
        "recent_businesses": Business.objects.select_related("town", "category").order_by("-created_at")[:6],
        "recent_actions": AdminActionLog.objects.select_related("admin")[:8],
        "messages_today": MessageLog.objects.filter(created_at__gte=now - timedelta(days=1)).count(),
    }


def onboard_provider(request):
    """One form that sets up a new service provider: business, owner login, policy, hours, services."""
    from .site import admin_site

    form = OnboardProviderForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        cd = form.cleaned_data
        with transaction.atomic():
            owner, created = User.objects.get_or_create(phone=cd["owner_phone"],
                                                        defaults={"full_name": cd["owner_name"]})
            if not owner.full_name:
                owner.full_name = cd["owner_name"]
            if cd["owner_email"] and not owner.email and not owner.pending_email:
                owner.pending_email = cd["owner_email"].lower()
            owner.save()
            b = Business.objects.create(
                name=cd["business_name"], category=cd["category"], town=cd["town"], address=cd["address"],
                tagline=cd["tagline"], phone=cd["business_phone"] or cd["owner_phone"],
                email=cd["owner_email"] or "",
                status=Business.Status.LISTED if cd["list_now"] else Business.Status.DRAFT)
            BookingPolicy.objects.create(business=b, requires_approval=cd["requires_approval"])
            member = BusinessMember.objects.create(business=b, user=owner, role=Role.OWNER, status="active",
                                                   invited_by=request.user, joined_at=timezone.now())
            services = [Service.objects.create(business=b, name=n, duration_min=m, price_cents=p, sort_order=i)
                        for i, (n, m, p) in enumerate(cd["services"])]
            if cd["owner_is_bookable"]:
                st = Staff.objects.create(business=b, member=member,
                                          display_name=cd["owner_name"].split()[0] if cd["owner_name"] else "Owner")
                for sv in services:
                    StaffService.objects.create(staff=st, service=sv)
                if cd["default_hours"]:
                    Schedule.objects.bulk_create([Schedule(staff=st, weekday=w, start_time=a, end_time=z)
                                                  for w, a, z in DEFAULT_HOURS])
            AdminActionLog.record(request, "onboard_provider", f"{b.name} ({b.id})",
                                  f"owner {owner.phone}; listed={cd['list_now']}")
        if cd["send_welcome"]:
            from notifications.senders import send
            send(str(owner.phone), f"Welcome to {settings.PLATFORM_NAME}! {b.name} is set up. Sign in with this "
                                   f"number to finish your page: {settings.PUBLIC_BASE_URL.rstrip('/')}/provider/",
                 owner.preferred_channel)
        messages.success(request, f"{b.name} is set up. The owner signs in at /provider/ with {owner.phone}.")
        return redirect("admin:businesses_business_change", b.id)
    return render(request, "platform_admin/onboard.html", {
        **admin_site.each_context(request), "form": form, "title": "Add a service provider"})

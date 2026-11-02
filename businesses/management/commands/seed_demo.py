"""Demo data for development and walkthroughs. Never run against production.

Creates three listed businesses with owners, staff, services and weekly hours, plus a platform admin.
Phone numbers are fictional Belize mobile numbers.
"""
from datetime import time

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from accounts.models import User
from businesses.models import (BookingPolicy, Business, BusinessMember, Category, Role, Schedule, Service, Staff,
                               StaffService, Town)

DEMO = [
    {
        "name": "Fade Kings Barbershop", "category": "barbers", "town": "belize-city",
        "tagline": "Sharp fades, hot towel shaves", "address": "12 Albert Street, Belize City",
        "owner": ("+5016001001", "Marlon Young"),
        "staff": [("Marlon", "Master barber", True), ("Jaylen", "Barber", False)],
        "services": [("Classic cut", 30, 2500), ("Skin fade", 45, 3500), ("Beard trim & line-up", 20, 1500),
                     ("Cut + beard", 60, 4500)],
        "hours": {0: [(9, 18)], 1: [(9, 18)], 2: [(9, 18)], 3: [(9, 18)], 4: [(9, 19)], 5: [(8, 17)]},
        "approval": False,
    },
    {
        "name": "Crown & Coils Studio", "category": "hair-braids-locs", "town": "san-ignacio",
        "tagline": "Braids, locs and natural hair care", "address": "Burns Avenue, San Ignacio",
        "owner": ("+5016002002", "Keisha Flowers"),
        "staff": [("Keisha", "Loctician", True)],
        "services": [("Knotless braids (medium)", 240, 18000), ("Loc retwist", 90, 6000),
                     ("Wash & style", 60, 4000)],
        "hours": {1: [(9, 13), (14, 18)], 2: [(9, 13), (14, 18)], 3: [(9, 13), (14, 18)], 4: [(9, 18)],
                  5: [(9, 15)]},
        "approval": True,
    },
    {
        "name": "Island Nails & Lashes", "category": "nail-techs", "town": "belmopan",
        "tagline": "Gel, acrylic and lash extensions", "address": "Market Square, Belmopan",
        "owner": ("+5016003003", "Ana Castillo"),
        "staff": [("Ana", "Nail tech", True), ("Sofia", "Lash tech", False)],
        "services": [("Gel manicure", 60, 4500), ("Acrylic full set", 90, 7000), ("Classic lash set", 120, 9000)],
        "hours": {0: [(10, 18)], 1: [(10, 18)], 2: [(10, 18)], 3: [(10, 18)], 4: [(10, 18)], 5: [(9, 16)]},
        "approval": False,
    },
]


class Command(BaseCommand):
    help = "Create demo businesses, staff, services and an admin login (development only)."

    def handle(self, *args, **opts):
        if not settings.DEBUG:
            raise CommandError("seed_demo only runs with DJANGO_DEBUG=1")
        from django.core.management import call_command
        call_command("seed_reference_data")
        with transaction.atomic():
            admin, created = User.objects.get_or_create(phone="+5016009999", defaults={
                "full_name": "Platform Admin", "is_staff": True, "is_superuser": True,
                "phone_verified_at": timezone.now()})
            if created:
                admin.set_password("admin-demo-password-change-me")
                admin.save()
            for d in DEMO:
                self._business(d)
        self.stdout.write(self.style.SUCCESS("Demo data ready."))
        self.stdout.write("Owner logins (phone code): " + ", ".join(d["owner"][0] for d in DEMO))
        self.stdout.write("Admin console: phone +5016009999 / password admin-demo-password-change-me (then set up MFA)")

    def _business(self, d):
        if Business.objects.filter(name=d["name"]).exists():
            return
        owner, _ = User.objects.get_or_create(phone=d["owner"][0], defaults={
            "full_name": d["owner"][1], "phone_verified_at": timezone.now()})
        b = Business.objects.create(name=d["name"], category=Category.objects.get(slug=d["category"]),
                                    town=Town.objects.get(slug=d["town"]), tagline=d["tagline"],
                                    address=d["address"], phone=d["owner"][0], status=Business.Status.LISTED,
                                    description=f"{d['tagline']}. Book online in under a minute.")
        BookingPolicy.objects.create(business=b, requires_approval=d["approval"])
        owner_member = BusinessMember.objects.create(business=b, user=owner, role=Role.OWNER,
                                                     status=BusinessMember.Status.ACTIVE, joined_at=timezone.now())
        services = [Service.objects.create(business=b, name=n, duration_min=m, price_cents=p, sort_order=i)
                    for i, (n, m, p) in enumerate(d["services"])]
        for i, (name, title, is_owner) in enumerate(d["staff"]):
            st = Staff.objects.create(business=b, display_name=name, title=title, sort_order=i,
                                      member=owner_member if is_owner else None)
            for sv in services:
                StaffService.objects.create(staff=st, service=sv)
            for wd, ranges in d["hours"].items():
                for a, z in ranges:
                    Schedule.objects.create(staff=st, weekday=wd, start_time=time(a), end_time=time(z))

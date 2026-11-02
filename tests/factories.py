"""Small builders for tests."""
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from django.utils import timezone

from accounts.models import User
from businesses.models import (BookingPolicy, Business, BusinessMember, Category, Role, Schedule, Service, Staff,
                               StaffService, Town)

BZ = ZoneInfo("America/Belize")
_n = [0]


def uniq():
    _n[0] += 1
    return _n[0]


def phone():
    return f"+5016{uniq():06d}"


def make_business(name=None, *, approval=False, staff_names=("Ana",), listed=True, hours=(9, 17)):
    i = uniq()
    cat, _ = Category.objects.get_or_create(slug="barbers", defaults={"name": "Barbers"})
    town, _ = Town.objects.get_or_create(slug="belize-city", defaults={"name": "Belize City", "district": "Belize"})
    b = Business.objects.create(name=name or f"Shop {i}", category=cat, town=town,
                                status="listed" if listed else "draft")
    BookingPolicy.objects.create(business=b, requires_approval=approval, min_notice_min=0)
    owner = User.objects.create(phone=phone(), full_name=f"Owner {i}")
    m = BusinessMember.objects.create(business=b, user=owner, role=Role.OWNER, status="active",
                                      joined_at=timezone.now())
    svc = Service.objects.create(business=b, name="Cut", duration_min=30, price_cents=2500, buffer_after_min=0)
    staff = []
    for j, n in enumerate(staff_names):
        st = Staff.objects.create(business=b, display_name=n, member=m if j == 0 else None)
        StaffService.objects.create(staff=st, service=svc)
        for wd in range(7):
            Schedule.objects.create(staff=st, weekday=wd, start_time=time(hours[0]), end_time=time(hours[1]))
        staff.append(st)
    return b, svc, staff, owner


def future_day(days=3):
    return timezone.now().astimezone(BZ).date() + timedelta(days=days)


def at(day: date, hh, mm=0):
    return datetime.combine(day, time(hh, mm), tzinfo=BZ)


def customer(name="Client"):
    return User.objects.create(phone=phone(), full_name=name, phone_verified_at=timezone.now())

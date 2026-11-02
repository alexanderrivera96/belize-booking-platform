"""Tenant isolation layer 4: automated cross-tenant tests (accounts design §2).

Walks every provider-dashboard and API route under a business id and calls it as the owner of a
*different* business. Every one must answer 404 and change nothing. New routes are covered
automatically because the list comes from the URL resolver.
"""
import re
import time
import uuid

from django.test import Client, TestCase, override_settings
from django.urls import get_resolver

from booking import lifecycle
from booking.models import Appointment
from clients.models import Customer

from .factories import at, future_day, make_business


def login(client, user):
    """Log in the way the app does (session policy fields included)."""
    from django.contrib.auth import SESSION_KEY, BACKEND_SESSION_KEY, HASH_SESSION_KEY
    client.force_login(user)
    s = client.session
    now = time.time()
    s.update({"login_at": now, "verified_at": now, "login_method": "phone", "epoch": user.session_epoch,
              "remember": True, "renewed_at": now})
    s.save()


def business_routes():
    out = []

    def walk(patterns, prefix=""):
        for p in patterns:
            pat = prefix + str(p.pattern)
            if hasattr(p, "url_patterns"):
                walk(p.url_patterns, pat)
            elif "business_id" in pat:
                out.append(pat)
    walk(get_resolver().url_patterns)
    return out


def fill(pattern, ids):
    path = pattern.lstrip("^").rstrip("$").replace("\\Z", "")
    path = re.sub(r"\(\?P<(\w+)>[^)]*\)", r"<\1>", path)

    def sub(m):
        name = m.group(1).split(":")[-1]
        return {"action": "approve", "format": "json"}.get(name, str(ids.get(name, uuid.uuid4())))
    path = re.sub(r"<([\w:]+)>", sub, path)
    path = path.replace("\\.", ".").replace("/?", "/")
    return "/" + path


@override_settings(CELERY_TASK_ALWAYS_EAGER=True)
class CrossTenantTests(TestCase):
    def setUp(self):
        self.a, self.svc_a, (self.st_a,), self.owner_a = make_business("A")
        self.b, self.svc_b, (self.st_b,), self.owner_b = make_business("B")
        self.cust_b = Customer.objects.create(business=self.b, name="Bob", source="walk_in")
        self.appt_b = lifecycle.create_provider_booking(business=self.b, staff=self.st_b, service=self.svc_b,
                                                        customer=self.cust_b, starts_at=at(future_day(), 10),
                                                        member=None)
        self.ids = {"business_id": self.b.id, "appt_id": self.appt_b.id, "client_id": self.cust_b.id,
                    "service_id": self.svc_b.id, "staff_id": self.st_b.id, "pk": self.appt_b.id,
                    "member_id": self.b.owner_membership.id, "item_id": 1}

    def test_every_business_route_is_404_for_other_tenants(self):
        c = Client()
        login(c, self.owner_a)
        routes = business_routes()
        self.assertGreater(len(routes), 25)
        before = Appointment.objects.get(id=self.appt_b.id).status
        for r in routes:
            url = fill(r, self.ids)
            for method in ("get", "post"):
                resp = getattr(c, method)(url)
                self.assertIn(resp.status_code, (404, 405), f"{method.upper()} {url} -> {resp.status_code}")
        self.assertEqual(Appointment.objects.get(id=self.appt_b.id).status, before)

    def test_owner_sees_own_business(self):
        c = Client()
        login(c, self.owner_b)
        self.assertEqual(c.get(f"/provider/{self.b.id}/").status_code, 200)
        self.assertEqual(c.get(f"/api/v1/businesses/{self.b.id}/appointments/").status_code, 200)

    def test_staff_cannot_reach_owner_pages(self):
        from accounts.models import User
        from businesses.models import BusinessMember
        u = User.objects.create(phone="+5016990001")
        m = BusinessMember.objects.create(business=self.b, user=u, role="staff", status="active")
        c = Client()
        login(c, u)
        for p in ["services/", "team/", "hours/", "settings/"]:
            self.assertEqual(c.get(f"/provider/{self.b.id}/{p}").status_code, 404, p)
        self.assertEqual(c.get(f"/provider/{self.b.id}/").status_code, 200)
        # staff without a calendar of their own see no bookings
        r = c.get(f"/api/v1/businesses/{self.b.id}/appointments/")
        self.assertEqual(r.json()["appointments"], [])

    def test_customers_cannot_see_each_others_bookings(self):
        from .factories import customer
        u1, u2 = customer("One"), customer("Two")
        h = lifecycle.create_hold(business=self.a, service=self.svc_a, staff_candidates=[self.st_a],
                                  starts_at=at(future_day(), 11), checkout_token="t")
        appt, _ = lifecycle.confirm_hold(hold_id=h.id, checkout_token="t", user=u1)
        c = Client()
        login(c, u2)
        self.assertEqual(c.get(f"/me/a/{appt.id}/").status_code, 404)
        self.assertEqual(c.post(f"/me/a/{appt.id}/cancel/").status_code, 404)
        self.assertEqual(c.post(f"/api/v1/me/appointments/{appt.id}/cancel/").status_code, 404)

    def test_stale_verification_requires_reverify(self):
        c = Client()
        login(c, self.owner_b)
        s = c.session
        s["verified_at"] = time.time() - 31 * 86400
        s.save()
        r = c.get(f"/provider/{self.b.id}/")
        self.assertEqual(r.status_code, 302)
        self.assertIn("/reverify/", r["Location"])
        s["verified_at"], s["login_method"] = time.time(), "email"
        s.save()
        self.assertIn("/reverify/", c.get(f"/provider/{self.b.id}/")["Location"])


class RouteRulesTests(TestCase):
    def test_api_is_deny_by_default(self):
        """Only the approved list of API views may allow anonymous access."""
        from rest_framework.permissions import AllowAny
        approved = {"categories", "search", "business_detail", "availability", "HoldView", "release_hold",
                    "OtpStart", "OtpVerify"}
        found = set()

        def walk(patterns):
            for p in patterns:
                if hasattr(p, "url_patterns"):
                    walk(p.url_patterns)
                    continue
                cb = p.callback
                cls = getattr(cb, "cls", None) or getattr(cb, "view_class", None)
                if cls is None or not cls.__module__.startswith("api"):
                    continue
                perms = getattr(cls, "permission_classes", [])
                if AllowAny in perms:
                    found.add(getattr(cb, "__name__", cls.__name__) if cls.__name__ == "WrappedAPIView"
                              else cls.__name__)
        walk(get_resolver().url_patterns)
        self.assertTrue(found <= approved, found - approved)

"""Session policy (accounts design §1.6).

- Customers who tick "keep me signed in": 180 days sliding, 365 days absolute.
- Unticked: ends when the browser closes, 12 hours absolute.
- Business area: last code verification within 30 days.
- Sensitive actions: verification within 15 minutes (step-up).
- Admin host: 30 minutes idle, 8 hours absolute.
- Bumping User.session_epoch signs out every device.
"""
import time

from django.conf import settings
from django.contrib.auth import login, logout

DAY = 86400


def start_session(request, user, *, remember: bool, method: str):
    login(request, user, backend="django.contrib.auth.backends.ModelBackend")  # rotates the session key
    s = request.session
    now = time.time()
    s["login_at"] = now
    s["verified_at"] = now
    s["login_method"] = method          # "phone" | "email" | "password"
    s["epoch"] = user.session_epoch
    s["remember"] = remember
    s["renewed_at"] = now
    s.set_expiry(settings.CUSTOMER_SESSION_DAYS * DAY if remember else 0)


def mark_verified(request, method="phone"):
    request.session["verified_at"] = time.time()
    request.session["login_method"] = method


def verified_within(request, seconds: int) -> bool:
    return time.time() - request.session.get("verified_at", 0) <= seconds


def strong_verification_within(request, seconds: int) -> bool:
    """Business area and step-up never accept an email-code verification (security plan)."""
    return verified_within(request, seconds) and request.session.get("login_method") != "email"


class SessionPolicyMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        user = getattr(request, "user", None)
        if user is not None and user.is_authenticated:
            s = request.session
            now = time.time()
            if settings.SITE_ROLE == "admin":
                idle = now - s.get("last_seen", now)
                if idle > settings.ADMIN_IDLE_MINUTES * 60 or \
                        now - s.get("admin_login_at", now) > settings.ADMIN_ABSOLUTE_HOURS * 3600:
                    logout(request)
                else:
                    s.setdefault("admin_login_at", now)
                    s["last_seen"] = now
            else:
                absolute = settings.CUSTOMER_SESSION_ABSOLUTE_DAYS * DAY if s.get("remember") else 12 * 3600
                if s.get("epoch") != user.session_epoch or now - s.get("login_at", 0) > absolute:
                    logout(request)
                elif s.get("remember") and now - s.get("renewed_at", 0) > DAY:
                    s.set_expiry(settings.CUSTOMER_SESSION_DAYS * DAY)
                    s["renewed_at"] = now
        return self.get_response(request)

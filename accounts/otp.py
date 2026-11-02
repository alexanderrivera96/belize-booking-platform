"""One-time code delivery and checking.

Backends:
- "console": development only. Generates a 6-digit code, keeps a hash in the cache for
  10 minutes and logs the code. Never enable in production.
- "twilio": Twilio Verify (WhatsApp, SMS, email via SendGrid). Codes never touch our database.

Rate limits (security plan, API abuse): per target 3 per 10 minutes and 10 per day,
per IP 10 per hour, and a global daily cap; 5 attempts per code.
"""
import base64
import hashlib
import hmac
import json
import logging
import secrets
import urllib.error
import urllib.parse
import urllib.request
from datetime import timedelta

import phonenumbers
from django.conf import settings
from django.core.cache import cache
from django.utils import timezone

from .models import OtpRequest

log = logging.getLogger("accounts.otp")

CODE_TTL = 600
MAX_ATTEMPTS = 5
GLOBAL_DAILY_CAP = 2000


class OtpError(Exception):
    """Shown to the user; message must not reveal whether an account exists."""


def normalise_phone(raw: str) -> str:
    """Return E.164 for a Belize mobile number, or raise OtpError."""
    try:
        num = phonenumbers.parse(raw, "BZ")
    except phonenumbers.NumberParseException:
        raise OtpError("Enter a valid mobile number.")
    if not phonenumbers.is_valid_number(num):
        raise OtpError("Enter a valid mobile number.")
    if num.country_code not in settings.OTP_ALLOWED_COUNTRY_CODES:
        raise OtpError("Codes can only be sent to Belize (+501) mobile numbers for now.")
    if phonenumbers.number_type(num) not in (phonenumbers.PhoneNumberType.MOBILE,
                                             phonenumbers.PhoneNumberType.FIXED_LINE_OR_MOBILE):
        raise OtpError("Please use a mobile number — codes can't be sent to landlines.")
    return phonenumbers.format_number(num, phonenumbers.PhoneNumberFormat.E164)


def _check_rate_limits(*, phone=None, email=None, ip=None):
    now = timezone.now()
    q = OtpRequest.objects.filter(phone=phone) if phone else OtpRequest.objects.filter(email__iexact=email)
    if q.filter(created_at__gte=now - timedelta(minutes=10)).count() >= 3:
        raise OtpError("Too many codes requested. Please wait a few minutes and try again.")
    if q.filter(created_at__gte=now - timedelta(days=1)).count() >= 10:
        raise OtpError("Too many codes requested today. Please try again tomorrow.")
    if ip and OtpRequest.objects.filter(ip=ip, created_at__gte=now - timedelta(hours=1)).count() >= 10:
        raise OtpError("Too many codes requested. Please try again later.")
    if OtpRequest.objects.filter(created_at__gte=now - timedelta(days=1)).count() >= GLOBAL_DAILY_CAP:
        log.error("Global daily OTP cap reached — possible SMS pumping")
        raise OtpError("We can't send codes right now. Please try again later.")


# ---------------------------------------------------------------- backends

class ConsoleBackend:
    """Development backend. Code is logged and, when DEBUG is on, shown on screen."""

    def _key(self, target, purpose):
        return f"otp:{purpose}:{target.lower()}"

    def start(self, target, channel, purpose):
        code = f"{secrets.randbelow(1_000_000):06d}"
        digest = hashlib.sha256(code.encode()).hexdigest()
        cache.set(self._key(target, purpose), {"h": digest, "n": 0}, CODE_TTL)
        cache.set(f"otp:last:{target.lower()}", code, CODE_TTL)  # for the DEBUG banner only
        log.warning("DEV CODE for %s via %s (%s): %s", target, channel, purpose, code)
        return "console"

    def check(self, target, code, purpose):
        key = self._key(target, purpose)
        entry = cache.get(key)
        if not entry:
            return False
        if entry["n"] >= MAX_ATTEMPTS:
            cache.delete(key)
            return False
        ok = hmac.compare_digest(entry["h"], hashlib.sha256(code.strip().encode()).hexdigest())
        if ok:
            cache.delete(key)
        else:
            entry["n"] += 1
            cache.set(key, entry, CODE_TTL)
        return ok


class TwilioVerifyBackend:
    base = "https://verify.twilio.com/v2/Services/{sid}/{path}"

    def _post(self, path, data):
        url = self.base.format(sid=settings.TWILIO_VERIFY_SERVICE_SID, path=path)
        req = urllib.request.Request(url, data=urllib.parse.urlencode(data).encode(), method="POST")
        token = base64.b64encode(f"{settings.TWILIO_ACCOUNT_SID}:{settings.TWILIO_AUTH_TOKEN}".encode()).decode()
        req.add_header("Authorization", f"Basic {token}")
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                return json.loads(resp.read())
        except urllib.error.HTTPError as exc:
            if path == "VerificationCheck" and exc.code == 404:
                return {"status": "expired"}
            log.error("Twilio Verify error %s on %s", exc.code, path)
            raise OtpError("We couldn't send or check the code. Please try again.")
        except urllib.error.URLError:
            raise OtpError("We couldn't reach the code service. Please try again.")

    def start(self, target, channel, purpose):
        return self._post("Verifications", {"To": target, "Channel": channel}).get("sid", "")

    def check(self, target, code, purpose):
        return self._post("VerificationCheck", {"To": target, "Code": code.strip()}).get("status") == "approved"


def backend():
    return TwilioVerifyBackend() if settings.OTP_BACKEND == "twilio" else ConsoleBackend()


# ---------------------------------------------------------------- public API

def start_phone_code(phone_raw, *, channel="whatsapp", purpose=OtpRequest.Purpose.LOGIN, ip=None, user=None):
    phone = normalise_phone(phone_raw)
    _check_rate_limits(phone=phone, ip=ip)
    sid = backend().start(phone, channel if channel in ("whatsapp", "sms") else "sms", purpose)
    OtpRequest.objects.create(phone=phone, channel=channel, purpose=purpose, ip=ip, user=user, provider_sid=sid)
    return phone


def check_phone_code(phone, code, *, purpose=OtpRequest.Purpose.LOGIN) -> bool:
    req = OtpRequest.objects.filter(phone=phone, purpose=purpose, status="pending").order_by("-created_at").first()
    if req is None:
        return False
    ok = backend().check(phone, code, purpose)
    req.attempts += 1
    if ok:
        req.status, req.verified_at = "approved", timezone.now()
    elif req.attempts >= MAX_ATTEMPTS:
        req.status = "failed"
    req.save(update_fields=["attempts", "status", "verified_at"])
    return ok


def start_email_code(email, *, purpose=OtpRequest.Purpose.LOGIN, ip=None, user=None):
    email = email.strip().lower()
    _check_rate_limits(email=email, ip=ip)
    sid = backend().start(email, "email", purpose)
    OtpRequest.objects.create(email=email, channel="email", purpose=purpose, ip=ip, user=user, provider_sid=sid)


def check_email_code(email, code, *, purpose=OtpRequest.Purpose.LOGIN) -> bool:
    email = email.strip().lower()
    req = OtpRequest.objects.filter(email__iexact=email, purpose=purpose, status="pending").order_by("-created_at").first()
    if req is None:
        return False
    ok = backend().check(email, code, purpose)
    req.attempts += 1
    if ok:
        req.status, req.verified_at = "approved", timezone.now()
    req.save(update_fields=["attempts", "status", "verified_at"])
    return ok


def dev_code_for(target: str):
    """Only used by templates when DEBUG and the console backend are both on."""
    if settings.DEBUG and settings.OTP_BACKEND == "console":
        return cache.get(f"otp:last:{target.lower()}")
    return None


def client_ip(request):
    return request.META.get("REMOTE_ADDR")

"""Read-only iCal feed per staff member (architecture plan: calendar sync, step 1).
Token in the path, stored hashed, rotatable. Events show the service only — no client names or
phone numbers, because calendar apps and shared calendars spread the feed."""
import hashlib
from datetime import timedelta

from django.conf import settings
from django.http import Http404, HttpResponse
from django.utils import timezone

from businesses.models import Staff

from .models import BLOCKING, Appointment, S


def _esc(v: str) -> str:
    return v.replace("\\", "\\\\").replace(";", "\;").replace(",", "\\,").replace("\n", "\\n")


def staff_feed(request, token):
    h = hashlib.sha256(token.encode()).hexdigest()
    st = Staff.objects.select_related("business").filter(feed_token_hash=h, active=True).first()
    if st is None:
        raise Http404()
    now = timezone.now()
    appts = (Appointment.objects.filter(staff=st, status__in=BLOCKING, starts_at__gte=now - timedelta(days=30),
                                        starts_at__lte=now + timedelta(days=120))
             .exclude(status=S.HELD).select_related("service"))
    from datetime import timezone as dt_tz
    utc = lambda d: d.astimezone(dt_tz.utc).strftime("%Y%m%dT%H%M%SZ")  # noqa: E731
    lines = ["BEGIN:VCALENDAR", "VERSION:2.0", f"PRODID:-//{settings.PLATFORM_NAME}//Bookings//EN",
             f"X-WR-CALNAME:{_esc(st.business.name)} — {_esc(st.display_name)}", "CALSCALE:GREGORIAN"]
    for a in appts:
        status = "TENTATIVE" if a.status == S.PENDING_APPROVAL else "CONFIRMED"
        lines += ["BEGIN:VEVENT", f"UID:{a.id}@{settings.PLATFORM_NAME.lower()}", f"DTSTAMP:{utc(a.updated_at)}",
                  f"DTSTART:{utc(a.starts_at)}", f"DTEND:{utc(a.ends_at)}",
                  f"SUMMARY:{_esc(a.service.name)} (booked)", f"STATUS:{status}", f"SEQUENCE:{a.version}",
                  "END:VEVENT"]
    lines.append("END:VCALENDAR")
    resp = HttpResponse("\r\n".join(lines) + "\r\n", content_type="text/calendar; charset=utf-8")
    resp["Cache-Control"] = "no-store"
    resp["Referrer-Policy"] = "no-referrer"
    return resp

"""Customer interface: marketplace, business pages, booking flow, my bookings, manage-by-link."""
import secrets
import uuid
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.db.models import Count, F, Min, Q
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from accounts import otp
from accounts.models import User
from booking.availability import available_slots, bookable_staff, staff_slots
from booking.lifecycle import (BookingError, SlotTaken, cancel_by_client, confirm_hold, create_hold, release_hold,
                               reschedule)
from booking.models import ACTIVE, Appointment, AppointmentAccessToken, S
from businesses.models import WEEKDAYS, Business, Category, Schedule, Service, Staff, Town
from core.sessions import start_session

from .forms import CheckoutForm

DAYS_SHOWN = 14


def listed():
    return (Business.objects.filter(status=Business.Status.LISTED)
            .select_related("category", "town")
            .annotate(min_price=Min("services__price_cents", filter=Q(services__active=True)),
                      service_count=Count("services", filter=Q(services__active=True), distinct=True)))


# ---------------------------------------------------------------- marketplace

def home(request):
    return render(request, "customer/home.html", {
        "categories": Category.objects.filter(active=True),
        "towns": Town.objects.filter(active=True),
        "featured": listed().filter(featured=True)[:6] or listed().order_by("-created_at")[:6],
    })


def search(request):
    qs = listed()
    cat = request.GET.get("category") or ""
    town = request.GET.get("town") or ""
    q = (request.GET.get("q") or "").strip()[:80]
    if cat:
        qs = qs.filter(category__slug=cat)
    if town:
        qs = qs.filter(town__slug=town)
    if q:
        qs = qs.filter(Q(name__icontains=q) | Q(tagline__icontains=q) | Q(services__name__icontains=q)).distinct()
    return render(request, "customer/search.html", {
        "results": qs.order_by("-featured", "name")[:50],
        "categories": Category.objects.filter(active=True), "towns": Town.objects.filter(active=True),
        "cat": cat, "town": town, "q": q,
    })


def business_detail(request, slug):
    b = get_object_or_404(listed(), slug=slug)
    services = b.services.filter(active=True)
    staff = Staff.objects.filter(business=b, active=True, bookable_online=True)
    hours = {}
    for sch in Schedule.objects.filter(staff__in=staff).order_by("weekday", "start_time"):
        hours.setdefault(sch.weekday, []).append((sch.start_time, sch.end_time))
    hours_rows = [(name, _merge(hours.get(i, []))) for i, name in WEEKDAYS]
    return render(request, "customer/business.html", {"b": b, "services": services, "staff": staff,
                                                      "hours": hours_rows})


def _merge(ranges):
    out = []
    for s, e in sorted(ranges):
        if out and s <= out[-1][1]:
            out[-1] = (out[-1][0], max(out[-1][1], e))
        else:
            out.append((s, e))
    return out


# ---------------------------------------------------------------- booking: pick a time

def _parse_day(raw, tz):
    today = timezone.now().astimezone(tz).date()
    try:
        d = date.fromisoformat(raw) if raw else today
    except ValueError:
        d = today
    return max(d, today), today


def _day_strip(staff_list, today, selected, max_days):
    open_days = set(Schedule.objects.filter(staff__in=staff_list).values_list("weekday", flat=True))
    return [{"date": today + timedelta(days=i), "open": (today + timedelta(days=i)).weekday() in open_days,
             "on": today + timedelta(days=i) == selected} for i in range(min(DAYS_SHOWN, max_days + 1))]


def book(request, slug):
    b = get_object_or_404(listed(), slug=slug)
    tz = ZoneInfo(b.timezone)
    service = get_object_or_404(Service, business=b, active=True, id=_uuid(request.GET.get("service")))
    candidates = bookable_staff(b, service)
    if not candidates:
        messages.error(request, "Nobody is taking online bookings for that service right now.")
        return redirect("customer:business", slug=slug)
    staff_id = request.GET.get("staff") or "any"
    staff = next((s for s in candidates if str(s.id) == staff_id), None)
    day, today = _parse_day(request.GET.get("date"), tz)
    raw = available_slots(b, service, staff, day)
    if not request.GET.get("date"):          # first visit: jump to the first day with free times
        for i in range(1, DAYS_SHOWN):
            if raw:
                break
            day = today + timedelta(days=i)
            raw = available_slots(b, service, staff, day)
    slots = [(st.astimezone(tz), ids) for st, ids in raw]
    ctx = {
        "b": b, "service": service, "candidates": candidates, "staff": staff, "staff_id": staff_id,
        "day": day, "days": _day_strip(candidates if not staff else [staff], today, day,
                                       b.booking_policy.max_advance_days),
        "slots": slots, "tzname": "Belize time" if b.timezone == "America/Belize" else b.timezone,
    }
    template = "customer/_slots.html" if request.headers.get("HX-Request") else "customer/book.html"
    return render(request, template, ctx)


def _uuid(v):
    try:
        return uuid.UUID(str(v))
    except (ValueError, TypeError):
        raise Http404()


def _checkout_token(request):
    tok = request.session.get("checkout_token")
    if not tok:
        tok = request.session["checkout_token"] = secrets.token_urlsafe(24)
    return tok


@require_POST
def hold(request, slug):
    b = get_object_or_404(listed(), slug=slug)
    service = get_object_or_404(Service, business=b, active=True, id=_uuid(request.POST.get("service")))
    try:
        starts_at = datetime.fromisoformat(request.POST.get("starts_at", ""))
        if starts_at.tzinfo is None:
            raise ValueError
    except ValueError:
        raise Http404()
    candidates = bookable_staff(b, service)
    sid = request.POST.get("staff") or "any"
    if sid != "any":
        candidates = [s for s in candidates if str(s.id) == sid]
    try:
        appt = create_hold(business=b, service=service, staff_candidates=candidates, starts_at=starts_at,
                           checkout_token=_checkout_token(request))
    except BookingError as e:
        messages.error(request, str(e))
        tz = ZoneInfo(b.timezone)
        return redirect(f"/b/{slug}/book/?service={service.id}&staff={sid}&date={starts_at.astimezone(tz).date()}")
    return redirect("customer:checkout", hold_id=appt.id)


def checkout(request, hold_id):
    tok = request.session.get("checkout_token", "")
    appt = (Appointment.objects.select_related("business", "service", "staff", "business__booking_policy")
            .filter(id=hold_id, status=S.HELD, checkout_token=tok).first())
    if appt is None:
        messages.error(request, "That time is no longer held for you. Please pick a time again.")
        return redirect("/")
    tz = ZoneInfo(appt.business.timezone)
    user = request.user if request.user.is_authenticated else None
    pending = request.session.get("checkout_pending")
    stage = "code" if pending and pending.get("hold") == str(appt.id) and not user else "details"
    initial = {"preferred_channel": "whatsapp", "remember": True}
    if user:
        initial.update(full_name=user.full_name, phone=str(user.phone), email=user.email or user.pending_email or "",
                       preferred_channel=user.preferred_channel)
    elif pending:
        initial.update({k: pending.get(k) for k in ("full_name", "phone", "email", "preferred_channel", "notes")})
    form = CheckoutForm(request.POST or None, initial=initial, signed_in=bool(user))
    error = None

    if request.method == "POST" and request.POST.get("action") == "back":
        request.session.pop("checkout_pending", None)
        return redirect("customer:checkout", hold_id=appt.id)

    if request.method == "POST" and request.POST.get("action") == "verify" and pending:
        code = (request.POST.get("code") or "").strip()
        if otp.check_phone_code(pending["phone"], code):
            with transaction.atomic():
                u, _ = User.objects.get_or_create(phone=pending["phone"])
                u.phone_verified_at = u.phone_verified_at or timezone.now()
                if not u.full_name:
                    u.full_name = pending["full_name"]
                u.preferred_channel = pending["preferred_channel"]
                if pending.get("email") and (pending["email"] != (u.email or "")):
                    u.pending_email, u.pending_email_set_at = pending["email"], timezone.now()
                u.save()
            start_session(request, u, remember=pending.get("remember", True), method="phone")
            request.session["checkout_token"] = tok  # login rotates the key but keeps data; be explicit
            request.session.pop("checkout_pending", None)
            if pending.get("email") and u.pending_email:
                try:
                    otp.start_email_code(u.pending_email, purpose="email_verify", user=u)
                except otp.OtpError:
                    pass
            return _finish(request, appt, u, pending.get("notes", ""), request.POST.get("idem"))
        error = "That code didn't work. Check it and try again."
        stage = "code"

    elif request.method == "POST" and form.is_valid():
        notes = form.cleaned_data.get("notes", "")
        if user:
            if form.cleaned_data.get("full_name") and not user.full_name:
                user.full_name = form.cleaned_data["full_name"]
                user.save(update_fields=["full_name"])
            return _finish(request, appt, user, notes, request.POST.get("idem"))
        try:
            phone = otp.start_phone_code(form.cleaned_data["phone"], channel=form.cleaned_data["preferred_channel"],
                                         ip=otp.client_ip(request))
        except otp.OtpError as e:
            form.add_error("phone", str(e))
        else:
            request.session["checkout_pending"] = {
                "hold": str(appt.id), "phone": phone, "full_name": form.cleaned_data["full_name"],
                "email": (form.cleaned_data.get("email") or "").lower(), "notes": notes,
                "preferred_channel": otp.effective_channel(form.cleaned_data["preferred_channel"]),
                "remember": form.cleaned_data.get("remember", True),
            }
            return redirect("customer:checkout", hold_id=appt.id)

    return render(request, "customer/checkout.html", {
        "appt": appt, "b": appt.business, "start_local": appt.starts_at.astimezone(tz), "form": form,
        "stage": stage, "pending": pending, "error": error, "idem": secrets.token_urlsafe(16),
        "dev_code": otp.dev_code_for(pending["phone"]) if stage == "code" and pending else None,
        "email_required": settings.CHECKOUT_EMAIL_REQUIRED,
    })


def _finish(request, appt, user, notes, idem):
    try:
        booked, _ = confirm_hold(hold_id=appt.id, checkout_token=request.session.get("checkout_token", ""),
                                 user=user, customer_notes=notes, idempotency_key=(idem or None))
    except BookingError as e:
        messages.error(request, str(e))
        return redirect(f"/b/{appt.business.slug}/book/?service={appt.service_id}")
    return redirect("customer:booked", appt_id=booked.id)


@require_POST
def cancel_hold(request, hold_id):
    appt = Appointment.objects.filter(id=hold_id).select_related("business").first()
    release_hold(hold_id=hold_id, checkout_token=request.session.get("checkout_token", ""))
    request.session.pop("checkout_pending", None)
    return redirect(f"/b/{appt.business.slug}/" if appt else "/")


# ---------------------------------------------------------------- my bookings

def visible_to(user):
    """accounts design §4 rule 4: only bookings the user made, or created after they linked."""
    return (Appointment.objects.filter(customer__user=user)
            .filter(Q(booked_by_user=user) | Q(created_at__gte=F("customer__linked_at")))
            .exclude(status=S.HELD)
            .select_related("business", "service", "staff"))


@login_required
def booked(request, appt_id):
    appt = get_object_or_404(visible_to(request.user), id=appt_id)
    return render(request, "customer/booked.html", {"appt": appt, "b": appt.business,
                                                    "start_local": appt.starts_at.astimezone(ZoneInfo(appt.business.timezone))})


@login_required
def my_bookings(request):
    now = timezone.now()
    qs = visible_to(request.user)
    upcoming = qs.filter(status__in=ACTIVE, ends_at__gte=now).order_by("starts_at")
    past = qs.exclude(id__in=upcoming.values("id")).order_by("-starts_at")[:30]
    return render(request, "customer/my_bookings.html", {"upcoming": upcoming, "past": past})


def _manage_context(request, appt, base_url):
    tz = ZoneInfo(appt.business.timezone)
    return {"appt": appt, "b": appt.business, "start_local": appt.starts_at.astimezone(tz), "base": base_url,
            "can_change": appt.is_upcoming, "late": appt.is_upcoming and appt.inside_cancel_window(),
            "window": appt.business.booking_policy.cancel_window_hours}


@login_required
def my_booking(request, appt_id):
    appt = get_object_or_404(visible_to(request.user), id=appt_id)
    return render(request, "customer/manage.html", _manage_context(request, appt, f"/me/a/{appt.id}/"))


def manage_by_token(request, token):
    appt = AppointmentAccessToken.lookup(token)
    if appt is None:
        return render(request, "customer/link_expired.html", status=404)
    return render(request, "customer/manage.html", _manage_context(request, appt, f"/m/{token}/"))


def _resolve(request, appt_id=None, token=None):
    if token:
        appt = AppointmentAccessToken.lookup(token)
        if appt is None:
            raise Http404()
        return appt, f"/m/{token}/"
    if not request.user.is_authenticated:
        raise Http404()
    return get_object_or_404(visible_to(request.user), id=appt_id), f"/me/a/{appt_id}/"


@require_POST
def do_cancel(request, appt_id=None, token=None):
    appt, base = _resolve(request, appt_id, token)
    actor = request.user if request.user.is_authenticated else None
    try:
        cancel_by_client(appt, actor=actor, reason=(request.POST.get("reason") or "")[:200])
        messages.success(request, "Your appointment is cancelled.")
    except BookingError as e:
        messages.error(request, str(e))
    return redirect(base)


def do_reschedule(request, appt_id=None, token=None):
    appt, base = _resolve(request, appt_id, token)
    if not appt.is_upcoming:
        messages.error(request, "This booking can't be moved.")
        return redirect(base)
    b = appt.business
    tz = ZoneInfo(b.timezone)
    if request.method == "POST":
        try:
            new_start = datetime.fromisoformat(request.POST.get("starts_at", ""))
            reschedule(appt, new_start, actor=request.user if request.user.is_authenticated else None)
            messages.success(request, "Done — your booking has moved.")
            return redirect(base)
        except (ValueError, BookingError) as e:
            messages.error(request, str(e) if isinstance(e, BookingError) else "Pick a time from the list.")
    day, today = _parse_day(request.GET.get("date"), tz)

    def starts_on(d):
        return staff_slots(b, appt.service, [appt.staff], d, exclude_appointment_id=appt.id).get(appt.staff_id, [])

    starts = starts_on(day)
    if not request.GET.get("date"):
        for i in range(1, DAYS_SHOWN):
            if starts:
                break
            day = today + timedelta(days=i)
            starts = starts_on(day)
    return render(request, "customer/reschedule.html", {
        "appt": appt, "b": b, "base": base, "day": day,
        "days": _day_strip([appt.staff], today, day, b.booking_policy.max_advance_days),
        "slots": [s.astimezone(tz) for s in starts], "start_local": appt.starts_at.astimezone(tz),
    })


# ---------------------------------------------------------------- static pages

def for_business(request):
    return render(request, "customer/for_business.html")


def privacy(request):
    return render(request, "customer/privacy.html")

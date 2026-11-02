"""Provider interface: calendar, approvals, walk-ins, clients, services, team, hours, time off, settings.

Every view goes through core.tenancy.business_view: active membership for the business in the URL
(or 404), 30-day re-verification, and a capability check. Querysets are always scoped to
request.business, and staff without MANAGE_ALL_APPOINTMENTS only ever see their own bookings.
"""
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.db.models import Count, Max, Q
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from accounts import otp
from accounts.models import User
from booking import lifecycle
from booking.lifecycle import BookingError
from booking.models import ACTIVE, BLOCKING, Appointment, S, Source
from businesses.models import (WEEKDAYS, BusinessMember, Role, Schedule, Service, Staff, StaffService, TimeOff)
from clients.models import Customer
from clients.services import client_for_provider_booking
from core.permissions import Cap, has_cap
from core.sessions import strong_verification_within
from core.tenancy import business_view

from .forms import (ClientNotesForm, InviteForm, PolicyForm, ProfileForm, RescheduleForm, ServiceForm, StaffForm,
                    TimeOffForm, WalkInForm)

HOUR_PX = 64


# ---------------------------------------------------------------- helpers

def tz_of(request):
    return ZoneInfo(request.business.timezone)


def can(request, cap):
    return has_cap(request.membership, cap)


def scoped_appointments(request):
    qs = (Appointment.objects.filter(business=request.business).exclude(status=S.HELD)
          .select_related("service", "staff", "customer"))
    if not can(request, Cap.MANAGE_ALL_APPOINTMENTS):
        qs = qs.filter(staff=request.staff) if request.staff else qs.none()
    return qs


def visible_staff(request):
    qs = Staff.objects.filter(business=request.business)
    if not can(request, Cap.VIEW_ALL_CALENDARS):
        qs = qs.filter(id=request.staff.id) if request.staff else qs.none()
    return qs


def step_up(request):
    """Sensitive changes need a phone verification within the last 15 minutes."""
    if strong_verification_within(request, settings.STEP_UP_MINUTES * 60):
        return None
    messages.info(request, "For security, please confirm it's you before changing this.")
    return redirect(f"{reverse('accounts:reverify')}?next={request.get_full_path()}")


def parse_day(raw, tz):
    try:
        return date.fromisoformat(raw)
    except (TypeError, ValueError):
        return timezone.now().astimezone(tz).date()


def nav_counts(request):
    return {"pending": scoped_appointments(request).filter(status=S.PENDING_APPROVAL).count()}


def ctx(request, section, **extra):
    base = {"b": request.business, "m": request.membership, "section": section, "counts": nav_counts(request),
            "caps": {c.name: can(request, c) for c in Cap},
            "other_memberships": BusinessMember.objects.filter(user=request.user, status="active")
            .exclude(id=request.membership.id).select_related("business")}
    base.update(extra)
    return base


# ---------------------------------------------------------------- entry + invites

@login_required
def home(request):
    members = list(BusinessMember.objects.filter(user=request.user, status__in=["active", "invited"])
                   .select_related("business"))
    active = [m for m in members if m.status == "active"]
    invites = [m for m in members if m.status == "invited"]
    if len(active) == 1 and not invites:
        return redirect("provider:dashboard", business_id=active[0].business_id)
    return render(request, "provider/choose.html", {"active": active, "invites": invites})


@login_required
@require_POST
def accept_invite(request, member_id):
    m = get_object_or_404(BusinessMember, id=member_id, user=request.user, status="invited")
    m.accept()
    messages.success(request, f"Welcome to {m.business.name}!")
    return redirect("provider:dashboard", business_id=m.business_id)


# ---------------------------------------------------------------- dashboard

@business_view(Cap.VIEW_OWN_CALENDAR)
def dashboard(request, business_id):
    tz = tz_of(request)
    now = timezone.now()
    today = now.astimezone(tz).date()
    d0 = datetime.combine(today, time.min, tzinfo=tz)
    qs = scoped_appointments(request)
    todays = qs.filter(starts_at__gte=d0, starts_at__lt=d0 + timedelta(days=1), status__in=BLOCKING)
    week = qs.filter(starts_at__gte=d0, starts_at__lt=d0 + timedelta(days=7), status__in=ACTIVE)
    pending = qs.filter(status=S.PENDING_APPROVAL).order_by("approval_expires_at")
    return render(request, "provider/dashboard.html", ctx(
        request, "dashboard", today=today, todays=todays.order_by("starts_at"), pending=pending,
        stats={"today": todays.exclude(status__in=[S.NO_SHOW]).count(), "week": week.count(),
               "pending": pending.count(),
               "revenue_today": sum(a.price_cents for a in todays.filter(status__in=[S.CONFIRMED, S.COMPLETED]))},
        booking_link=f"{settings.PUBLIC_BASE_URL.rstrip('/')}/b/{request.business.slug}/",
        setup=setup_checklist(request),
    ))


def setup_checklist(request):
    b = request.business
    items = [
        ("Add your services", b.services.exists(), "provider:services"),
        ("Add your team", b.staff.exists(), "provider:team"),
        ("Set working hours", Schedule.objects.filter(staff__business=b).exists(), "provider:hours"),
        ("Link services to your team", StaffService.objects.filter(staff__business=b).exists(), "provider:team"),
    ]
    return [i for i in items if not i[1]] if can(request, Cap.MANAGE_SERVICES) else []


# ---------------------------------------------------------------- calendar

@business_view(Cap.VIEW_OWN_CALENDAR)
def calendar(request, business_id):
    tz = tz_of(request)
    day = parse_day(request.GET.get("date"), tz)
    view = request.GET.get("view", "day")
    staff_all = list(visible_staff(request).filter(active=True))
    if not staff_all:
        return render(request, "provider/calendar.html", ctx(request, "calendar", empty=True, day=day, view=view))
    selected = next((s for s in staff_all if str(s.id) == request.GET.get("staff")), None)

    if view == "week":
        person = selected or (request.staff if request.staff in staff_all else staff_all[0])
        week_start = day - timedelta(days=day.weekday())
        columns = [{"label": (week_start + timedelta(days=i)).strftime("%a %-d"), "day": week_start + timedelta(days=i),
                    "staff": person} for i in range(7)]
    else:
        people = [selected] if selected else staff_all
        columns = [{"label": s.display_name, "day": day, "staff": s} for s in people]

    ids = {c["staff"].id for c in columns}
    sched = Schedule.objects.filter(staff_id__in=ids)
    first = min([s.start_time.hour for s in sched] or [8])
    last = max([(s.end_time.hour + (1 if s.end_time.minute else 0)) for s in sched] or [19])
    first, last = min(first, 8), max(last, 18)

    days = {c["day"] for c in columns}
    span0 = datetime.combine(min(days), time.min, tzinfo=tz)
    span1 = datetime.combine(max(days), time.min, tzinfo=tz) + timedelta(days=1)
    appts = list(scoped_appointments(request).filter(staff_id__in=ids, starts_at__gte=span0, starts_at__lt=span1)
                 .filter(status__in=BLOCKING + [S.HELD]).order_by("starts_at"))
    offs = list(TimeOff.objects.filter(staff_id__in=ids, starts_at__lt=span1, ends_at__gt=span0))
    by_staff_day = {}
    for s in sched:
        by_staff_day.setdefault((s.staff_id, s.weekday), []).append((s.start_time, s.end_time))

    def y(dt_local, col_day):
        base = datetime.combine(col_day, time(first), tzinfo=tz)
        return max(0, int((dt_local - base).total_seconds() / 3600 * HOUR_PX))

    total_h = (last - first) * HOUR_PX
    for c in columns:
        col_day, st = c["day"], c["staff"]
        c["events"], c["off"] = [], []
        for a in appts:
            ls = a.starts_at.astimezone(tz)
            if a.staff_id == st.id and ls.date() == col_day:
                top = y(ls, col_day)
                h = max(22, int(a.duration_min / 60 * HOUR_PX) - 2)
                c["events"].append({"a": a, "top": top, "h": min(h, total_h - top), "local": ls})
        for t in offs:
            if t.staff_id != st.id:
                continue
            s_l, e_l = t.starts_at.astimezone(tz), t.ends_at.astimezone(tz)
            d0 = datetime.combine(col_day, time(first), tzinfo=tz)
            d1 = datetime.combine(col_day, time.min, tzinfo=tz) + timedelta(hours=last)
            if s_l < d1 and e_l > d0:
                top = y(max(s_l, d0), col_day)
                c["events"].append({"timeoff": t, "top": top, "h": y(min(e_l, d1), col_day) - top})
        # shade hours outside the schedule
        ranges = sorted(by_staff_day.get((st.id, col_day.weekday()), []))
        cursor = time(first)
        for a_, z_ in ranges:
            if a_ > cursor:
                c["off"].append((y(datetime.combine(col_day, cursor, tzinfo=tz), col_day),
                                 y(datetime.combine(col_day, a_, tzinfo=tz), col_day)))
            cursor = max(cursor, z_)
        end = datetime.combine(col_day, time.min, tzinfo=tz) + timedelta(hours=last)
        start_off = datetime.combine(col_day, cursor, tzinfo=tz)
        if start_off < end:
            c["off"].append((y(start_off, col_day), y(end, col_day)))
        c["off"] = [(t, h - t) for t, h in c["off"] if h > t]

    now_local = timezone.now().astimezone(tz)
    now_y = y(now_local, now_local.date()) if any(c["day"] == now_local.date() for c in columns) \
        and first <= now_local.hour < last else None
    step = 7 if view == "week" else 1
    context = ctx(request, "calendar", columns=columns, hours=[time(h) for h in range(first, last)],
                  total_h=total_h, day=day, view=view, staff_all=staff_all, selected=selected, now_y=now_y,
                  prev=day - timedelta(days=step), next=day + timedelta(days=step),
                  today=now_local.date(), scroll_to=max(0, (now_y or 0) - 120),
                  grid_cols=f"64px repeat({len(columns)}, minmax(150px, 1fr))")
    template = "provider/_calendar_grid.html" if request.headers.get("HX-Request") else "provider/calendar.html"
    return render(request, template, context)


# ---------------------------------------------------------------- appointments

@business_view(Cap.VIEW_OWN_CALENDAR)
def appointments(request, business_id):
    tz = tz_of(request)
    qs = scoped_appointments(request)
    status = request.GET.get("status", "upcoming")
    now = timezone.now()
    if status == "upcoming":
        qs = qs.filter(status__in=ACTIVE, ends_at__gte=now).order_by("starts_at")
    elif status == "pending":
        qs = qs.filter(status=S.PENDING_APPROVAL).order_by("starts_at")
    elif status == "past":
        qs = qs.filter(starts_at__lt=now).order_by("-starts_at")
    elif status == "cancelled":
        qs = qs.filter(status__in=[S.CANCELLED_BY_CLIENT, S.CANCELLED_BY_PROVIDER, S.DECLINED, S.EXPIRED]) \
            .order_by("-starts_at")
    else:
        qs = qs.order_by("-starts_at")
    q = (request.GET.get("q") or "").strip()
    if q:
        qs = qs.filter(Q(customer__name__icontains=q) | Q(customer__phone__icontains=q) | Q(service__name__icontains=q))
    return render(request, "provider/appointments.html", ctx(request, "appointments", rows=qs[:200], status=status,
                                                              q=q, tz=tz))


def _own_appt(request, appt_id):
    appt = get_object_or_404(scoped_appointments(request), id=appt_id)
    if not (can(request, Cap.MANAGE_ALL_APPOINTMENTS) or (request.staff and appt.staff_id == request.staff.id)):
        raise Http404()
    return appt


@business_view(Cap.VIEW_OWN_CALENDAR)
def appointment_detail(request, business_id, appt_id):
    appt = _own_appt(request, appt_id)
    tz = tz_of(request)
    local = appt.starts_at.astimezone(tz)
    form = RescheduleForm(initial={"staff": appt.staff, "date": local.date(), "time": local.time()},
                          staff_qs=visible_staff(request).filter(active=True))
    history = (Appointment.objects.filter(business=request.business, customer=appt.customer)
               .exclude(id=appt.id).exclude(status=S.HELD).order_by("-starts_at")[:8]) if appt.customer else []
    return render(request, "provider/appointment.html", ctx(
        request, "appointments", appt=appt, local=local, form=form, history=history,
        clash=lifecycle.clashes_with_time_off(appt), now=timezone.now(),
        messages_sent=appt.messages.order_by("-created_at")[:10]))


@business_view(Cap.MANAGE_OWN_APPOINTMENTS)
@require_POST
def appointment_action(request, business_id, appt_id, action):
    appt = _own_appt(request, appt_id)
    b, actor = request.business, request.user
    reason = (request.POST.get("reason") or "").strip()
    try:
        if action == "approve":
            lifecycle.approve(appt.id, business=b, actor=actor)
            messages.success(request, "Approved — the client has been told.")
        elif action == "decline":
            lifecycle.decline(appt.id, business=b, actor=actor, reason=reason)
            messages.success(request, "Declined — the client has been told.")
        elif action == "cancel":
            lifecycle.cancel_by_provider(appt.id, business=b, actor=actor, reason=reason)
            messages.success(request, "Cancelled — the client has been told.")
        elif action == "no-show":
            lifecycle.mark_no_show(appt.id, business=b, actor=actor)
            messages.success(request, "Marked as no-show.")
        elif action == "complete":
            lifecycle.mark_completed(appt.id, business=b, actor=actor)
            messages.success(request, "Marked as completed.")
        elif action == "reschedule":
            staff_qs = visible_staff(request).filter(active=True)
            form = RescheduleForm(request.POST, staff_qs=staff_qs)
            if not form.is_valid():
                raise BookingError("Choose a team member, date and time.")
            tz = tz_of(request)
            new_start = datetime.combine(form.cleaned_data["date"], form.cleaned_data["time"], tzinfo=tz)
            lifecycle.reschedule(appt, new_start, actor=actor, by_provider=True, new_staff=form.cleaned_data["staff"])
            messages.success(request, "Moved — the client has been told.")
        elif action == "notes":
            Appointment.objects.filter(id=appt.id).update(provider_notes=(request.POST.get("notes") or "")[:2000])
            messages.success(request, "Note saved.")
        else:
            raise Http404()
    except BookingError as e:
        messages.error(request, str(e))
    nxt = request.POST.get("next")
    if nxt and nxt.startswith(f"/provider/{business_id}/"):
        return redirect(nxt)
    return redirect("provider:appointment", business_id=business_id, appt_id=appt.id)


@business_view(Cap.CREATE_BOOKING_FOR_SELF)
def new_booking(request, business_id):
    b = request.business
    tz = tz_of(request)
    staff_qs = Staff.objects.filter(business=b, active=True)
    if not can(request, Cap.MANAGE_ALL_APPOINTMENTS):
        staff_qs = staff_qs.filter(id=request.staff.id) if request.staff else staff_qs.none()
    now_l = timezone.now().astimezone(tz)
    initial = {"date": request.GET.get("date") or now_l.date(), "staff": request.GET.get("staff") or request.staff,
               "time": request.GET.get("time") or (now_l + timedelta(minutes=15 - now_l.minute % 15)).time()
               .replace(second=0, microsecond=0)}
    form = WalkInForm(request.POST or None, business=b, staff_qs=staff_qs, initial=initial)
    if request.method == "POST" and form.is_valid():
        cd = form.cleaned_data
        phone = None
        if cd["client_phone"]:
            try:
                phone = otp.normalise_phone(cd["client_phone"])
            except otp.OtpError as e:
                form.add_error("client_phone", str(e))
        if not form.errors:
            if not StaffService.objects.filter(staff=cd["staff"], service=cd["service"]).exists():
                messages.warning(request, f"{cd['staff']} isn't set up for {cd['service']} — booked anyway.")
            customer = client_for_provider_booking(b, name=cd["client_name"].strip(), phone=phone,
                                                   member=request.membership, source=cd["source"])
            try:
                appt = lifecycle.create_provider_booking(
                    business=b, staff=cd["staff"], service=cd["service"], customer=customer,
                    starts_at=datetime.combine(cd["date"], cd["time"], tzinfo=tz), member=request.membership,
                    source=Source(cd["source"]), allow_overlap=cd.get("allow_overlap", False), notes=cd["notes"])
                messages.success(request, "Booked.")
                return redirect("provider:appointment", business_id=business_id, appt_id=appt.id)
            except BookingError as e:
                form.add_error("time", str(e))
    return render(request, "provider/new_booking.html", ctx(request, "new", form=form))


# ---------------------------------------------------------------- clients

@business_view(Cap.VIEW_CLIENTS)
def clients(request, business_id):
    qs = Customer.objects.filter(business=request.business).annotate(
        visits=Count("appointments", filter=Q(appointments__status__in=[S.COMPLETED, S.CONFIRMED])),
        no_shows=Count("appointments", filter=Q(appointments__status=S.NO_SHOW)),
        last_visit=Max("appointments__starts_at"))
    q = (request.GET.get("q") or "").strip()
    if q:
        qs = qs.filter(Q(name__icontains=q) | Q(phone__icontains=q) | Q(email__icontains=q))
    return render(request, "provider/clients.html", ctx(request, "clients", rows=qs.order_by("name")[:300], q=q))


@business_view(Cap.VIEW_CLIENTS)
def client_detail(request, business_id, client_id):
    c = get_object_or_404(Customer, business=request.business, id=client_id)
    form = ClientNotesForm(request.POST or None, initial={"notes": c.notes})
    if request.method == "POST":
        if not can(request, Cap.EDIT_CLIENT_NOTES):
            raise Http404()
        if form.is_valid():
            c.notes = form.cleaned_data["notes"]
            c.save(update_fields=["notes"])
            messages.success(request, "Notes saved.")
            return redirect("provider:client", business_id=business_id, client_id=c.id)
    appts = scoped_appointments(request).filter(customer=c).order_by("-starts_at")
    return render(request, "provider/client.html", ctx(request, "clients", c=c, form=form, appts=appts))


# ---------------------------------------------------------------- services

@business_view(Cap.MANAGE_SERVICES)
def services(request, business_id):
    rows = Service.objects.filter(business=request.business).annotate(staff_count=Count("staff"))
    return render(request, "provider/services.html", ctx(request, "services", rows=rows))


@business_view(Cap.MANAGE_SERVICES)
def service_edit(request, business_id, service_id=None):
    inst = get_object_or_404(Service, business=request.business, id=service_id) if service_id else None
    form = ServiceForm(request.POST or None, instance=inst)
    if request.method == "POST" and form.is_valid():
        with transaction.atomic():
            s = form.save(commit=False)
            s.business = request.business
            s.save()
            if inst is None:   # new service: everyone active can do it by default
                for st in Staff.objects.filter(business=request.business, active=True):
                    StaffService.objects.get_or_create(staff=st, service=s)
        messages.success(request, "Service saved.")
        return redirect("provider:services", business_id=business_id)
    return render(request, "provider/form_page.html", ctx(
        request, "services", form=form, title="Edit service" if inst else "New service",
        back=reverse("provider:services", args=[business_id]),
        intro="Existing bookings keep the price and length they were booked with."))


# ---------------------------------------------------------------- team

@business_view(Cap.MANAGE_STAFF)
def team(request, business_id):
    b = request.business
    staff = Staff.objects.filter(business=b).select_related("member__user").prefetch_related("services")
    members = BusinessMember.objects.filter(business=b).exclude(status="removed").select_related("user")
    return render(request, "provider/team.html", ctx(request, "team", staff=staff, members=members,
                                                      invite_form=InviteForm(business=b)))


@business_view(Cap.MANAGE_STAFF)
def staff_edit(request, business_id, staff_id=None):
    b = request.business
    inst = get_object_or_404(Staff, business=b, id=staff_id) if staff_id else None
    if request.method == "POST" and (r := step_up(request)):
        return r
    form = StaffForm(request.POST or None, instance=inst, business=b)
    if request.method == "POST" and form.is_valid():
        with transaction.atomic():
            st = form.save(commit=False)
            st.business = b
            if inst and inst.active and not st.active and Appointment.objects.filter(
                    staff=inst, status__in=ACTIVE, starts_at__gt=timezone.now()).exists():
                form.add_error("active", "Move or cancel this person's upcoming bookings first.")
            else:
                st.save()
                wanted = set(form.cleaned_data["services"])
                StaffService.objects.filter(staff=st).exclude(service__in=wanted).delete()
                for sv in wanted:
                    StaffService.objects.get_or_create(staff=st, service=sv)
                if inst is None:   # sensible default hours: Mon–Sat 9–5
                    for wd in range(6):
                        Schedule.objects.create(staff=st, weekday=wd, start_time=time(9), end_time=time(17))
                messages.success(request, "Saved." if inst else "Added — default hours Mon–Sat 9–5; adjust under Hours.")
                return redirect("provider:team", business_id=business_id)
    return render(request, "provider/form_page.html", ctx(
        request, "team", form=form, title=f"Edit {inst.display_name}" if inst else "Add a team member",
        back=reverse("provider:team", args=[business_id]),
        intro="Team members don't need a login to be bookable. Invite them from the Team page if they should see their calendar."))


@business_view(Cap.MANAGE_STAFF)
@require_POST
def invite(request, business_id):
    if r := step_up(request):
        return r
    b = request.business
    form = InviteForm(request.POST, business=b)
    if form.is_valid():
        try:
            phone = otp.normalise_phone(form.cleaned_data["phone"])
        except otp.OtpError as e:
            messages.error(request, str(e))
            return redirect("provider:team", business_id=business_id)
        with transaction.atomic():
            user, _ = User.objects.get_or_create(phone=phone)
            member, created = BusinessMember.objects.get_or_create(
                business=b, user=user, defaults={"role": Role.STAFF, "invited_by": request.user})
            if not created and member.status == "removed":
                member.status, member.role, member.removed_at = "invited", Role.STAFF, None
                member.save()
            st = form.cleaned_data.get("staff")
            if st and not st.member_id:
                st.member = member
                st.save(update_fields=["member"])
        from notifications.senders import send
        send(phone, f"{request.user.full_name or 'Your team'} invited you to {b.name} on {settings.PLATFORM_NAME}. "
                    f"Sign in with this number to accept: {settings.PUBLIC_BASE_URL.rstrip('/')}/provider/", "whatsapp")
        messages.success(request, f"Invite sent to {phone}. They'll see it when they sign in with that number.")
    else:
        messages.error(request, "Check the phone number.")
    return redirect("provider:team", business_id=business_id)


@business_view(Cap.MANAGE_STAFF)
@require_POST
def remove_member(request, business_id, member_id):
    if r := step_up(request):
        return r
    m = get_object_or_404(BusinessMember, business=request.business, id=member_id)
    if m.role == Role.OWNER:
        messages.error(request, "The owner can't be removed. Contact support to transfer ownership.")
    else:
        m.status, m.removed_at = "removed", timezone.now()
        m.save(update_fields=["status", "removed_at"])
        if not BusinessMember.objects.filter(user=m.user, status="active").exists():
            m.user.sign_out_everywhere()
        messages.success(request, f"{m.user} no longer has access. Their bookable profile stays for history.")
    return redirect("provider:team", business_id=business_id)


# ---------------------------------------------------------------- hours + time off

@business_view(Cap.MANAGE_HOURS_ALL)
def hours(request, business_id):
    b = request.business
    staff_list = list(Staff.objects.filter(business=b, active=True))
    if not staff_list:
        messages.info(request, "Add a team member first.")
        return redirect("provider:team", business_id=business_id)
    st = next((s for s in staff_list if str(s.id) == request.GET.get("staff")), staff_list[0])
    if request.method == "POST":
        rows, errors = [], []
        for wd, name in WEEKDAYS:
            starts = request.POST.getlist(f"start_{wd}")
            ends = request.POST.getlist(f"end_{wd}")
            for a, z in zip(starts, ends):
                if not a and not z:
                    continue
                try:
                    ta, tz_ = time.fromisoformat(a), time.fromisoformat(z)
                except ValueError:
                    errors.append(f"{name}: enter both times.")
                    continue
                if ta >= tz_:
                    errors.append(f"{name}: the end must be after the start.")
                    continue
                rows.append(Schedule(staff=st, weekday=wd, start_time=ta, end_time=tz_))
        if errors:
            for e in errors:
                messages.error(request, e)
        else:
            with transaction.atomic():
                Schedule.objects.filter(staff=st).delete()
                Schedule.objects.bulk_create(rows)
            messages.success(request, f"Hours saved for {st.display_name}.")
            return redirect(f"{reverse('provider:hours', args=[business_id])}?staff={st.id}")
    by_day = {wd: [] for wd, _ in WEEKDAYS}
    for s in Schedule.objects.filter(staff=st):
        by_day[s.weekday].append(s)
    return render(request, "provider/hours.html", ctx(request, "hours", staff_list=staff_list, st=st,
                                                       days=[(wd, name, by_day[wd]) for wd, name in WEEKDAYS]))


@business_view(Cap.BLOCK_OWN_TIME)
def time_off(request, business_id):
    tz = tz_of(request)
    staff_qs = visible_staff(request).filter(active=True) if can(request, Cap.MANAGE_HOURS_ALL) else \
        Staff.objects.filter(id=getattr(request.staff, "id", None))
    form = TimeOffForm(request.POST or None, staff_qs=staff_qs, initial={"staff": request.staff})
    if request.method == "POST" and form.is_valid():
        cd = form.cleaned_data
        t = TimeOff.objects.create(staff=cd["staff"], starts_at=cd["starts"].replace(tzinfo=tz) if timezone.is_naive(
            cd["starts"]) else cd["starts"], ends_at=cd["ends"].replace(tzinfo=tz) if timezone.is_naive(cd["ends"])
            else cd["ends"], reason=cd["reason"])
        clashes = Appointment.objects.filter(staff=t.staff, status__in=ACTIVE, starts_at__lt=t.ends_at,
                                             ends_at__gt=t.starts_at).count()
        if clashes:
            messages.warning(request, f"Time blocked. {clashes} existing booking(s) fall in this time — "
                                      f"they're highlighted on the calendar so you can move them.")
        else:
            messages.success(request, "Time blocked.")
        return redirect("provider:time_off", business_id=business_id)
    rows = TimeOff.objects.filter(staff__in=staff_qs, ends_at__gte=timezone.now()).select_related("staff")
    return render(request, "provider/time_off.html", ctx(request, "time_off", form=form, rows=rows, tz=tz))


@business_view(Cap.BLOCK_OWN_TIME)
@require_POST
def time_off_delete(request, business_id, item_id):
    t = get_object_or_404(TimeOff, id=item_id, staff__business=request.business)
    if not (can(request, Cap.MANAGE_HOURS_ALL) or (request.staff and t.staff_id == request.staff.id)):
        raise Http404()
    t.delete()
    messages.success(request, "Removed.")
    return redirect("provider:time_off", business_id=business_id)


# ---------------------------------------------------------------- settings

@business_view(Cap.MANAGE_PROFILE)
def settings_view(request, business_id):
    b = request.business
    policy = b.booking_policy
    which = request.POST.get("form")
    if request.method == "POST" and which == "policy" and (r := step_up(request)):
        return r
    pform = ProfileForm(request.POST if which == "profile" else None, instance=b, prefix="p")
    polform = PolicyForm(request.POST if which == "policy" else None, instance=policy, prefix="r")
    if request.method == "POST":
        f = pform if which == "profile" else polform
        if f.is_valid():
            f.save()
            messages.success(request, "Settings saved.")
            return redirect("provider:settings", business_id=business_id)
    return render(request, "provider/settings.html", ctx(
        request, "settings", pform=pform, polform=polform,
        booking_link=f"{settings.PUBLIC_BASE_URL.rstrip('/')}/b/{b.slug}/"))

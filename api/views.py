"""REST API v1 (architecture plan: API design). Serves the website today and the mobile app later.

Public endpoints opt out of authentication explicitly; everything else is deny-by-default.
Business endpoints carry the business id in the path and go through the four tenancy layers.
"""
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from django.conf import settings
from django.contrib.auth import logout
from django.db import transaction
from django.db.models import Q
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import status, viewsets
from rest_framework.decorators import action, api_view, permission_classes, throttle_classes
from rest_framework.exceptions import NotFound, ValidationError
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView

from accounts import otp
from accounts.models import User
from booking import lifecycle
from booking.availability import available_slots, bookable_staff
from booking.lifecycle import BookingError, SlotTaken
from booking.models import ACTIVE, Appointment, S, Source
from businesses.models import Business, BusinessMember, Category, Schedule, Service, Staff, TimeOff, Town
from clients.services import client_for_provider_booking
from core.permissions import Cap, has_cap
from core.sessions import start_session
from core.tenancy import BusinessScopedViewSet, HasCapability, IsBusinessMember

from . import serializers as s


def listed():
    return Business.objects.filter(status=Business.Status.LISTED).select_related("category", "town",
                                                                                  "booking_policy")


class Throttled(ScopedRateThrottle):
    pass


def err(e: BookingError, http=status.HTTP_400_BAD_REQUEST):
    code = status.HTTP_409_CONFLICT if isinstance(e, SlotTaken) else http
    return Response({"detail": str(e)}, status=code)


# ---------------------------------------------------------------- public

@api_view(["GET"])
@permission_classes([AllowAny])
def categories(request):
    return Response({"categories": s.CategorySerializer(Category.objects.filter(active=True), many=True).data,
                     "towns": s.TownSerializer(Town.objects.filter(active=True), many=True).data})


@api_view(["GET"])
@permission_classes([AllowAny])
def search(request):
    qs = listed()
    if request.GET.get("category"):
        qs = qs.filter(category__slug=request.GET["category"])
    if request.GET.get("town"):
        qs = qs.filter(town__slug=request.GET["town"])
    if q := (request.GET.get("q") or "").strip()[:80]:
        qs = qs.filter(Q(name__icontains=q) | Q(services__name__icontains=q)).distinct()
    return Response({"results": s.PublicBusinessSerializer(qs.order_by("-featured", "name")[:50], many=True).data})


@api_view(["GET"])
@permission_classes([AllowAny])
def business_detail(request, slug):
    return Response(s.PublicBusinessDetailSerializer(get_object_or_404(listed(), slug=slug)).data)


@api_view(["GET"])
@permission_classes([AllowAny])
def availability(request, slug):
    """One day per call (API4: resource limits)."""
    b = get_object_or_404(listed(), slug=slug)
    service = get_object_or_404(Service, business=b, active=True, id=request.GET.get("service"))
    staff = None
    if request.GET.get("staff") not in (None, "", "any"):
        staff = get_object_or_404(Staff, business=b, id=request.GET["staff"], active=True, bookable_online=True)
    try:
        day = date.fromisoformat(request.GET.get("date", ""))
    except ValueError:
        raise ValidationError({"date": "Use YYYY-MM-DD."})
    tz = ZoneInfo(b.timezone)
    slots = available_slots(b, service, staff, day)
    return Response({"date": day.isoformat(), "timezone": b.timezone,
                     "slots": [{"starts_at": st.astimezone(tz).isoformat(), "staff": [str(i) for i in ids]}
                               for st, ids in slots]})


def checkout_token(request):
    tok = request.session.get("checkout_token")
    if not tok:
        import secrets
        tok = request.session["checkout_token"] = secrets.token_urlsafe(24)
    return tok


class HoldView(APIView):
    permission_classes = [AllowAny]
    throttle_classes = [Throttled]
    throttle_scope = "holds"

    def post(self, request):
        ser = s.HoldRequestSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        d = ser.validated_data
        b = get_object_or_404(listed(), slug=d["business"])
        service = get_object_or_404(Service, business=b, active=True, id=d["service"])
        cands = bookable_staff(b, service)
        if d["staff"] != "any":
            cands = [c for c in cands if str(c.id) == d["staff"]]
        try:
            appt = lifecycle.create_hold(business=b, service=service, staff_candidates=cands,
                                         starts_at=d["starts_at"], checkout_token=checkout_token(request))
        except BookingError as e:
            return err(e)
        return Response({"id": str(appt.id), "staff": str(appt.staff_id), "starts_at": appt.starts_at,
                         "hold_expires_at": appt.hold_expires_at, "price_cents": appt.price_cents},
                        status=status.HTTP_201_CREATED)


@api_view(["DELETE"])
@permission_classes([AllowAny])
def release_hold(request, hold_id):
    lifecycle.release_hold(hold_id=hold_id, checkout_token=request.session.get("checkout_token", ""))
    return Response(status=status.HTTP_204_NO_CONTENT)


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def confirm_hold(request, hold_id):
    ser = s.ConfirmSerializer(data=request.data)
    ser.is_valid(raise_exception=True)
    try:
        appt, _ = lifecycle.confirm_hold(hold_id=hold_id, checkout_token=request.session.get("checkout_token", ""),
                                         user=request.user, customer_notes=ser.validated_data.get("notes", ""),
                                         idempotency_key=request.headers.get("Idempotency-Key"))
    except BookingError as e:
        return err(e)
    nxt = {"pending_approval": "awaiting_approval", "awaiting_deposit": "pay_deposit"}.get(appt.status, "confirmed")
    return Response({"appointment": s.AppointmentReadSerializer(appt).data, "next_step": nxt},
                    status=status.HTTP_201_CREATED)


# ---------------------------------------------------------------- auth

class OtpStart(APIView):
    permission_classes = [AllowAny]
    throttle_classes = [Throttled]
    throttle_scope = "otp_start"

    def post(self, request):
        ser = s.OtpStartSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        try:
            phone = otp.start_phone_code(ser.validated_data["phone"], channel=ser.validated_data["channel"],
                                         ip=otp.client_ip(request))
        except otp.OtpError as e:
            return Response({"detail": str(e)}, status=status.HTTP_400_BAD_REQUEST)
        return Response({"phone": phone, "sent": True})


class OtpVerify(APIView):
    permission_classes = [AllowAny]
    throttle_classes = [Throttled]
    throttle_scope = "otp_verify"

    def post(self, request):
        ser = s.OtpVerifySerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        d = ser.validated_data
        try:
            phone = otp.normalise_phone(d["phone"])
        except otp.OtpError as e:
            return Response({"detail": str(e)}, status=status.HTTP_400_BAD_REQUEST)
        if not otp.check_phone_code(phone, d["code"]):
            return Response({"detail": "That code didn't work."}, status=status.HTTP_400_BAD_REQUEST)
        tok = request.session.get("checkout_token")
        with transaction.atomic():
            user, _ = User.objects.get_or_create(phone=phone)
            user.phone_verified_at = user.phone_verified_at or timezone.now()
            if d.get("full_name") and not user.full_name:
                user.full_name = d["full_name"]
            if d.get("preferred_channel"):
                user.preferred_channel = d["preferred_channel"]
            if d.get("email") and d["email"].lower() != (user.email or ""):
                user.pending_email = d["email"].lower()
            user.save()
        start_session(request, user, remember=d["remember"], method="phone")
        if tok:
            request.session["checkout_token"] = tok
        return Response({"user": {"id": str(user.id), "full_name": user.full_name, "phone": str(user.phone)},
                         "memberships": memberships_for(user)})


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def auth_logout(request):
    logout(request)
    return Response(status=status.HTTP_204_NO_CONTENT)


def memberships_for(user):
    return [{"id": str(m.id), "business_id": str(m.business_id), "business_name": m.business.name, "role": m.role,
             "status": m.status}
            for m in BusinessMember.objects.filter(user=user).exclude(status="removed").select_related("business")]


# ---------------------------------------------------------------- me

@api_view(["GET"])
@permission_classes([IsAuthenticated])
def my_memberships(request):
    return Response({"memberships": memberships_for(request.user)})


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def accept_invite(request, member_id):
    m = get_object_or_404(BusinessMember, id=member_id, user=request.user, status="invited")
    m.accept()
    return Response({"business_id": str(m.business_id), "status": m.status})


def my_appts(request):
    from django.db.models import F
    return (Appointment.objects.filter(customer__user=request.user)
            .filter(Q(booked_by_user=request.user) | Q(created_at__gte=F("customer__linked_at")))
            .exclude(status=S.HELD).select_related("business", "service", "staff"))


class MyAppointments(viewsets.ViewSet):
    permission_classes = [IsAuthenticated]

    def list(self, request):
        qs = my_appts(request).order_by("-starts_at")[:100]
        return Response({"appointments": s.AppointmentReadSerializer(qs, many=True).data})

    def partial_update(self, request, pk=None):
        appt = get_object_or_404(my_appts(request), id=pk)
        ser = s.RescheduleSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        try:
            appt = lifecycle.reschedule(appt, ser.validated_data["starts_at"], actor=request.user)
        except BookingError as e:
            return err(e)
        return Response(s.AppointmentReadSerializer(appt).data)

    @action(detail=True, methods=["post"])
    def cancel(self, request, pk=None):
        appt = get_object_or_404(my_appts(request), id=pk)
        try:
            appt = lifecycle.cancel_by_client(appt, actor=request.user,
                                              reason=(request.data.get("reason") or "")[:200])
        except BookingError as e:
            return err(e)
        return Response(s.AppointmentReadSerializer(appt).data)


# ---------------------------------------------------------------- business (provider)

class ServiceViewSet(BusinessScopedViewSet):
    queryset = Service.objects.all()
    serializer_class = s.ServiceSerializer
    capabilities = {"list": Cap.VIEW_OWN_CALENDAR, "retrieve": Cap.VIEW_OWN_CALENDAR, "create": Cap.MANAGE_SERVICES,
                    "update": Cap.MANAGE_SERVICES, "partial_update": Cap.MANAGE_SERVICES,
                    "destroy": Cap.MANAGE_SERVICES}

    def perform_destroy(self, inst):       # never hard-delete: bookings point at services
        inst.active = False
        inst.save(update_fields=["active"])


class StaffViewSet(BusinessScopedViewSet):
    queryset = Staff.objects.all()
    serializer_class = s.StaffSerializer
    capabilities = {"list": Cap.VIEW_OWN_CALENDAR, "retrieve": Cap.VIEW_OWN_CALENDAR, "create": Cap.MANAGE_STAFF,
                    "update": Cap.MANAGE_STAFF, "partial_update": Cap.MANAGE_STAFF, "destroy": Cap.MANAGE_STAFF}

    def perform_destroy(self, inst):
        inst.active = False
        inst.save(update_fields=["active"])


class ScheduleViewSet(BusinessScopedViewSet):
    queryset = Schedule.objects.all()
    serializer_class = s.ScheduleSerializer
    capabilities = {"list": Cap.VIEW_OWN_CALENDAR, "retrieve": Cap.VIEW_OWN_CALENDAR,
                    "create": Cap.MANAGE_HOURS_ALL, "update": Cap.MANAGE_HOURS_ALL,
                    "partial_update": Cap.MANAGE_HOURS_ALL, "destroy": Cap.MANAGE_HOURS_ALL}

    def get_queryset(self):
        return Schedule.objects.filter(staff__business=self.request.business)

    def perform_create(self, serializer):
        serializer.save()


class AppointmentViewSet(BusinessScopedViewSet):
    queryset = Appointment.objects.exclude(status=S.HELD).select_related("customer", "service", "staff")
    serializer_class = s.ProviderAppointmentSerializer
    http_method_names = ["get", "post", "patch"]
    capabilities = {"list": Cap.VIEW_OWN_CALENDAR, "retrieve": Cap.VIEW_OWN_CALENDAR,
                    "create": Cap.CREATE_BOOKING_FOR_SELF, "partial_update": Cap.MANAGE_OWN_APPOINTMENTS,
                    "approve": Cap.MANAGE_OWN_APPOINTMENTS, "decline": Cap.MANAGE_OWN_APPOINTMENTS,
                    "cancel": Cap.MANAGE_OWN_APPOINTMENTS, "no_show": Cap.MANAGE_OWN_APPOINTMENTS,
                    "complete": Cap.MANAGE_OWN_APPOINTMENTS}

    def get_queryset(self):
        qs = super().get_queryset()
        if not has_cap(self.request.membership, Cap.MANAGE_ALL_APPOINTMENTS):
            qs = qs.filter(staff=self.request.staff) if self.request.staff else qs.none()
        p = self.request.query_params
        if p.get("from"):
            qs = qs.filter(starts_at__gte=p["from"])
        if p.get("to"):
            qs = qs.filter(starts_at__lt=p["to"])
        if p.get("staff"):
            qs = qs.filter(staff_id=p["staff"])
        return qs.order_by("starts_at")

    def list(self, request, *args, **kwargs):
        qs = self.get_queryset()
        if not (request.query_params.get("from") and request.query_params.get("to")):
            now = timezone.now()
            qs = qs.filter(starts_at__gte=now - timedelta(days=1), starts_at__lt=now + timedelta(days=14))
        return Response({"appointments": self.get_serializer(qs[:500], many=True).data})

    def create(self, request, *args, **kwargs):
        ser = s.WalkInSerializer(data=request.data, context={"request": request})
        ser.is_valid(raise_exception=True)
        d = ser.validated_data
        if not has_cap(request.membership, Cap.MANAGE_ALL_APPOINTMENTS) and d["staff"] != request.staff:
            raise ValidationError({"staff": "You can only book for yourself."})
        phone = None
        if d.get("client_phone"):
            try:
                phone = otp.normalise_phone(d["client_phone"])
            except otp.OtpError as e:
                raise ValidationError({"client_phone": str(e)})
        customer = client_for_provider_booking(request.business, name=d["client_name"], phone=phone,
                                               member=request.membership, source=d["source"])
        try:
            appt = lifecycle.create_provider_booking(
                business=request.business, staff=d["staff"], service=d["service"], customer=customer,
                starts_at=d["starts_at"], member=request.membership, source=Source(d["source"]),
                allow_overlap=d["allow_overlap"], notes=d.get("notes", ""))
        except BookingError as e:
            return err(e)
        return Response(self.get_serializer(appt).data, status=status.HTTP_201_CREATED)

    def partial_update(self, request, *args, **kwargs):
        appt = self.get_object()
        ser = s.RescheduleSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        try:
            appt = lifecycle.reschedule(appt, ser.validated_data["starts_at"], actor=request.user, by_provider=True)
        except BookingError as e:
            return err(e)
        return Response(self.get_serializer(appt).data)

    def _act(self, request, fn, **kw):
        appt = self.get_object()
        try:
            out = fn(appt.id, business=request.business, actor=request.user, **kw)
        except BookingError as e:
            return err(e)
        return Response(self.get_serializer(out).data)

    @action(detail=True, methods=["post"])
    def approve(self, request, *a, **kw):
        return self._act(request, lifecycle.approve)

    @action(detail=True, methods=["post"])
    def decline(self, request, *a, **kw):
        return self._act(request, lifecycle.decline, reason=(request.data.get("reason") or "")[:200])

    @action(detail=True, methods=["post"])
    def cancel(self, request, *a, **kw):
        return self._act(request, lifecycle.cancel_by_provider, reason=(request.data.get("reason") or "")[:200])

    @action(detail=True, methods=["post"], url_path="no-show")
    def no_show(self, request, *a, **kw):
        return self._act(request, lifecycle.mark_no_show)

    @action(detail=True, methods=["post"])
    def complete(self, request, *a, **kw):
        return self._act(request, lifecycle.mark_completed)

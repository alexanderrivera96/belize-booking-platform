"""Explicit serializers only (no fields='__all__'); status, prices and ownership are never client-writable."""
from rest_framework import serializers

from booking.models import Appointment
from businesses.models import Business, Category, Schedule, Service, Staff, Town
from core.tenancy import BusinessScopedPK


class CategorySerializer(serializers.ModelSerializer):
    class Meta:
        model = Category
        fields = ["slug", "name"]


class TownSerializer(serializers.ModelSerializer):
    class Meta:
        model = Town
        fields = ["slug", "name", "district"]


class PublicServiceSerializer(serializers.ModelSerializer):
    class Meta:
        model = Service
        fields = ["id", "name", "description", "duration_min", "price_cents"]


class PublicStaffSerializer(serializers.ModelSerializer):
    class Meta:
        model = Staff
        fields = ["id", "display_name", "title"]


class PublicBusinessSerializer(serializers.ModelSerializer):
    category = CategorySerializer()
    town = TownSerializer()
    requires_approval = serializers.BooleanField(source="booking_policy.requires_approval")

    class Meta:
        model = Business
        fields = ["slug", "name", "tagline", "description", "address", "category", "town", "timezone", "currency",
                  "requires_approval"]


class PublicBusinessDetailSerializer(PublicBusinessSerializer):
    services = serializers.SerializerMethodField()
    staff = serializers.SerializerMethodField()

    class Meta(PublicBusinessSerializer.Meta):
        fields = PublicBusinessSerializer.Meta.fields + ["services", "staff"]

    def get_services(self, b):
        return PublicServiceSerializer(b.services.filter(active=True), many=True).data

    def get_staff(self, b):
        return PublicStaffSerializer(b.staff.filter(active=True, bookable_online=True), many=True).data


class HoldRequestSerializer(serializers.Serializer):
    business = serializers.SlugField()
    service = serializers.UUIDField()
    staff = serializers.CharField(default="any")
    starts_at = serializers.DateTimeField()


class ConfirmSerializer(serializers.Serializer):
    notes = serializers.CharField(required=False, allow_blank=True, max_length=1000)


class OtpStartSerializer(serializers.Serializer):
    phone = serializers.CharField(max_length=20)
    channel = serializers.ChoiceField(choices=["whatsapp", "sms"], default="whatsapp")


class OtpVerifySerializer(serializers.Serializer):
    phone = serializers.CharField(max_length=20)
    code = serializers.CharField(max_length=8)
    remember = serializers.BooleanField(default=True)
    full_name = serializers.CharField(max_length=150, required=False, allow_blank=True)
    email = serializers.EmailField(required=False, allow_blank=True)
    preferred_channel = serializers.ChoiceField(choices=["whatsapp", "sms"], required=False)


class AppointmentReadSerializer(serializers.ModelSerializer):
    business = serializers.CharField(source="business.name")
    business_slug = serializers.CharField(source="business.slug")
    service = serializers.CharField(source="service.name")
    staff = serializers.CharField(source="staff.display_name")

    class Meta:
        model = Appointment
        fields = ["id", "business", "business_slug", "service", "staff", "starts_at", "ends_at", "status",
                  "price_cents", "deposit_cents", "customer_notes"]


class ProviderAppointmentSerializer(serializers.ModelSerializer):
    customer = serializers.SerializerMethodField()
    service = serializers.CharField(source="service.name")
    staff_id = serializers.UUIDField()

    class Meta:
        model = Appointment
        fields = ["id", "staff_id", "service", "customer", "starts_at", "ends_at", "status", "source",
                  "price_cents", "customer_notes", "provider_notes", "late_cancellation", "version"]

    def get_customer(self, a):
        c = a.customer
        return {"id": str(c.id), "name": c.name, "phone": str(c.phone) if c.phone else None} if c else None


class RescheduleSerializer(serializers.Serializer):
    starts_at = serializers.DateTimeField()


class ReasonSerializer(serializers.Serializer):
    reason = serializers.CharField(required=False, allow_blank=True, max_length=200)


class ServiceSerializer(serializers.ModelSerializer):
    class Meta:
        model = Service
        fields = ["id", "name", "description", "duration_min", "price_cents", "buffer_before_min", "buffer_after_min",
                  "active", "sort_order"]
        read_only_fields = ["id"]


class StaffSerializer(serializers.ModelSerializer):
    services = BusinessScopedPK(queryset=Service.objects.all(), many=True, required=False)

    class Meta:
        model = Staff
        fields = ["id", "display_name", "title", "active", "bookable_online", "sort_order", "services"]
        read_only_fields = ["id"]

    def create(self, data):
        services = data.pop("services", [])
        st = Staff.objects.create(**data)
        st.services.set(services)
        return st

    def update(self, inst, data):
        services = data.pop("services", None)
        inst = super().update(inst, data)
        if services is not None:
            inst.services.set(services)
        return inst


class ScheduleSerializer(serializers.ModelSerializer):
    staff = BusinessScopedPK(queryset=Staff.objects.all())

    class Meta:
        model = Schedule
        fields = ["id", "staff", "weekday", "start_time", "end_time"]

    def validate(self, d):
        if d["start_time"] >= d["end_time"]:
            raise serializers.ValidationError("start_time must be before end_time")
        return d


class WalkInSerializer(serializers.Serializer):
    service = BusinessScopedPK(queryset=Service.objects.all())
    staff = BusinessScopedPK(queryset=Staff.objects.all())
    starts_at = serializers.DateTimeField()
    client_name = serializers.CharField(max_length=150)
    client_phone = serializers.CharField(max_length=20, required=False, allow_blank=True)
    source = serializers.ChoiceField(choices=["walk_in", "phone"], default="walk_in")
    notes = serializers.CharField(required=False, allow_blank=True, max_length=500)
    allow_overlap = serializers.BooleanField(default=False)

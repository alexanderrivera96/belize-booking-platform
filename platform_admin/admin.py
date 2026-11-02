"""Model admin for the platform console. No delete permissions anywhere: records are suspended,
deactivated or status-changed instead. Customer phone numbers are masked in lists."""
from auditlog.models import LogEntry
from django.contrib import admin, messages
from django.contrib.auth.models import Group
from django.db.models import Count, Q
from django.utils import timezone
from django.utils.html import format_html

from accounts.models import MessagingConsent, OtpRequest, User
from booking.models import Appointment
from businesses.models import (BookingPolicy, Business, BusinessMember, Category, Schedule, Service, Staff,
                               StaffService, TimeOff, Town)
from clients.models import Customer
from notifications.models import MessageLog, Reminder

from .models import AdminActionLog, SiteSettings
from .site import admin_site


def mask(phone):
    s = str(phone or "")
    return s[:5] + "•••" + s[-2:] if len(s) > 7 else s


class NoDeleteAdmin(admin.ModelAdmin):
    def has_delete_permission(self, request, obj=None):
        return False


class ReadOnlyAdmin(NoDeleteAdmin):
    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False


# ---------------------------------------------------------------- businesses

class MemberInline(admin.TabularInline):
    model = BusinessMember
    fk_name = "business"
    extra = 0
    fields = ["user", "role", "status", "joined_at"]
    readonly_fields = ["joined_at"]
    raw_id_fields = ["user"]
    can_delete = False


class ServiceInline(admin.TabularInline):
    model = Service
    extra = 0
    fields = ["name", "duration_min", "price_cents", "buffer_before_min", "buffer_after_min", "active", "sort_order"]
    can_delete = False


class StaffInline(admin.TabularInline):
    model = Staff
    extra = 0
    fields = ["display_name", "title", "member", "active", "bookable_online"]
    raw_id_fields = ["member"]
    can_delete = False


class PolicyInline(admin.StackedInline):
    model = BookingPolicy
    can_delete = False
    fields = ["requires_approval", "approval_timeout_min", "min_notice_min", "max_advance_days", "slot_interval_min",
              "cancel_window_hours", "reminder_offsets_min", "allow_provider_overlap", "staff_can_view_all"]


@admin.register(Business, site=admin_site)
class BusinessAdmin(NoDeleteAdmin):
    list_display = ["name", "category", "town", "status_badge", "featured", "owner", "bookings", "created_at"]
    list_filter = ["status", "featured", "category", "town"]
    search_fields = ["name", "slug", "members__user__phone"]
    readonly_fields = ["id", "created_at", "updated_at", "public_link"]
    prepopulated_fields = {"slug": ["name"]}
    inlines = [PolicyInline, MemberInline, StaffInline, ServiceInline]
    actions = ["list_businesses", "suspend_businesses", "unlist_to_draft", "feature", "unfeature"]
    fieldsets = [
        (None, {"fields": ["name", "slug", "category", "town", "status", "featured", "public_link"]}),
        ("Profile", {"fields": ["tagline", "description", "address", "phone", "email", "timezone",
                                "latitude", "longitude"]}),
        ("Record", {"fields": ["id", "created_at", "updated_at"], "classes": ["collapse"]}),
    ]

    def get_queryset(self, request):
        return super().get_queryset(request).select_related("category", "town").annotate(
            n_bookings=Count("appointments", filter=~Q(appointments__status="held")))

    @admin.display(description="Status", ordering="status")
    def status_badge(self, obj):
        color = {"listed": "#2f7d4f", "draft": "#9a6a00", "suspended": "#b3362b"}[obj.status]
        return format_html('<strong style="color:{}">{}</strong>', color, obj.get_status_display())

    @admin.display(description="Owner")
    def owner(self, obj):
        m = obj.owner_membership
        return f"{m.user.full_name} ({mask(m.user.phone)})" if m else "—"

    @admin.display(description="Bookings", ordering="n_bookings")
    def bookings(self, obj):
        return obj.n_bookings

    @admin.display(description="Public page")
    def public_link(self, obj):
        return format_html('<a href="/b/{}/" target="_blank" rel="noopener">/b/{}/</a> (on the public site)',
                           obj.slug, obj.slug) if obj.pk else "—"

    def _set(self, request, qs, action, **fields):
        n = qs.update(**fields)
        for b in qs:
            AdminActionLog.record(request, action, f"{b.name} ({b.id})")
        self.message_user(request, f"{n} business(es) updated.", messages.SUCCESS)

    @admin.action(description="List publicly")
    def list_businesses(self, request, qs):
        self._set(request, qs, "list_business", status="listed")

    @admin.action(description="Suspend (hide and stop new bookings)")
    def suspend_businesses(self, request, qs):
        self._set(request, qs, "suspend_business", status="suspended")

    @admin.action(description="Move back to draft")
    def unlist_to_draft(self, request, qs):
        self._set(request, qs, "unlist_business", status="draft")

    @admin.action(description="Feature on home page")
    def feature(self, request, qs):
        self._set(request, qs, "feature_business", featured=True)

    @admin.action(description="Stop featuring")
    def unfeature(self, request, qs):
        self._set(request, qs, "unfeature_business", featured=False)

    def save_model(self, request, obj, form, change):
        super().save_model(request, obj, form, change)
        if not hasattr(obj, "booking_policy"):
            BookingPolicy.objects.get_or_create(business=obj)

    def save_formset(self, request, form, formset, change):
        instances = formset.save(commit=False)
        for inst in instances:
            if isinstance(inst, BusinessMember) and inst.status == "active" and not inst.joined_at:
                inst.joined_at = timezone.now()
            inst.save()
        formset.save_m2m()


@admin.register(Category, site=admin_site)
class CategoryAdmin(NoDeleteAdmin):
    list_display = ["name", "slug", "sort_order", "active", "n"]
    list_editable = ["sort_order", "active"]
    prepopulated_fields = {"slug": ["name"]}

    def get_queryset(self, request):
        return super().get_queryset(request).annotate(_n=Count("businesses"))

    @admin.display(description="Businesses")
    def n(self, obj):
        return obj._n


@admin.register(Town, site=admin_site)
class TownAdmin(NoDeleteAdmin):
    list_display = ["name", "district", "active"]
    list_editable = ["active"]
    list_filter = ["district"]
    prepopulated_fields = {"slug": ["name"]}


class StaffServiceInline(admin.TabularInline):
    model = StaffService
    extra = 0


class ScheduleInline(admin.TabularInline):
    model = Schedule
    extra = 0


@admin.register(Staff, site=admin_site)
class StaffAdmin(NoDeleteAdmin):
    list_display = ["display_name", "business", "title", "active", "bookable_online", "member"]
    list_filter = ["active", "bookable_online"]
    search_fields = ["display_name", "business__name"]
    raw_id_fields = ["business", "member"]
    inlines = [StaffServiceInline, ScheduleInline]


@admin.register(Service, site=admin_site)
class ServiceAdmin(NoDeleteAdmin):
    list_display = ["name", "business", "duration_min", "price", "active"]
    list_filter = ["active"]
    search_fields = ["name", "business__name"]
    raw_id_fields = ["business"]

    @admin.display(description="Price", ordering="price_cents")
    def price(self, obj):
        return f"BZ${obj.price_cents / 100:,.2f}"


@admin.register(BusinessMember, site=admin_site)
class BusinessMemberAdmin(NoDeleteAdmin):
    list_display = ["user", "business", "role", "status", "joined_at"]
    list_filter = ["role", "status"]
    search_fields = ["user__phone", "user__full_name", "business__name"]
    raw_id_fields = ["user", "business", "invited_by"]


@admin.register(TimeOff, site=admin_site)
class TimeOffAdmin(NoDeleteAdmin):
    list_display = ["staff", "starts_at", "ends_at"]
    raw_id_fields = ["staff"]


# ---------------------------------------------------------------- people

@admin.register(User, site=admin_site)
class UserAdmin(NoDeleteAdmin):
    list_display = ["full_name", "masked_phone", "email", "is_staff", "is_active", "date_joined"]
    list_filter = ["is_staff", "is_active", "preferred_channel"]
    search_fields = ["full_name", "phone", "email"]
    readonly_fields = ["id", "date_joined", "last_login", "phone_verified_at", "email_verified_at", "session_epoch"]
    fieldsets = [
        (None, {"fields": ["full_name", "phone", "email", "pending_email", "preferred_channel", "timezone"]}),
        ("Access", {"fields": ["is_active", "is_staff", "is_superuser", "groups"]}),
        ("Record", {"fields": ["id", "date_joined", "last_login", "phone_verified_at", "email_verified_at",
                               "session_epoch"]}),
    ]
    filter_horizontal = ["groups"]
    actions = ["sign_out_everywhere"]

    @admin.display(description="Phone", ordering="phone")
    def masked_phone(self, obj):
        return mask(obj.phone)

    def get_readonly_fields(self, request, obj=None):
        ro = list(super().get_readonly_fields(request, obj))
        if not request.user.is_superuser:
            ro += ["is_staff", "is_superuser", "groups"]
        return ro

    def save_model(self, request, obj, form, change):
        if change and {"is_staff", "is_superuser", "groups"} & set(form.changed_data):
            AdminActionLog.record(request, "change_admin_rights", obj, ",".join(form.changed_data))
        super().save_model(request, obj, form, change)

    @admin.action(description="Sign out of all devices")
    def sign_out_everywhere(self, request, qs):
        for u in qs:
            u.sign_out_everywhere()
            AdminActionLog.record(request, "sign_out_everywhere", u)
        self.message_user(request, f"Signed out {qs.count()} user(s) everywhere.")


@admin.register(Customer, site=admin_site)
class CustomerAdmin(ReadOnlyAdmin):
    list_display = ["name", "business", "masked_phone", "source", "linked", "created_at"]
    list_filter = ["source"]
    search_fields = ["name", "business__name"]
    exclude = ["notes"]          # private to the business

    @admin.display(description="Phone")
    def masked_phone(self, obj):
        return mask(obj.phone)

    @admin.display(boolean=True, description="Verified account")
    def linked(self, obj):
        return obj.user_id is not None


# ---------------------------------------------------------------- bookings and messages

@admin.register(Appointment, site=admin_site)
class AppointmentAdmin(ReadOnlyAdmin):
    list_display = ["starts_at", "business", "service", "staff", "customer", "status", "source"]
    list_filter = ["status", "source", "business"]
    search_fields = ["business__name", "customer__name"]
    date_hierarchy = "starts_at"
    exclude = ["blocked_range", "checkout_token", "idempotency_key"]


@admin.register(Reminder, site=admin_site)
class ReminderAdmin(ReadOnlyAdmin):
    list_display = ["send_at", "appointment", "kind", "offset_min", "status", "attempts", "channel_used"]
    list_filter = ["status", "kind"]


@admin.register(MessageLog, site=admin_site)
class MessageLogAdmin(ReadOnlyAdmin):
    list_display = ["created_at", "business", "audience", "template", "channel", "masked_to", "status"]
    list_filter = ["audience", "template", "channel", "status"]
    exclude = ["body"]           # bodies contain manage-booking links

    @admin.display(description="To")
    def masked_to(self, obj):
        return mask(obj.to)


@admin.register(OtpRequest, site=admin_site)
class OtpRequestAdmin(ReadOnlyAdmin):
    list_display = ["created_at", "channel", "purpose", "status", "masked_target", "ip"]
    list_filter = ["channel", "purpose", "status"]

    @admin.display(description="To")
    def masked_target(self, obj):
        return mask(obj.phone) if obj.phone else (obj.email or "")[:3] + "•••"


@admin.register(MessagingConsent, site=admin_site)
class ConsentAdmin(ReadOnlyAdmin):
    list_display = ["user", "channel", "consented_at", "revoked_at", "source"]


# ---------------------------------------------------------------- platform

@admin.register(SiteSettings, site=admin_site)
class SiteSettingsAdmin(NoDeleteAdmin):
    fieldsets = [
        ("Home page", {"fields": ["home_headline", "home_subheadline"]}),
        ("Announcement banner", {"fields": ["announcement", "announcement_level"]}),
        ("Support contacts", {"fields": ["support_phone", "support_email"]}),
        ("Emergency", {"fields": ["new_bookings_paused"]}),
    ]

    def has_add_permission(self, request):
        return not SiteSettings.objects.exists()

    def changelist_view(self, request, extra_context=None):
        from django.shortcuts import redirect
        obj = SiteSettings.load()
        return redirect("admin:platform_admin_sitesettings_change", obj.pk)

    def save_model(self, request, obj, form, change):
        super().save_model(request, obj, form, change)
        AdminActionLog.record(request, "site_settings", ",".join(form.changed_data))


@admin.register(AdminActionLog, site=admin_site)
class AdminActionLogAdmin(ReadOnlyAdmin):
    list_display = ["at", "admin", "action", "target", "reason"]
    list_filter = ["action"]
    search_fields = ["target", "reason"]


@admin.register(LogEntry, site=admin_site)
class AuditLogAdmin(ReadOnlyAdmin):
    list_display = ["timestamp", "actor", "content_type", "object_repr", "action"]
    list_filter = ["action", "content_type"]
    search_fields = ["object_repr"]


admin_site.register(Group, NoDeleteAdmin)

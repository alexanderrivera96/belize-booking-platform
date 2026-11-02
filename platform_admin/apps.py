from django.apps import AppConfig


class PlatformAdminConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "platform_admin"
    verbose_name = "Platform"

    def ready(self):
        from auditlog.registry import auditlog
        from django.apps import apps

        for label in ["accounts.User", "businesses.Business", "businesses.BusinessMember", "businesses.Staff",
                      "businesses.Service", "businesses.BookingPolicy", "businesses.Schedule", "businesses.TimeOff",
                      "clients.Customer", "booking.Appointment", "platform_admin.SiteSettings"]:
            model = apps.get_model(label)
            if not auditlog.contains(model):
                auditlog.register(model, exclude_fields=["blocked_range", "checkout_token", "password",
                                                         "session_epoch", "last_login"])

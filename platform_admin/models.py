from django.conf import settings
from django.core.cache import cache
from django.db import models


class SiteSettings(models.Model):
    """Site-wide adjustments the admin team can make without a deploy (singleton)."""
    home_headline = models.CharField(max_length=160, blank=True,
                                     help_text="Overrides the home page headline. Leave blank for the default.")
    home_subheadline = models.CharField(max_length=300, blank=True)
    announcement = models.CharField(max_length=300, blank=True,
                                    help_text="Banner shown at the top of every customer page. Leave blank to hide.")
    announcement_level = models.CharField(max_length=10, default="info",
                                          choices=[("info", "Info"), ("warning", "Warning")])
    support_phone = models.CharField(max_length=30, blank=True)
    support_email = models.EmailField(blank=True)
    new_bookings_paused = models.BooleanField(default=False,
                                              help_text="Emergency switch: stop all new online bookings platform-wide.")
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = verbose_name_plural = "site settings"

    def __str__(self):
        return "Site settings"

    def save(self, *args, **kwargs):
        self.pk = 1
        super().save(*args, **kwargs)
        cache.delete("site_settings")

    @classmethod
    def load(cls):
        obj = cache.get("site_settings")
        if obj is None:
            obj, _ = cls.objects.get_or_create(pk=1)
            cache.set("site_settings", obj, 60)
        return obj


class AdminActionLog(models.Model):
    """Admin actions that aren't plain edits (append-only)."""
    admin = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")
    action = models.CharField(max_length=60)
    target = models.CharField(max_length=200)
    reason = models.CharField(max_length=300, blank=True)
    at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-at"]

    def __str__(self):
        return f"{self.at:%Y-%m-%d %H:%M} {self.admin} {self.action} {self.target}"

    @classmethod
    def record(cls, request, action, target, reason=""):
        return cls.objects.create(admin=request.user if request.user.is_authenticated else None, action=action,
                                  target=str(target)[:200], reason=reason[:300])

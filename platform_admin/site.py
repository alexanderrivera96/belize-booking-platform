"""Platform admin console: Django admin on its own host with mandatory MFA (TOTP + backup codes).

- Password + authenticator code to sign in; phone/email codes are never enough.
- First sign-in walks the admin through enrolling an authenticator app (when allowed).
- Every page requires an OTP-verified session; sessions end after 30 min idle / 8 h.
"""
import base64
import io

import qrcode
import qrcode.image.svg
from django.conf import settings
from django.contrib import admin, messages
from django.contrib.auth import login as auth_login, logout as auth_logout
from django.db import transaction
from django.shortcuts import redirect, render
from django.urls import path, reverse
from django.utils import timezone
from django.utils.decorators import method_decorator
from django.views.decorators.cache import never_cache
from django_otp import login as otp_login, user_has_device
from django_otp.plugins.otp_static.models import StaticDevice, StaticToken
from django_otp.plugins.otp_totp.models import TOTPDevice

from .forms import AdminLoginForm, MfaSetupForm


class PlatformAdminSite(admin.AdminSite):
    site_header = f"{settings.PLATFORM_NAME} admin"
    site_title = f"{settings.PLATFORM_NAME} admin"
    index_title = "Platform console"
    index_template = "platform_admin/index.html"
    enable_nav_sidebar = True

    def has_permission(self, request):
        u = request.user
        return u.is_active and u.is_staff and getattr(u, "is_verified", lambda: False)()

    def get_urls(self):
        from . import views
        extra = [
            path("mfa/setup/", self.mfa_setup, name="mfa_setup"),
            path("onboard/", self.admin_view(views.onboard_provider), name="onboard_provider"),
        ]
        return extra + super().get_urls()

    def index(self, request, extra_context=None):
        from .views import dashboard_context
        extra_context = {**(extra_context or {}), **dashboard_context()}
        return super().index(request, extra_context)

    @method_decorator(never_cache)
    def login(self, request, extra_context=None):
        if request.method == "GET" and self.has_permission(request):
            return redirect(reverse("admin:index", current_app=self.name))
        form = AdminLoginForm(request, data=request.POST or None)
        if request.method == "POST" and form.is_valid():
            user = form.user
            auth_login(request, user, backend="django.contrib.auth.backends.ModelBackend")
            request.session["admin_login_at"] = timezone.now().timestamp()
            if form.device is not None:
                otp_login(request, form.device)
                from .models import AdminActionLog
                AdminActionLog.record(request, "admin_login", user)
                return redirect(request.GET.get("next") or reverse("admin:index", current_app=self.name))
            return redirect(reverse("admin:mfa_setup", current_app=self.name))
        return render(request, "platform_admin/login.html", {**self.each_context(request), "form": form,
                                                             "title": "Sign in"})

    def logout(self, request, extra_context=None):
        auth_logout(request)
        return redirect(reverse("admin:login", current_app=self.name))

    def mfa_setup(self, request):
        """Enrol an authenticator app. Only for a signed-in admin with no confirmed device."""
        u = request.user
        if not (u.is_authenticated and u.is_active and u.is_staff):
            return redirect(reverse("admin:login", current_app=self.name))
        if user_has_device(u, confirmed=True):
            return redirect(reverse("admin:index", current_app=self.name))
        if not settings.ADMIN_ALLOW_SELF_ENROLL:
            auth_logout(request)
            messages.error(request, "Ask a superuser to enrol your authenticator (manage.py enroll_admin_mfa).")
            return redirect(reverse("admin:login", current_app=self.name))
        device, _ = TOTPDevice.objects.get_or_create(user=u, confirmed=False, defaults={"name": "Authenticator app"})
        form = MfaSetupForm(request.POST or None)
        backup_codes = None
        if request.method == "POST" and form.is_valid():
            if device.verify_token(form.cleaned_data["token"]):
                with transaction.atomic():
                    device.confirmed = True
                    device.save()
                    static, _ = StaticDevice.objects.get_or_create(user=u, name="Backup codes")
                    static.token_set.all().delete()
                    backup_codes = [StaticToken.random_token() for _ in range(8)]
                    for t in backup_codes:
                        static.token_set.create(token=t)
                otp_login(request, device)
                from .models import AdminActionLog
                AdminActionLog.record(request, "mfa_enrolled", u)
            else:
                form.add_error("token", "That code didn't match. Check your phone's clock and try again.")
        img = qrcode.make(device.config_url, image_factory=qrcode.image.svg.SvgPathImage, box_size=8)
        buf = io.BytesIO()
        img.save(buf)
        qr = "data:image/svg+xml;base64," + base64.b64encode(buf.getvalue()).decode()
        return render(request, "platform_admin/mfa_setup.html", {
            **self.each_context(request), "form": form, "qr": qr, "backup_codes": backup_codes,
            "secret": base64.b32encode(device.bin_key).decode(), "title": "Set up two-step sign-in"})


admin_site = PlatformAdminSite(name="admin")

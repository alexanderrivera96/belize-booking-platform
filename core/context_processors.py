from django.conf import settings


def site(request):
    from platform_admin.models import SiteSettings

    try:
        site_settings = SiteSettings.load()
    except Exception:          # database not migrated yet
        site_settings = None
    return {
        "SITE": site_settings,
        "PLATFORM_NAME": settings.PLATFORM_NAME,
        "PAYMENTS_ENABLED": settings.PAYMENTS_ENABLED,
        "OTP_BACKEND": settings.OTP_BACKEND,
        "DEBUG": settings.DEBUG,
    }

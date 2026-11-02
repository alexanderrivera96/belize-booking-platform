from django.conf import settings


def site(request):
    return {
        "PLATFORM_NAME": settings.PLATFORM_NAME,
        "PAYMENTS_ENABLED": settings.PAYMENTS_ENABLED,
        "OTP_BACKEND": settings.OTP_BACKEND,
        "DEBUG": settings.DEBUG,
    }

"""Security headers on every response (security plan: web application hardening)."""
from django.conf import settings

CSP = (
    "default-src 'self'; "
    "script-src 'self'; "
    "style-src 'self' 'unsafe-inline'; "
    "img-src 'self' data:; "
    "font-src 'self'; "
    "connect-src 'self'; "
    "object-src 'none'; "
    "base-uri 'none'; "
    "frame-ancestors 'none'; "
    "form-action 'self'"
)

TOKEN_PATH_PREFIXES = ("/m/", "/invite/", "/feeds/")


class SecurityHeadersMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        response.headers.setdefault("Content-Security-Policy", CSP)
        response.headers.setdefault("Permissions-Policy", "camera=(), microphone=(), payment=(), geolocation=(self)")
        if request.path.startswith(TOKEN_PATH_PREFIXES):
            response.headers["Referrer-Policy"] = "no-referrer"
            response.headers["Cache-Control"] = "no-store"
        elif getattr(request, "user", None) is not None and request.user.is_authenticated:
            response.headers.setdefault("Cache-Control", "no-store")
        if settings.SITE_ROLE == "admin":
            response.headers["X-Robots-Tag"] = "noindex, nofollow"
        return response

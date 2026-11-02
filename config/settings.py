"""Settings for the Belize booking platform.

One codebase, one Docker image, four services (web, admin, worker, beat).
`SITE_ROLE` decides whether a web process serves the public site or the admin console.
"""
import os
from pathlib import Path

import dj_database_url

BASE_DIR = Path(__file__).resolve().parent.parent


def env(name, default=None):
    return os.environ.get(name, default)


def env_bool(name, default=False):
    return env(name, "1" if default else "0").lower() in ("1", "true", "yes", "on")


def env_list(name, default=""):
    return [x.strip() for x in env(name, default).split(",") if x.strip()]


DEBUG = env_bool("DJANGO_DEBUG", False)
SECRET_KEY = env("DJANGO_SECRET_KEY") or ("dev-insecure-key-do-not-use-in-production" if DEBUG else None)
if not SECRET_KEY:
    raise RuntimeError("DJANGO_SECRET_KEY must be set when DEBUG is off")
SECRET_KEY_FALLBACKS = env_list("DJANGO_SECRET_KEY_FALLBACKS")

ALLOWED_HOSTS = env_list("DJANGO_ALLOWED_HOSTS", "localhost,127.0.0.1,testserver")
CSRF_TRUSTED_ORIGINS = env_list("DJANGO_CSRF_TRUSTED_ORIGINS")

SITE_ROLE = env("SITE_ROLE", "public")          # "public" | "admin"
if SITE_ROLE not in ("public", "admin"):
    raise RuntimeError("SITE_ROLE must be 'public' or 'admin'")
ROOT_URLCONF = "config.urls_admin" if SITE_ROLE == "admin" else "config.urls_public"

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "django.contrib.postgres",
    "rest_framework",
    "phonenumber_field",
    "django_otp",
    "django_otp.plugins.otp_totp",
    "django_otp.plugins.otp_static",
    "auditlog",
    "core",
    "accounts",
    "businesses",
    "clients",
    "booking",
    "notifications",
    "platform_admin",
    "customer_site",
    "provider_site",
    "api",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "core.middleware.SecurityHeadersMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django_otp.middleware.OTPMiddleware",
    "core.sessions.SessionPolicyMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "auditlog.middleware.AuditlogMiddleware",
]

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "core.context_processors.site",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"

DATABASES = {
    "default": dj_database_url.parse(
        env("DATABASE_URL", "postgres://booking:booking@localhost:5432/booking"),
        conn_max_age=60,
    )
}

AUTH_USER_MODEL = "accounts.User"                 # set before the first migrate — never change
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

PASSWORD_HASHERS = [
    "django.contrib.auth.hashers.Argon2PasswordHasher",
    "django.contrib.auth.hashers.PBKDF2PasswordHasher",
]
try:  # Argon2 needs argon2-cffi; fall back gracefully in minimal dev setups
    import argon2  # noqa: F401
except ImportError:
    PASSWORD_HASHERS = PASSWORD_HASHERS[1:]

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator", "OPTIONS": {"min_length": 15}},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
]

LANGUAGE_CODE = "en-us"
TIME_ZONE = "America/Belize"
USE_I18N = True
USE_TZ = True

STATIC_URL = "/static/"
STATICFILES_DIRS = [BASE_DIR / "static"]
STATIC_ROOT = BASE_DIR / "staticfiles"

PHONENUMBER_DEFAULT_REGION = "BZ"
PHONENUMBER_DEFAULT_FORMAT = "E164"

# ---- Sessions (accounts design §1.6) ----
SESSION_ENGINE = "django.contrib.sessions.backends.db"
SESSION_COOKIE_NAME = "bk_admin" if SITE_ROLE == "admin" else "bk_session"
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Lax"
SESSION_EXPIRE_AT_BROWSER_CLOSE = False
CSRF_COOKIE_NAME = "bk_admin_csrf" if SITE_ROLE == "admin" else "bk_csrf"

CUSTOMER_SESSION_DAYS = 180
CUSTOMER_SESSION_ABSOLUTE_DAYS = 365
BUSINESS_REVERIFY_DAYS = 30
STEP_UP_MINUTES = 15
CHECKOUT_EMAIL_REQUIRED = False
ADMIN_IDLE_MINUTES = 30
ADMIN_ABSOLUTE_HOURS = 8

LOGIN_URL = "accounts:login"

# ---- Security (security plan: web hardening) ----
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_REFERRER_POLICY = "strict-origin-when-cross-origin"
SECURE_CROSS_ORIGIN_OPENER_POLICY = "same-origin"
X_FRAME_OPTIONS = "DENY"
DATA_UPLOAD_MAX_MEMORY_SIZE = 2_621_440
if not DEBUG:
    SECURE_SSL_REDIRECT = env_bool("DJANGO_SSL_REDIRECT", True)
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
    SECURE_HSTS_SECONDS = int(env("DJANGO_HSTS_SECONDS", "86400"))
    SECURE_HSTS_INCLUDE_SUBDOMAINS = True
    SECURE_HSTS_PRELOAD = env_bool("DJANGO_HSTS_PRELOAD", False)
    if not SECURE_HSTS_PRELOAD:
        # Security plan: start HSTS at 1 day, raise it, then opt in to preload once every subdomain is HTTPS.
        SILENCED_SYSTEM_CHECKS = ["security.W021"]
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True

# ---- Platform behaviour ----
PAYMENTS_ENABLED = env_bool("PAYMENTS_ENABLED", False)
PUBLIC_BASE_URL = env("PUBLIC_BASE_URL", "http://localhost:8000")
PLATFORM_NAME = env("PLATFORM_NAME", "BookBZ")
OTP_BACKEND = env("OTP_BACKEND", "console")
MESSAGING_BACKEND = env("MESSAGING_BACKEND", "console")
TWILIO_ACCOUNT_SID = env("TWILIO_ACCOUNT_SID", "")
TWILIO_AUTH_TOKEN = env("TWILIO_AUTH_TOKEN", "")
TWILIO_VERIFY_SERVICE_SID = env("TWILIO_VERIFY_SERVICE_SID", "")
TWILIO_SMS_FROM = env("TWILIO_SMS_FROM", "")
TWILIO_WHATSAPP_FROM = env("TWILIO_WHATSAPP_FROM", "")
OTP_ALLOWED_COUNTRY_CODES = [501]                 # SEC2: Belize only at launch

# ---- Django REST Framework: deny by default ----
REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": ["rest_framework.authentication.SessionAuthentication"],
    "DEFAULT_PERMISSION_CLASSES": ["rest_framework.permissions.IsAuthenticated"],
    "DEFAULT_RENDERER_CLASSES": ["rest_framework.renderers.JSONRenderer"],
    "DEFAULT_THROTTLE_RATES": {
        "otp_start": "10/hour",
        "otp_verify": "20/hour",
        "holds": "20/hour",
        "public": "60/minute",
    },
    "EXCEPTION_HANDLER": "core.exceptions.fail_closed_handler",
}

# ---- Celery ----
CELERY_BROKER_URL = env("REDIS_URL", "redis://localhost:6379/0")
CELERY_RESULT_BACKEND = None
CELERY_ACCEPT_CONTENT = ["json"]
CELERY_TASK_SERIALIZER = "json"
CELERY_TIMEZONE = "America/Belize"
CELERY_TASK_ALWAYS_EAGER = env_bool("CELERY_TASK_ALWAYS_EAGER", False)

CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.redis.RedisCache" if env("REDIS_URL") else
                   "django.core.cache.backends.locmem.LocMemCache",
        "LOCATION": env("REDIS_URL", "bk-local"),
    }
}

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {"plain": {"format": "%(asctime)s %(levelname)s %(name)s: %(message)s"}},
    "handlers": {"console": {"class": "logging.StreamHandler", "formatter": "plain"}},
    "root": {"handlers": ["console"], "level": "INFO"},
}

# Admin console MFA enrolment: allow an admin with no authenticator to enrol on first password sign-in.
# Turn off in production once every admin is enrolled; then use `manage.py enroll_admin_mfa`.
ADMIN_ALLOW_SELF_ENROLL = env_bool("ADMIN_ALLOW_SELF_ENROLL", True)
OTP_TOTP_ISSUER = PLATFORM_NAME

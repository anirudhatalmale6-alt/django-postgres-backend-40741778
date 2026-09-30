"""
Django settings — driven entirely by environment variables.

Nothing environment-specific is hardcoded: every value that differs between
local / staging / production is read from the environment (see .env.example).
The only defaults baked in are safe-for-development ones, and DEBUG defaults
to False so a missing .env can never accidentally expose a production box.
"""

from datetime import timedelta
from pathlib import Path

from decouple import Csv, config

BASE_DIR = Path(__file__).resolve().parent.parent


# --------------------------------------------------------------------------
# Core
# --------------------------------------------------------------------------

SECRET_KEY = config("DJANGO_SECRET_KEY", default="dev-only-insecure-key-change-me")
DEBUG = config("DJANGO_DEBUG", default=False, cast=bool)
ALLOWED_HOSTS = config("DJANGO_ALLOWED_HOSTS", default="localhost,127.0.0.1", cast=Csv())

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    # Third party
    "rest_framework",
    "rest_framework_simplejwt",
    "rest_framework_simplejwt.token_blacklist",
    "drf_spectacular",
    "corsheaders",
    "django_filters",
    # Local
    "core",
    "accounts",
    "integrations",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "corsheaders.middleware.CorsMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "core.middleware.RequestIDMiddleware",
]

ROOT_URLCONF = "config.urls"

# Movable admin path: /admin/ is the first thing credential-stuffing bots try.
ADMIN_URL = config("DJANGO_ADMIN_URL", default="admin/")

WSGI_APPLICATION = "config.wsgi.application"
ASGI_APPLICATION = "config.asgi.application"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]


# --------------------------------------------------------------------------
# Database — PostgreSQL
# --------------------------------------------------------------------------
# CONN_MAX_AGE keeps connections alive between requests, which is what makes a
# 30-50 concurrent-user workload cheap: without it every request pays a fresh
# TCP + auth round trip to Postgres.

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": config("POSTGRES_DB", default="app_db"),
        "USER": config("POSTGRES_USER", default="app_user"),
        "PASSWORD": config("POSTGRES_PASSWORD", default=""),
        "HOST": config("POSTGRES_HOST", default="127.0.0.1"),
        "PORT": config("POSTGRES_PORT", default="5432"),
        "CONN_MAX_AGE": config("POSTGRES_CONN_MAX_AGE", default=60, cast=int),
        "CONN_HEALTH_CHECKS": True,
        "OPTIONS": {
            "connect_timeout": 5,
        },
    }
}

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"


# --------------------------------------------------------------------------
# Auth
# --------------------------------------------------------------------------

AUTH_USER_MODEL = "accounts.User"

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {
        "NAME": "django.contrib.auth.password_validation.MinimumLengthValidator",
        "OPTIONS": {"min_length": config("PASSWORD_MIN_LENGTH", default=10, cast=int)},
    },
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

# Argon2 first: stronger than the PBKDF2 default and the hasher Django itself
# recommends. The remaining entries let existing PBKDF2 hashes keep verifying
# and transparently upgrade on next login.
PASSWORD_HASHERS = [
    "django.contrib.auth.hashers.Argon2PasswordHasher",
    "django.contrib.auth.hashers.PBKDF2PasswordHasher",
    "django.contrib.auth.hashers.PBKDF2SHA1PasswordHasher",
    "django.contrib.auth.hashers.ScryptPasswordHasher",
]

PASSWORD_RESET_TIMEOUT = config("PASSWORD_RESET_TIMEOUT", default=3600, cast=int)


# --------------------------------------------------------------------------
# DRF + JWT
# --------------------------------------------------------------------------

REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": (
        "rest_framework_simplejwt.authentication.JWTAuthentication",
    ),
    "DEFAULT_PERMISSION_CLASSES": ("rest_framework.permissions.IsAuthenticated",),
    "DEFAULT_PAGINATION_CLASS": "core.pagination.DefaultPagination",
    "PAGE_SIZE": config("API_PAGE_SIZE", default=25, cast=int),
    "DEFAULT_FILTER_BACKENDS": (
        "django_filters.rest_framework.DjangoFilterBackend",
        "rest_framework.filters.SearchFilter",
        "rest_framework.filters.OrderingFilter",
    ),
    "DEFAULT_SCHEMA_CLASS": "drf_spectacular.openapi.AutoSchema",
    "DEFAULT_THROTTLE_CLASSES": (
        "rest_framework.throttling.ScopedRateThrottle",
    ),
    "DEFAULT_THROTTLE_RATES": {
        "auth": config("THROTTLE_AUTH", default="10/min"),
        "password_reset": config("THROTTLE_PASSWORD_RESET", default="5/hour"),
        "register": config("THROTTLE_REGISTER", default="20/hour"),
    },
    "EXCEPTION_HANDLER": "core.exceptions.api_exception_handler",
    "TEST_REQUEST_DEFAULT_FORMAT": "json",
}

SIMPLE_JWT = {
    "ACCESS_TOKEN_LIFETIME": timedelta(
        minutes=config("JWT_ACCESS_MINUTES", default=15, cast=int)
    ),
    "REFRESH_TOKEN_LIFETIME": timedelta(
        days=config("JWT_REFRESH_DAYS", default=7, cast=int)
    ),
    # Rotate + blacklist means a stolen refresh token is single-use and a
    # logout genuinely invalidates the session server-side.
    "ROTATE_REFRESH_TOKENS": True,
    "BLACKLIST_AFTER_ROTATION": True,
    "UPDATE_LAST_LOGIN": True,
    "ALGORITHM": "HS256",
    "SIGNING_KEY": config("JWT_SIGNING_KEY", default=SECRET_KEY),
    "AUTH_HEADER_TYPES": ("Bearer",),
    "USER_ID_FIELD": "id",
    "USER_ID_CLAIM": "user_id",
}

SPECTACULAR_SETTINGS = {
    "TITLE": config("API_TITLE", default="Application API"),
    "DESCRIPTION": (
        "Backend API. Authenticate at `/api/v1/auth/login/`, then send "
        "`Authorization: Bearer <access token>` on subsequent requests."
    ),
    "VERSION": config("API_VERSION", default="1.0.0"),
    "SERVE_INCLUDE_SCHEMA": False,
    "COMPONENT_SPLIT_REQUEST": True,
    "SORT_OPERATIONS": False,
    "SCHEMA_PATH_PREFIX": r"/api/v[0-9]+",
    "SWAGGER_UI_SETTINGS": {
        "persistAuthorization": True,
        "displayRequestDuration": True,
    },
}


# --------------------------------------------------------------------------
# CORS — the front-end team's origins, explicitly listed (never a wildcard
# with credentials, which browsers reject anyway and which would be unsafe).
# --------------------------------------------------------------------------

CORS_ALLOWED_ORIGINS = config(
    "CORS_ALLOWED_ORIGINS",
    default="http://localhost:3000,http://127.0.0.1:3000",
    cast=Csv(),
)
CORS_ALLOW_CREDENTIALS = True
CSRF_TRUSTED_ORIGINS = config("CSRF_TRUSTED_ORIGINS", default="", cast=Csv())


# --------------------------------------------------------------------------
# Security headers / cookies
# --------------------------------------------------------------------------
# These all key off DJANGO_DEBUG so local http:// development still works while
# a deployed instance gets the hardened settings by default.

_prod = not DEBUG

SECURE_SSL_REDIRECT = config("SECURE_SSL_REDIRECT", default=_prod, cast=bool)
# Exempt the probes: a load balancer or container healthcheck hits them over
# plain HTTP inside the network and would otherwise get a 301 and mark the
# instance unhealthy.
SECURE_REDIRECT_EXEMPT = [r"^health/"]
SESSION_COOKIE_SECURE = config("SESSION_COOKIE_SECURE", default=_prod, cast=bool)
CSRF_COOKIE_SECURE = config("CSRF_COOKIE_SECURE", default=_prod, cast=bool)
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Lax"
CSRF_COOKIE_SAMESITE = "Lax"
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_REFERRER_POLICY = "same-origin"
X_FRAME_OPTIONS = "DENY"
SECURE_HSTS_SECONDS = config("SECURE_HSTS_SECONDS", default=31536000 if _prod else 0, cast=int)
SECURE_HSTS_INCLUDE_SUBDOMAINS = config("SECURE_HSTS_INCLUDE_SUBDOMAINS", default=_prod, cast=bool)
SECURE_HSTS_PRELOAD = config("SECURE_HSTS_PRELOAD", default=False, cast=bool)

# Behind a reverse proxy / load balancer terminating TLS.
if config("USE_X_FORWARDED_PROTO", default=_prod, cast=bool):
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")


# --------------------------------------------------------------------------
# Email — password reset delivery
# --------------------------------------------------------------------------
# Defaults to the console backend: reset links are PRINTED TO THE LOG and no
# mail leaves the machine until real SMTP credentials are supplied.

EMAIL_BACKEND = config(
    "EMAIL_BACKEND", default="django.core.mail.backends.console.EmailBackend"
)
EMAIL_HOST = config("EMAIL_HOST", default="")
EMAIL_PORT = config("EMAIL_PORT", default=587, cast=int)
EMAIL_HOST_USER = config("EMAIL_HOST_USER", default="")
EMAIL_HOST_PASSWORD = config("EMAIL_HOST_PASSWORD", default="")
EMAIL_USE_TLS = config("EMAIL_USE_TLS", default=True, cast=bool)
DEFAULT_FROM_EMAIL = config("DEFAULT_FROM_EMAIL", default="no-reply@example.com")

# Where the password-reset link should point. This is a FRONT-END route: the
# API mails a URL the SPA owns, the SPA collects the new password and POSTs it
# back to /api/v1/auth/password-reset/confirm/.
FRONTEND_PASSWORD_RESET_URL = config(
    "FRONTEND_PASSWORD_RESET_URL", default="http://localhost:3000/reset-password"
)


# --------------------------------------------------------------------------
# External integrations
# --------------------------------------------------------------------------

INTEGRATIONS = {
    "EXCHANGE_RATES": {
        "BASE_URL": config(
            "EXCHANGE_RATES_BASE_URL", default="https://open.er-api.com/v6"
        ),
        "API_KEY": config("EXCHANGE_RATES_API_KEY", default=""),
        "TIMEOUT": config("EXCHANGE_RATES_TIMEOUT", default=10, cast=int),
    },
}

HTTP_CLIENT_MAX_RETRIES = config("HTTP_CLIENT_MAX_RETRIES", default=2, cast=int)
HTTP_CLIENT_BACKOFF = config("HTTP_CLIENT_BACKOFF", default=0.5, cast=float)


# --------------------------------------------------------------------------
# Cache — locmem by default; point CACHE_URL at Redis in phase two and the
# integration layer's response caching starts working with no code change.
# --------------------------------------------------------------------------

_redis_url = config("REDIS_URL", default="")
if _redis_url:
    CACHES = {
        "default": {
            "BACKEND": "django.core.cache.backends.redis.RedisCache",
            "LOCATION": _redis_url,
        }
    }
else:
    CACHES = {
        "default": {
            "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
            "LOCATION": "app-default",
        }
    }


# --------------------------------------------------------------------------
# i18n / static
# --------------------------------------------------------------------------

LANGUAGE_CODE = config("LANGUAGE_CODE", default="en-us")
TIME_ZONE = config("TIME_ZONE", default="UTC")
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {
        "BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage"
    },
}
MEDIA_URL = "media/"
MEDIA_ROOT = BASE_DIR / "media"


# --------------------------------------------------------------------------
# Logging — structured-ish console output, request id attached where present.
# --------------------------------------------------------------------------

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "filters": {
        "request_id": {"()": "core.logging_filters.RequestIDFilter"},
    },
    "formatters": {
        "standard": {
            "format": "%(asctime)s %(levelname)-8s [%(request_id)s] %(name)s: %(message)s",
        },
    },
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
            "filters": ["request_id"],
            "formatter": "standard",
        },
    },
    "root": {
        "handlers": ["console"],
        "level": config("LOG_LEVEL", default="INFO"),
    },
    "loggers": {
        "django.db.backends": {
            "handlers": ["console"],
            "level": config("SQL_LOG_LEVEL", default="WARNING"),
            "propagate": False,
        },
    },
}

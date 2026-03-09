from __future__ import annotations

import os
from datetime import timedelta
from pathlib import Path

from dotenv import load_dotenv


BASE_DIR = Path(__file__).resolve().parent.parent
# Load local .env only as a fallback for development.
# Platform environment variables remain authoritative because override=False.
load_dotenv(BASE_DIR / ".env", override=False)


def _read_env_int(name: str, default: int = 0, minimum: int | None = None) -> int:
    raw_value = (os.environ.get(name, "") or "").strip()
    if not raw_value:
        value = default
    else:
        try:
            value = int(raw_value)
        except ValueError:
            value = default
    if minimum is not None:
        value = max(minimum, value)
    return value


def _read_env_bool(name: str, default: bool = False) -> bool:
    raw_value = (os.environ.get(name, "") or "").strip().lower()
    if not raw_value:
        return default
    return raw_value in {"1", "true", "yes", "on"}


def _read_env_csv(name: str) -> tuple[str, ...]:
    raw = (os.environ.get(name, "") or "").strip()
    if not raw:
        return tuple()
    return tuple({item.strip().lower() for item in raw.split(",") if item.strip()})


def _normalize_database_url(raw_url: str) -> str:
    value = (raw_url or "").strip()
    if not value:
        return ""
    # Normalize Postgres URLs to SQLAlchemy psycopg v3 dialect.
    if value.startswith("postgres://"):
        return value.replace("postgres://", "postgresql+psycopg://", 1)
    if value.startswith("postgresql://") and not value.startswith("postgresql+"):
        return value.replace("postgresql://", "postgresql+psycopg://", 1)
    return value


def _read_database_url() -> str:
    primary = (os.environ.get("DATABASE_URL", "") or "").strip()
    fallback = (os.environ.get("DATABASE_URI", "") or "").strip()
    return _normalize_database_url(primary or fallback)


class Config:
    APP_NAME = "PDFMaster Ultra Suite"
    ENV_NAME = (os.environ.get("APP_CONFIG", "development") or "development").strip().lower()

    SECRET_KEY = (os.environ.get("SECRET_KEY", "") or "").strip()
    SQLALCHEMY_DATABASE_URI = _read_database_url() or f"sqlite:///{(BASE_DIR / 'instance' / 'pdfmaster_ultra.db').as_posix()}"
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    SQLALCHEMY_ENGINE_OPTIONS = {
        "pool_pre_ping": True,
    }

    REMEMBER_COOKIE_HTTPONLY = True
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = (os.environ.get("SESSION_COOKIE_SAMESITE", "Lax") or "Lax").strip()
    SESSION_COOKIE_SECURE = _read_env_bool("SESSION_COOKIE_SECURE", False)
    SESSION_REFRESH_EACH_REQUEST = _read_env_bool("SESSION_REFRESH_EACH_REQUEST", False)
    SESSION_COOKIE_NAME = (os.environ.get("SESSION_COOKIE_NAME", "pdfmaster_session") or "pdfmaster_session").strip()
    REMEMBER_COOKIE_SECURE = SESSION_COOKIE_SECURE
    REMEMBER_COOKIE_HTTPONLY = True
    REMEMBER_COOKIE_SAMESITE = SESSION_COOKIE_SAMESITE
    WTF_CSRF_TIME_LIMIT = _read_env_int("WTF_CSRF_TIME_LIMIT_SECONDS", 60 * 60 * 2, minimum=300)
    WTF_CSRF_SSL_STRICT = _read_env_bool("WTF_CSRF_SSL_STRICT", False)
    PERMANENT_SESSION_LIFETIME = timedelta(seconds=_read_env_int("PERMANENT_SESSION_LIFETIME_SECONDS", 60 * 60 * 24 * 14, minimum=3600))
    EMAIL_VERIFICATION_TOKEN_TTL_SECONDS = _read_env_int("EMAIL_VERIFICATION_TOKEN_TTL_SECONDS", 60 * 60 * 24, minimum=900)
    PASSWORD_RESET_TOKEN_TTL_SECONDS = _read_env_int("PASSWORD_RESET_TOKEN_TTL_SECONDS", 60 * 60, minimum=300)
    CHECK_EMAIL_DELIVERABILITY = _read_env_bool("CHECK_EMAIL_DELIVERABILITY", True)
    PASSWORD_RESET_HELP_EMAIL = (
        os.environ.get("PASSWORD_RESET_HELP_EMAIL", "")
        or os.environ.get("ADMIN_EMAIL", "")
        or "pdfmasterultrasuite@gmail.com"
    ).strip().lower()

    MAX_CONTENT_LENGTH = _read_env_int("MAX_CONTENT_LENGTH", 50 * 1024 * 1024, minimum=1_048_576)
    MAX_SINGLE_UPLOAD_BYTES = _read_env_int("MAX_SINGLE_UPLOAD_BYTES", 25 * 1024 * 1024, minimum=1_048_576)
    TEMP_FILE_TTL_HOURS = _read_env_int("TEMP_FILE_TTL_HOURS", 24, minimum=1)
    SHARE_LINK_TTL_HOURS = _read_env_int("SHARE_LINK_TTL_HOURS", 24, minimum=1)

    UPLOAD_EXTENSIONS = {
        ".pdf",
        ".png",
        ".jpg",
        ".jpeg",
        ".webp",
        ".heic",
        ".svg",
        ".tif",
        ".tiff",
        ".bmp",
        ".doc",
        ".docx",
        ".ppt",
        ".pptx",
        ".xls",
        ".xlsx",
        ".csv",
        ".html",
        ".htm",
        ".rtf",
        ".md",
        ".txt",
        ".zip",
        ".json",
    }
    PDF_EXTENSIONS = {".pdf"}
    IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".tif", ".tiff", ".bmp"}

    OUTPUT_ROOT = Path((os.environ.get("OUTPUT_ROOT", "") or "").strip() or (BASE_DIR / "outputs"))
    UPLOAD_ROOT = Path((os.environ.get("UPLOAD_ROOT", "") or "").strip() or (BASE_DIR / "uploads"))
    CLOUD_ROOT = Path((os.environ.get("CLOUD_ROOT", "") or "").strip() or (BASE_DIR / "cloud"))
    SCAN_ROOT = Path((os.environ.get("SCAN_ROOT", "") or "").strip() or (UPLOAD_ROOT / "scanner"))

    MIN_TOOL_PRICE_PAISE = 500
    MAX_TOOL_PRICE_PAISE = 2500
    DEFAULT_TOP_UP_PAISE = 50000
    TOOL_PLACEHOLDER_TARGET = _read_env_int("TOOL_PLACEHOLDER_TARGET", 1000, minimum=100)

    JOB_MAX_WORKERS = _read_env_int("JOB_MAX_WORKERS", 2, minimum=1)
    OCR_LANG = (os.environ.get("OCR_LANG", "eng") or "eng").strip()
    TRANSLATION_PROVIDER = (os.environ.get("TRANSLATION_PROVIDER", "") or "").strip()

    GOOGLE_CLIENT_ID = (os.environ.get("GOOGLE_CLIENT_ID", "") or "").strip()
    GOOGLE_CLIENT_SECRET = (os.environ.get("GOOGLE_CLIENT_SECRET", "") or "").strip()
    GOOGLE_DISCOVERY_URL = (
        os.environ.get(
            "GOOGLE_DISCOVERY_URL",
            "https://accounts.google.com/.well-known/openid-configuration",
        )
        or "https://accounts.google.com/.well-known/openid-configuration"
    ).strip()
    GOOGLE_REDIRECT_URI = (os.environ.get("GOOGLE_REDIRECT_URI", "") or "").strip()

    PUBLIC_BASE_URL = (os.environ.get("PUBLIC_BASE_URL", "") or "").strip().rstrip("/")

    ADMIN_EMAIL = (os.environ.get("ADMIN_EMAIL", "") or "").strip().lower()
    ADMIN_ALLOWED_EMAILS = (os.environ.get("ADMIN_ALLOWED_EMAILS", "") or "").strip()
    ADMIN_ALLOWED_EMAIL_LIST = _read_env_csv("ADMIN_ALLOWED_EMAILS")
    ADMIN_OWNER_EMAILS = (os.environ.get("ADMIN_OWNER_EMAILS", "") or "").strip()
    ADMIN_OWNER_EMAIL_LIST = _read_env_csv("ADMIN_OWNER_EMAILS")
    ADMIN_SUPPORT_EMAILS = (os.environ.get("ADMIN_SUPPORT_EMAILS", "") or "").strip()
    ADMIN_SUPPORT_EMAIL_LIST = _read_env_csv("ADMIN_SUPPORT_EMAILS")
    ADMIN_SEED_EMAIL = (os.environ.get("ADMIN_SEED_EMAIL", "") or "").strip().lower()

    PAYMENT_MODE = (os.environ.get("PAYMENT_MODE", "demo") or "demo").strip().lower()
    RAZORPAY_KEY_ID = (os.environ.get("RAZORPAY_KEY_ID", "") or "").strip()
    RAZORPAY_KEY_SECRET = (os.environ.get("RAZORPAY_KEY_SECRET", "") or "").strip()
    RAZORPAY_WEBHOOK_SECRET = (os.environ.get("RAZORPAY_WEBHOOK_SECRET", "") or "").strip()
    RAZORPAY_CURRENCY = (os.environ.get("RAZORPAY_CURRENCY", "INR") or "INR").strip().upper()
    RAZORPAY_CALLBACK_URL = (os.environ.get("RAZORPAY_CALLBACK_URL", "") or "").strip()
    RAZORPAY_API_BASE = (
        os.environ.get("RAZORPAY_API_BASE", "https://api.razorpay.com")
        or "https://api.razorpay.com"
    ).strip().rstrip("/")

    SUBSCRIPTION_PRICE_PROFILE = (
        os.environ.get("SUBSCRIPTION_PRICE_PROFILE", "default") or "default"
    ).strip().lower()
    CUSTOM_DAILY_RATE_PAISE = max(1, _read_env_int("CUSTOM_DAILY_RATE_PAISE", 100))
    CUSTOM_PLAN_MIN_DAYS = max(1, _read_env_int("CUSTOM_PLAN_MIN_DAYS", 5))
    CUSTOM_PLAN_MAX_DAYS = max(
        CUSTOM_PLAN_MIN_DAYS,
        _read_env_int("CUSTOM_PLAN_MAX_DAYS", 365),
    )
    EXPIRING_SOON_DAYS = max(1, _read_env_int("EXPIRING_SOON_DAYS", 7))
    COUPONS_ENABLED = _read_env_bool("COUPONS_ENABLED", False)

    BILLING_SETTINGS_URL = (os.environ.get("BILLING_SETTINGS_URL", "") or "").strip()

    # Optional outbound email (signature reminders, notifications)
    SMTP_HOST = (os.environ.get("SMTP_HOST", "") or "").strip()
    SMTP_PORT = _read_env_int("SMTP_PORT", 587, minimum=1)
    SMTP_USERNAME = (os.environ.get("SMTP_USERNAME", "") or "").strip()
    SMTP_PASSWORD = (os.environ.get("SMTP_PASSWORD", "") or "").strip()
    SMTP_FROM_EMAIL = (os.environ.get("SMTP_FROM_EMAIL", "") or "").strip()
    SMTP_USE_TLS = _read_env_bool("SMTP_USE_TLS", True)
    SMTP_USE_SSL = _read_env_bool("SMTP_USE_SSL", False)

    # Cloud integration provider credentials (optional)
    GOOGLE_DRIVE_CLIENT_ID = (os.environ.get("GOOGLE_DRIVE_CLIENT_ID", "") or "").strip()
    GOOGLE_DRIVE_CLIENT_SECRET = (os.environ.get("GOOGLE_DRIVE_CLIENT_SECRET", "") or "").strip()
    DROPBOX_APP_KEY = (os.environ.get("DROPBOX_APP_KEY", "") or "").strip()
    DROPBOX_APP_SECRET = (os.environ.get("DROPBOX_APP_SECRET", "") or "").strip()
    ONEDRIVE_CLIENT_ID = (os.environ.get("ONEDRIVE_CLIENT_ID", "") or "").strip()
    ONEDRIVE_CLIENT_SECRET = (os.environ.get("ONEDRIVE_CLIENT_SECRET", "") or "").strip()

    # API/webhook controls
    API_DEFAULT_RATE_LIMIT_PER_MINUTE = _read_env_int("API_DEFAULT_RATE_LIMIT_PER_MINUTE", 60, minimum=10)
    WEBHOOK_MAX_FAILURES = _read_env_int("WEBHOOK_MAX_FAILURES", 10, minimum=1)
    RATELIMIT_ENABLED = _read_env_bool("RATELIMIT_ENABLED", True)
    RATELIMIT_STORAGE_URI = (os.environ.get("RATELIMIT_STORAGE_URI", "memory://") or "memory://").strip()
    RATELIMIT_HEADERS_ENABLED = _read_env_bool("RATELIMIT_HEADERS_ENABLED", True)

    # Privacy defaults
    PRIVACY_DEFAULT_AUTO_DELETE_HOURS = _read_env_int("PRIVACY_DEFAULT_AUTO_DELETE_HOURS", 24, minimum=1)
    AUTO_TEMP_CLEANUP = _read_env_bool("AUTO_TEMP_CLEANUP", True)
    AUTO_TEMP_CLEANUP_INTERVAL_MINUTES = _read_env_int("AUTO_TEMP_CLEANUP_INTERVAL_MINUTES", 60, minimum=5)

    AUTO_SEED_ON_STARTUP = _read_env_bool("AUTO_SEED_ON_STARTUP", False)
    DISABLE_SECURITY_HEADERS = _read_env_bool("DISABLE_SECURITY_HEADERS", False)
    CSP_ALLOW_UNSAFE_INLINE = _read_env_bool("CSP_ALLOW_UNSAFE_INLINE", True)


class DevelopmentConfig(Config):
    DEBUG = True


class ProductionConfig(Config):
    DEBUG = False
    PREFERRED_URL_SCHEME = "https"
    SESSION_COOKIE_SECURE = True
    REMEMBER_COOKIE_SECURE = True
    CSP_ALLOW_UNSAFE_INLINE = _read_env_bool("CSP_ALLOW_UNSAFE_INLINE", True)


config_map = {
    "development": DevelopmentConfig,
    "production": ProductionConfig,
    "default": DevelopmentConfig,
}

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv


BASE_DIR = Path(__file__).resolve().parent.parent
# Load local .env only as a fallback for development.
# Platform env vars (e.g., Render) remain authoritative because override=False.
load_dotenv(BASE_DIR / ".env", override=False)


def _read_env_int(name: str, default: int = 0) -> int:
    raw_value = (os.environ.get(name, "") or "").strip()
    if not raw_value:
        return default
    try:
        return int(raw_value)
    except ValueError:
        return default


def _read_env_bool(name: str, default: bool = False) -> bool:
    raw_value = (os.environ.get(name, "") or "").strip().lower()
    if not raw_value:
        return default
    return raw_value in {"1", "true", "yes", "on"}


class Config:
    APP_NAME = "PDFMaster Ultra Suite"
    SECRET_KEY = os.environ.get("SECRET_KEY", "change-this-in-production")
    SQLALCHEMY_DATABASE_URI = os.environ.get(
        "DATABASE_URL",
        f"sqlite:///{(BASE_DIR / 'instance' / 'pdfmaster_ultra.db').as_posix()}",
    )
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    REMEMBER_COOKIE_HTTPONLY = True
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"
    MAX_CONTENT_LENGTH = int(os.environ.get("MAX_CONTENT_LENGTH", 50 * 1024 * 1024))
    MAX_SINGLE_UPLOAD_BYTES = int(
        os.environ.get("MAX_SINGLE_UPLOAD_BYTES", 25 * 1024 * 1024)
    )
    TEMP_FILE_TTL_HOURS = int(os.environ.get("TEMP_FILE_TTL_HOURS", 24))
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
    OUTPUT_ROOT = BASE_DIR / "outputs"
    UPLOAD_ROOT = BASE_DIR / "uploads"
    CLOUD_ROOT = BASE_DIR / "cloud"
    SCAN_ROOT = BASE_DIR / "uploads" / "scanner"
    PASSWORD_RESET_TTL_MINUTES = 30
    SHARE_LINK_TTL_HOURS = 24
    MIN_TOOL_PRICE_PAISE = 500
    MAX_TOOL_PRICE_PAISE = 2500
    DEFAULT_TOP_UP_PAISE = 50000
    TOOL_PLACEHOLDER_TARGET = 1000
    JOB_MAX_WORKERS = int(os.environ.get("JOB_MAX_WORKERS", 2))
    OCR_LANG = os.environ.get("OCR_LANG", "eng")
    TRANSLATION_PROVIDER = os.environ.get("TRANSLATION_PROVIDER", "")
    MAIL_SERVER = (os.environ.get("MAIL_SERVER", "") or "").strip()
    MAIL_PORT = _read_env_int("MAIL_PORT", 587)
    MAIL_USE_TLS = _read_env_bool("MAIL_USE_TLS", True)
    MAIL_USE_SSL = _read_env_bool("MAIL_USE_SSL", False)
    MAIL_USERNAME = (os.environ.get("MAIL_USERNAME", "") or "").strip()
    # Google App Password is often copied with spaces; normalize to compact form.
    MAIL_PASSWORD = "".join((os.environ.get("MAIL_PASSWORD", "") or "").strip().split())
    MAIL_DEFAULT_SENDER = (
        os.environ.get("MAIL_DEFAULT_SENDER", MAIL_USERNAME)
        or MAIL_USERNAME
    ).strip()
    MAIL_SENDER_NAME = (
        os.environ.get("MAIL_SENDER_NAME", "PDFMaster Security")
        or "PDFMaster Security"
    ).strip()
    MAIL_TIMEOUT_SECONDS = max(1, _read_env_int("MAIL_TIMEOUT_SECONDS", 10))
    EMAIL_LOGO_URL = (
        os.environ.get(
            "EMAIL_LOGO_URL",
            "https://pdf-master-ultra-suite.onrender.com/static/images/logo.jpeg",
        )
        or "https://pdf-master-ultra-suite.onrender.com/static/images/logo.jpeg"
    ).strip()
    PUBLIC_BASE_URL = (
        os.environ.get("PUBLIC_BASE_URL", "https://pdf-master-ultra-suite.onrender.com")
        or "https://pdf-master-ultra-suite.onrender.com"
    ).strip().rstrip("/")
    OTP_TTL_MINUTES = int(os.environ.get("OTP_TTL_MINUTES", 2))
    OTP_MAX_ATTEMPTS = int(os.environ.get("OTP_MAX_ATTEMPTS", 5))
    GOOGLE_CLIENT_ID = (os.environ.get("GOOGLE_CLIENT_ID", "") or "").strip()
    GOOGLE_CLIENT_SECRET = (os.environ.get("GOOGLE_CLIENT_SECRET", "") or "").strip()
    GOOGLE_DISCOVERY_URL = (
        os.environ.get(
            "GOOGLE_DISCOVERY_URL",
            "https://accounts.google.com/.well-known/openid-configuration",
        )
        or "https://accounts.google.com/.well-known/openid-configuration"
    ).strip()
    ADMIN_EMAIL = (
        os.environ.get("ADMIN_EMAIL", "pdfmasterultrasuite@gmail.com")
        or "pdfmasterultrasuite@gmail.com"
    ).strip().lower()
    ADMIN_ALLOWED_EMAILS = (os.environ.get("ADMIN_ALLOWED_EMAILS", "") or "").strip()
    ADMIN_SEED_EMAIL = (os.environ.get("ADMIN_SEED_EMAIL", "") or "").strip().lower()
    RAZORPAY_KEY_ID = os.environ.get("RAZORPAY_KEY_ID", "").strip()
    RAZORPAY_KEY_SECRET = os.environ.get("RAZORPAY_KEY_SECRET", "").strip()
    RAZORPAY_CURRENCY = (os.environ.get("RAZORPAY_CURRENCY", "INR") or "INR").strip().upper()
    RAZORPAY_CALLBACK_URL = os.environ.get("RAZORPAY_CALLBACK_URL", "").strip()
    RAZORPAY_API_BASE = (os.environ.get("RAZORPAY_API_BASE", "https://api.razorpay.com") or "https://api.razorpay.com").strip().rstrip("/")
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
    BILLING_SETTINGS_URL = (
        os.environ.get(
            "BILLING_SETTINGS_URL",
            "https://pdf-master-ultra-suite.onrender.com/settings?tab=billing",
        )
        or "https://pdf-master-ultra-suite.onrender.com/settings?tab=billing"
    ).strip()


class DevelopmentConfig(Config):
    DEBUG = True


class ProductionConfig(Config):
    DEBUG = False
    SESSION_COOKIE_SECURE = True
    REMEMBER_COOKIE_SECURE = True


config_map = {
    "development": DevelopmentConfig,
    "production": ProductionConfig,
    "default": DevelopmentConfig,
}

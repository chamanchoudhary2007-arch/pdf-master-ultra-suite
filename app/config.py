from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv


BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")


def _read_env_int(name: str, default: int = 0) -> int:
    raw_value = (os.environ.get(name, "") or "").strip()
    if not raw_value:
        return default
    try:
        return int(raw_value)
    except ValueError:
        return default


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
    MAIL_PORT = _read_env_int("MAIL_PORT", 0)
    # OTP delivery requires TLS-enabled SMTP transport.
    MAIL_USE_TLS = True
    MAIL_USE_SSL = False
    MAIL_USERNAME = (os.environ.get("MAIL_USERNAME", "") or "").strip()
    # Google App Password is often copied with spaces; normalize to compact form.
    MAIL_PASSWORD = "".join((os.environ.get("MAIL_PASSWORD", "") or "").strip().split())
    MAIL_DEFAULT_SENDER = (
        os.environ.get("MAIL_DEFAULT_SENDER", MAIL_USERNAME)
        or MAIL_USERNAME
    ).strip()
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
    RAZORPAY_KEY_ID = os.environ.get("RAZORPAY_KEY_ID", "").strip()
    RAZORPAY_KEY_SECRET = os.environ.get("RAZORPAY_KEY_SECRET", "").strip()
    RAZORPAY_CURRENCY = (os.environ.get("RAZORPAY_CURRENCY", "INR") or "INR").strip().upper()
    RAZORPAY_CALLBACK_URL = os.environ.get("RAZORPAY_CALLBACK_URL", "").strip()
    RAZORPAY_API_BASE = (os.environ.get("RAZORPAY_API_BASE", "https://api.razorpay.com") or "https://api.razorpay.com").strip().rstrip("/")


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

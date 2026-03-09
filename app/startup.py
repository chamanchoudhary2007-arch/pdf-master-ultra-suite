from __future__ import annotations

import logging
from logging.config import dictConfig
from pathlib import Path

from flask import Flask

from app.seeds import seed_admin_user, seed_tool_catalog


def configure_logging(app: Flask) -> None:
    log_level = app.config.get("LOG_LEVEL", "INFO")
    dictConfig(
        {
            "version": 1,
            "disable_existing_loggers": False,
            "formatters": {
                "default": {
                    "format": "%(asctime)s %(levelname)s %(name)s %(message)s",
                }
            },
            "handlers": {
                "default": {
                    "class": "logging.StreamHandler",
                    "formatter": "default",
                }
            },
            "root": {
                "level": str(log_level).upper(),
                "handlers": ["default"],
            },
        }
    )


def _looks_like_placeholder_secret(secret: str) -> bool:
    candidate = (secret or "").strip().lower()
    if not candidate:
        return True
    placeholders = {
        "changeme",
        "change-me",
        "change-this",
        "change-this-in-production",
        "secret",
        "default",
        "password",
    }
    if candidate in placeholders:
        return True
    return "change" in candidate and "secret" in candidate


def validate_runtime_config(app: Flask) -> None:
    secret_key = (app.config.get("SECRET_KEY") or "").strip()
    env_name = (app.config.get("ENV_NAME") or "").strip().lower()
    is_production = env_name == "production"

    if not secret_key:
        if is_production:
            raise RuntimeError("SECRET_KEY is required in production.")
        app.logger.warning("SECRET_KEY is not set. Using Flask fallback; sessions are not stable across restarts.")
    elif _looks_like_placeholder_secret(secret_key):
        if is_production:
            raise RuntimeError("SECRET_KEY looks unsafe. Set a long random value in production.")
        app.logger.warning("SECRET_KEY appears to be a placeholder value.")

    if is_production and not app.config.get("SESSION_COOKIE_SECURE"):
        raise RuntimeError("SESSION_COOKIE_SECURE must be enabled in production.")

    payment_mode = (app.config.get("PAYMENT_MODE") or "demo").strip().lower()
    if payment_mode not in {"demo", "live"}:
        raise RuntimeError("PAYMENT_MODE must be either 'demo' or 'live'.")

    if payment_mode == "live":
        missing_live_keys = []
        if not (app.config.get("RAZORPAY_KEY_ID") or "").strip():
            missing_live_keys.append("RAZORPAY_KEY_ID")
        if not (app.config.get("RAZORPAY_KEY_SECRET") or "").strip():
            missing_live_keys.append("RAZORPAY_KEY_SECRET")
        if missing_live_keys:
            raise RuntimeError(
                "PAYMENT_MODE=live requires: " + ", ".join(missing_live_keys)
            )
        if not (app.config.get("RAZORPAY_WEBHOOK_SECRET") or "").strip():
            app.logger.warning(
                "RAZORPAY_WEBHOOK_SECRET is empty. Asynchronous UPI confirmations may be delayed."
            )

    has_google_id = bool((app.config.get("GOOGLE_CLIENT_ID") or "").strip())
    has_google_secret = bool((app.config.get("GOOGLE_CLIENT_SECRET") or "").strip())
    if has_google_id != has_google_secret:
        app.logger.warning(
            "Google OAuth config is incomplete. Set both GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET."
        )

    smtp_host = bool((app.config.get("SMTP_HOST") or "").strip())
    smtp_from = bool((app.config.get("SMTP_FROM_EMAIL") or "").strip())
    if smtp_host != smtp_from:
        app.logger.warning(
            "SMTP config is incomplete. Set both SMTP_HOST and SMTP_FROM_EMAIL to enable email delivery."
        )
    if not app.config.get("CHECK_EMAIL_DELIVERABILITY", True):
        app.logger.warning("CHECK_EMAIL_DELIVERABILITY is disabled. Invalid/unreachable email domains may pass signup checks.")

    provider_pairs = (
        ("GOOGLE_DRIVE_CLIENT_ID", "GOOGLE_DRIVE_CLIENT_SECRET", "Google Drive"),
        ("DROPBOX_APP_KEY", "DROPBOX_APP_SECRET", "Dropbox"),
        ("ONEDRIVE_CLIENT_ID", "ONEDRIVE_CLIENT_SECRET", "OneDrive"),
    )
    for id_key, secret_key, label in provider_pairs:
        has_id = bool((app.config.get(id_key) or "").strip())
        has_secret = bool((app.config.get(secret_key) or "").strip())
        if has_id != has_secret:
            app.logger.warning(
                "%s integration config is incomplete. Set both %s and %s.",
                label,
                id_key,
                secret_key,
            )

    allowlist = app.config.get("ADMIN_ALLOWED_EMAIL_LIST") or ()
    if not allowlist:
        app.logger.warning("ADMIN_ALLOWED_EMAILS is empty. Only users with role='admin' can access admin routes.")
    owner_allowlist = app.config.get("ADMIN_OWNER_EMAIL_LIST") or ()
    support_allowlist = app.config.get("ADMIN_SUPPORT_EMAIL_LIST") or ()
    if owner_allowlist and not allowlist:
        app.logger.warning("ADMIN_OWNER_EMAILS is set but ADMIN_ALLOWED_EMAILS is empty. Owners will still be granted portal access.")
    if support_allowlist and not allowlist:
        app.logger.warning("ADMIN_SUPPORT_EMAILS is set but ADMIN_ALLOWED_EMAILS is empty. Support users only get read-only portal access.")

    database_uri = (app.config.get("SQLALCHEMY_DATABASE_URI") or "").strip()
    if database_uri.startswith("sqlite:///"):
        db_path = Path(database_uri.replace("sqlite:///", "", 1))
        if not db_path.is_absolute():
            db_path = Path(app.root_path).parent / db_path
        db_path.parent.mkdir(parents=True, exist_ok=True)

    if app.config.get("RATELIMIT_ENABLED") and not (app.config.get("RATELIMIT_STORAGE_URI") or "").strip():
        app.logger.warning("RATELIMIT_STORAGE_URI is empty. Falling back to in-memory limits.")


def bootstrap_reference_data(app: Flask) -> None:
    if not app.config.get("AUTO_SEED_ON_STARTUP"):
        return
    try:
        created = seed_tool_catalog(app.config["TOOL_PLACEHOLDER_TARGET"])
        seed_admin_user()
        app.logger.info("Startup seeding complete. created_tool_rows=%s", created)
    except Exception:
        app.logger.exception("Startup seeding failed")
        raise

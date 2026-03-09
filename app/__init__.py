from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from flask import Flask, current_app, jsonify, render_template, request, session, url_for
from flask_login import current_user
from flask_migrate import upgrade
from werkzeug.exceptions import HTTPException
from werkzeug.middleware.proxy_fix import ProxyFix

from app.config import config_map
from app.extensions import csrf, db, limiter, login_manager, migrate, oauth
from app.models import ManagedFile, User
from app.services.url_service import UrlService
from app.startup import bootstrap_reference_data, configure_logging, validate_runtime_config


def create_app(config_name: str = "default") -> Flask:
    app = Flask(__name__, instance_relative_config=True)
    app.config.from_object(config_map.get(config_name, config_map["default"]))
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_port=1)

    configure_logging(app)
    validate_runtime_config(app)

    Path(app.instance_path).mkdir(parents=True, exist_ok=True)
    for key in ("UPLOAD_ROOT", "OUTPUT_ROOT", "CLOUD_ROOT", "SCAN_ROOT"):
        Path(app.config[key]).mkdir(parents=True, exist_ok=True)

    register_extensions(app)
    register_blueprints(app)
    register_hooks(app)
    register_error_handlers(app)
    register_commands(app)
    register_template_helpers(app)

    with app.app_context():
        bootstrap_reference_data(app)

    return app


def register_extensions(app: Flask) -> None:
    db.init_app(app)
    migrate.init_app(app, db)
    login_manager.init_app(app)
    csrf.init_app(app)
    limiter.init_app(app)
    oauth.init_app(app)

    if (
        not getattr(oauth, "is_stub", False)
        and app.config.get("GOOGLE_CLIENT_ID")
        and app.config.get("GOOGLE_CLIENT_SECRET")
    ):
        oauth._registry.pop("google", None)
        oauth.register(
            name="google",
            client_id=app.config["GOOGLE_CLIENT_ID"],
            client_secret=app.config["GOOGLE_CLIENT_SECRET"],
            server_metadata_url=app.config["GOOGLE_DISCOVERY_URL"],
            client_kwargs={"scope": "openid email profile"},
        )
    elif getattr(oauth, "is_stub", False) and (
        app.config.get("GOOGLE_CLIENT_ID") or app.config.get("GOOGLE_CLIENT_SECRET")
    ):
        app.logger.warning(
            "Google OAuth keys found but Authlib is not installed. Install dependencies to enable Google sign-in."
        )

    login_manager.login_view = "auth.login"
    login_manager.login_message_category = "warning"
    login_manager.session_protection = "strong"


@login_manager.user_loader
def load_user(user_id: str) -> User | None:
    return db.session.get(User, int(user_id))


def register_blueprints(app: Flask) -> None:
    from app.blueprints.admin import admin_bp
    from app.blueprints.api import api_bp
    from app.blueprints.auth import auth_bp
    from app.blueprints.main import main_bp
    from app.blueprints.tools import tools_bp
    from app.blueprints.workspace import workspace_bp

    app.register_blueprint(main_bp)
    app.register_blueprint(auth_bp, url_prefix="/auth")
    app.register_blueprint(tools_bp, url_prefix="/tools")
    app.register_blueprint(admin_bp, url_prefix="/admin")
    app.register_blueprint(workspace_bp)
    app.register_blueprint(api_bp)


def _build_csp(app: Flask) -> str:
    allow_unsafe_inline = bool(app.config.get("CSP_ALLOW_UNSAFE_INLINE"))
    style_src = ["'self'", "https://cdn.jsdelivr.net"]
    script_src = ["'self'", "https://cdn.jsdelivr.net", "https://checkout.razorpay.com"]
    if allow_unsafe_inline:
        style_src.append("'unsafe-inline'")
        script_src.append("'unsafe-inline'")

    directives = {
        "default-src": [
            "'self'",
            "https://cdn.jsdelivr.net",
            "https://checkout.razorpay.com",
            "https://api.razorpay.com",
        ],
        "img-src": ["'self'", "data:", "blob:", "https://*.razorpay.com"],
        "style-src": style_src,
        "script-src": script_src,
        "font-src": ["'self'", "data:", "https://cdn.jsdelivr.net"],
        "connect-src": ["'self'", "https://api.razorpay.com", "https://checkout.razorpay.com"],
        "frame-src": ["'self'", "https://api.razorpay.com", "https://checkout.razorpay.com"],
        "object-src": ["'none'"],
        "base-uri": ["'self'"],
        "frame-ancestors": ["'self'"],
        "form-action": ["'self'"],
    }
    return "; ".join(f"{directive} {' '.join(values)}" for directive, values in directives.items())


def register_hooks(app: Flask) -> None:
    @app.before_request
    def make_session_permanent() -> None:
        session.permanent = True
        if not app.config.get("AUTO_TEMP_CLEANUP", True):
            return

        now = datetime.now(timezone.utc)
        interval_minutes = int(app.config.get("AUTO_TEMP_CLEANUP_INTERVAL_MINUTES") or 60)
        interval_minutes = max(5, interval_minutes)
        last_run = app.config.get("_AUTO_TEMP_CLEANUP_LAST_RUN_AT")
        if isinstance(last_run, datetime):
            if last_run.tzinfo is None:
                last_run = last_run.replace(tzinfo=timezone.utc)
            if now - last_run < timedelta(minutes=interval_minutes):
                return

        from app.services.storage_service import StorageService

        try:
            StorageService.cleanup_expired_temp_files()
        except Exception:
            app.logger.exception("Scheduled temp file cleanup failed")
        finally:
            app.config["_AUTO_TEMP_CLEANUP_LAST_RUN_AT"] = now

    @app.after_request
    def apply_security_headers(response):
        if app.config.get("DISABLE_SECURITY_HEADERS"):
            return response

        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "SAMEORIGIN"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["Permissions-Policy"] = "camera=(self), microphone=()"
        response.headers["Content-Security-Policy"] = _build_csp(app)
        response.headers["Cross-Origin-Opener-Policy"] = "same-origin"
        response.headers["Cross-Origin-Resource-Policy"] = "same-origin"
        response.headers["Cross-Origin-Embedder-Policy"] = "unsafe-none"

        if request.is_secure:
            response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"

        return response


def register_error_handlers(app: Flask) -> None:
    def _wants_json_response() -> bool:
        if request.path.startswith("/billing/") or request.path.startswith("/api/"):
            return True
        if request.headers.get("X-Requested-With", "").lower() == "xmlhttprequest":
            return True
        return request.accept_mimetypes.best == "application/json"

    @app.errorhandler(HTTPException)
    def handle_http_exception(exc: HTTPException):
        if exc.code == 429:
            too_many_message = "Too many requests. Please wait a moment and try again."
            if _wants_json_response():
                return jsonify({"error": too_many_message}), 429
            return (
                render_template(
                    "errors/error.html",
                    status_code=429,
                    title="Too Many Requests",
                    message=too_many_message,
                ),
                429,
            )
        if _wants_json_response():
            return jsonify({"error": exc.description or exc.name}), exc.code
        return (
            render_template(
                "errors/error.html",
                status_code=exc.code,
                title=exc.name,
                message=exc.description,
            ),
            exc.code,
        )

    @app.errorhandler(Exception)
    def handle_exception(exc: Exception):
        current_app.logger.exception("Unhandled exception")
        if _wants_json_response():
            return jsonify({"error": "Something went wrong while processing your request."}), 500
        return (
            render_template(
                "errors/error.html",
                status_code=500,
                title="Server Error",
                message="Something went wrong while processing your request.",
            ),
            500,
        )


def register_template_helpers(app: Flask) -> None:
    @app.template_filter("money")
    def format_money(value: int) -> str:
        rupees = (value or 0) / 100
        return f"\u20b9{rupees:,.2f}"

    @app.context_processor
    def inject_globals():
        from app.services.auth_service import AuthService
        from app.services.subscription_service import SubscriptionService

        is_premium_user = False
        profile_photo_url = None
        can_access_admin = False
        can_manage_admin = False
        admin_role = "user"
        if current_user.is_authenticated:
            is_premium_user = SubscriptionService.is_user_premium(current_user)
            admin_role = AuthService.effective_portal_role(current_user)
            can_access_admin = AuthService.can_access_admin_panel(current_user)
            can_manage_admin = AuthService.has_admin_access(current_user, min_role="admin")
            profile_photo = (
                ManagedFile.query.filter_by(
                    user_id=current_user.id,
                    label="profile_photo",
                    is_deleted=False,
                )
                .order_by(ManagedFile.created_at.desc())
                .first()
            )
            if profile_photo:
                profile_photo_url = url_for("main.preview_file", file_id=profile_photo.id)

        password_reset_help_email = (
            current_app.config.get("PASSWORD_RESET_HELP_EMAIL")
            or current_app.config.get("ADMIN_EMAIL")
            or "pdfmasterultrasuite@gmail.com"
        ).strip()
        account_email_line = (
            f"Account email: {current_user.email}\n"
            if current_user.is_authenticated
            else ""
        )
        password_reset_help_link = UrlService.gmail_compose_url(
            to_email=password_reset_help_email,
            subject=f"{current_app.config['APP_NAME']} - Help to forget key",
            body=(
                "Hello Admin,\n\n"
                "I forgot my 4-digit password reset key. Please help me regain access.\n"
                f"{account_email_line}"
                "Reason: \n\n"
                "Thanks."
            ),
        )

        return {
            "APP_NAME": current_app.config["APP_NAME"],
            "is_premium_user": is_premium_user,
            "profile_photo_url": profile_photo_url,
            "can_access_admin": can_access_admin,
            "can_manage_admin": can_manage_admin,
            "admin_role": admin_role,
            "billing_settings_url": UrlService.resolve_app_url(
                "main.settings",
                config_key="BILLING_SETTINGS_URL",
                tab="billing",
            ),
            "tool_categories": [
                "Organize",
                "Convert",
                "Security",
                "Edit",
                "Students",
                "Office",
                "Legal",
                "Finance",
                "Utilities",
                "OCR",
                "AI Tools",
            ],
            "max_single_upload_mb": int(current_app.config["MAX_SINGLE_UPLOAD_BYTES"] / (1024 * 1024)),
            "password_reset_help_email": password_reset_help_email,
            "password_reset_help_link": password_reset_help_link,
        }


def register_commands(app: Flask) -> None:
    from app.seeds import seed_admin_user, seed_tool_catalog
    from app.services.storage_service import StorageService
    from sqlalchemy import inspect, text

    def _upgrade_or_stamp_legacy() -> str:
        """Apply migrations with safeguards for legacy and inconsistent schemas."""
        from flask_migrate import stamp

        inspector = inspect(db.engine)
        legacy_signature_tables = ("users", "tool_catalog", "managed_files", "jobs")
        core_table_presence = {
            table_name: inspector.has_table(table_name)
            for table_name in legacy_signature_tables
        }
        has_any_core_table = any(core_table_presence.values())
        has_all_core_tables = all(core_table_presence.values())

        has_alembic_table = inspector.has_table("alembic_version")
        has_alembic_version = False
        if has_alembic_table:
            version_rows = db.session.execute(text("SELECT version_num FROM alembic_version")).scalars().all()
            has_alembic_version = bool(version_rows)

        if has_alembic_version and not has_any_core_table:
            stamp(revision="base")
            current_app.logger.warning(
                "Alembic version found but core tables are missing. Reset to base and re-applied migrations."
            )
            upgrade()
            return "repaired"

        if has_all_core_tables and not has_alembic_version:
            stamp(revision="head")
            current_app.logger.warning(
                "Legacy database detected (pre-migrations). Stamped alembic head without schema changes."
            )
            return "stamped"

        upgrade()
        return "upgraded"

    @app.cli.command("init-db")
    def init_db_command() -> None:
        """Apply migrations (or stamp legacy schema) only."""
        status = _upgrade_or_stamp_legacy()
        if status == "stamped":
            print("Legacy schema detected. Migration head stamped successfully.")
        elif status == "repaired":
            print("Inconsistent migration state detected. Schema repaired and migrations applied.")
        else:
            print("Database migrations applied.")

    @app.cli.command("seed-data")
    def seed_data_command() -> None:
        """Seed tool catalog and optional admin user."""
        created = seed_tool_catalog(app.config["TOOL_PLACEHOLDER_TARGET"])
        seed_admin_user()
        print(f"Seed complete. created_tool_rows={created}")

    @app.cli.command("init-app")
    def init_app_command() -> None:
        """Apply migrations and seed initial data."""
        status = _upgrade_or_stamp_legacy()
        created = seed_tool_catalog(app.config["TOOL_PLACEHOLDER_TARGET"])
        seed_admin_user()
        print(f"App initialized ({status}). created_tool_rows={created}")

    @app.cli.command("check-config")
    def check_config_command() -> None:
        """Validate runtime configuration."""
        validate_runtime_config(current_app)
        print("Configuration looks valid.")

    @app.cli.command("cleanup-files")
    def cleanup_files_command() -> None:
        stats = StorageService.cleanup_expired_temp_files()
        print(
            "Cleanup complete: "
            f"removed_files={stats['removed_files']}, "
            f"marked_deleted={stats['marked_deleted']}, "
            f"failed={stats['failed']}, "
            f"ttl_hours={stats['ttl_hours']}"
        )

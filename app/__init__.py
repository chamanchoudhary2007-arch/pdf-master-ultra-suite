from __future__ import annotations

from pathlib import Path

from flask import Flask, current_app, url_for
from flask_login import current_user
from sqlalchemy import inspect, text

from app.config import config_map
from app.extensions import csrf, db, login_manager, mail, migrate, oauth
from app.models import ManagedFile, User, generate_referral_code
from app.seeds import seed_admin_user, seed_tool_catalog


def create_app(config_name: str = "default") -> Flask:
    app = Flask(__name__, instance_relative_config=True)
    app.config.from_object(config_map.get(config_name, config_map["default"]))

    Path(app.instance_path).mkdir(parents=True, exist_ok=True)
    for key in ("UPLOAD_ROOT", "OUTPUT_ROOT", "CLOUD_ROOT", "SCAN_ROOT"):
        Path(app.config[key]).mkdir(parents=True, exist_ok=True)

    register_extensions(app)
    register_blueprints(app)
    register_hooks(app)
    register_commands(app)
    register_template_helpers(app)

    with app.app_context():
        db.create_all()
        ensure_user_referral_schema()
        seed_tool_catalog(app.config["TOOL_PLACEHOLDER_TARGET"])
        seed_admin_user()

    return app


def register_extensions(app: Flask) -> None:
    db.init_app(app)
    migrate.init_app(app, db)
    login_manager.init_app(app)
    csrf.init_app(app)
    mail.init_app(app)
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


def _generate_unique_referral_code_sql(conn) -> str:
    for _ in range(64):
        code = generate_referral_code()
        exists = conn.execute(
            text("SELECT 1 FROM users WHERE referral_code = :code LIMIT 1"),
            {"code": code},
        ).scalar()
        if not exists:
            return code
    raise RuntimeError("Unable to generate unique referral code for schema upgrade.")


def ensure_user_referral_schema() -> None:
    inspector = inspect(db.engine)
    if "users" not in inspector.get_table_names():
        return

    existing_columns = {column["name"] for column in inspector.get_columns("users")}
    needs_update = any(
        key not in existing_columns for key in ("referral_code", "referred_by", "total_referrals")
    )

    with db.engine.begin() as conn:
        if "referral_code" not in existing_columns:
            conn.execute(text("ALTER TABLE users ADD COLUMN referral_code VARCHAR(16)"))
        if "referred_by" not in existing_columns:
            conn.execute(text("ALTER TABLE users ADD COLUMN referred_by VARCHAR(16)"))
        if "total_referrals" not in existing_columns:
            conn.execute(
                text("ALTER TABLE users ADD COLUMN total_referrals INTEGER NOT NULL DEFAULT 0")
            )

        conn.execute(text("UPDATE users SET total_referrals = COALESCE(total_referrals, 0)"))

        missing_code_user_ids = conn.execute(
            text("SELECT id FROM users WHERE referral_code IS NULL OR TRIM(referral_code) = ''")
        ).scalars().all()
        for user_id in missing_code_user_ids:
            conn.execute(
                text("UPDATE users SET referral_code = :referral_code WHERE id = :user_id"),
                {
                    "referral_code": _generate_unique_referral_code_sql(conn),
                    "user_id": user_id,
                },
            )

        conn.execute(
            text("CREATE UNIQUE INDEX IF NOT EXISTS ix_users_referral_code ON users (referral_code)")
        )
        conn.execute(text("CREATE INDEX IF NOT EXISTS ix_users_referred_by ON users (referred_by)"))

    if needs_update:
        current_app.logger.info("Applied users referral schema compatibility upgrade.")


@login_manager.user_loader
def load_user(user_id: str) -> User | None:
    return db.session.get(User, int(user_id))


def register_blueprints(app: Flask) -> None:
    from app.blueprints.admin import admin_bp
    from app.blueprints.auth import auth_bp
    from app.blueprints.main import main_bp
    from app.blueprints.tools import tools_bp

    app.register_blueprint(main_bp)
    app.register_blueprint(auth_bp, url_prefix="/auth")
    app.register_blueprint(tools_bp, url_prefix="/tools")
    app.register_blueprint(admin_bp, url_prefix="/admin")


def register_hooks(app: Flask) -> None:
    @app.after_request
    def apply_security_headers(response):
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "SAMEORIGIN"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["Permissions-Policy"] = "camera=(self), microphone=()"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self' https://cdn.jsdelivr.net https://checkout.razorpay.com https://api.razorpay.com; "
            "img-src 'self' data: blob: https://*.razorpay.com; "
            "style-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; "
            "script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net https://checkout.razorpay.com; "
            "font-src 'self' data: https://cdn.jsdelivr.net; "
            "connect-src 'self' https://api.razorpay.com https://checkout.razorpay.com; "
            "frame-src 'self' https://api.razorpay.com https://checkout.razorpay.com;"
        )
        return response


def register_template_helpers(app: Flask) -> None:
    @app.template_filter("money")
    def format_money(value: int) -> str:
        rupees = (value or 0) / 100
        return f"\u20b9{rupees:,.2f}"

    @app.context_processor
    def inject_globals():
        is_premium_user = False
        profile_photo_url = None
        if current_user.is_authenticated:
            from app.services.subscription_service import SubscriptionService

            is_premium_user = SubscriptionService.is_user_premium(current_user)
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
        return {
            "APP_NAME": current_app.config["APP_NAME"],
            "is_premium_user": is_premium_user,
            "profile_photo_url": profile_photo_url,
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
        }


def register_commands(app: Flask) -> None:
    from app.extensions import db
    from app.seeds import seed_admin_user, seed_tool_catalog
    from app.services.storage_service import StorageService

    @app.cli.command("init-db")
    def init_db_command() -> None:
        db.create_all()
        ensure_user_referral_schema()
        seed_tool_catalog(app.config["TOOL_PLACEHOLDER_TARGET"])
        seed_admin_user()
        print("Database initialized and seed data loaded.")

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

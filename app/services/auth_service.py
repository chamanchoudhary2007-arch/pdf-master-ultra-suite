from __future__ import annotations

import secrets
import string
import re
from datetime import datetime, timedelta, timezone
from functools import wraps

import pytz
from flask import abort, current_app, request, session
from flask_login import current_user
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from werkzeug.security import check_password_hash, generate_password_hash

try:
    from email_validator import EmailNotValidError, validate_email
except Exception:  # pragma: no cover - optional dependency fallback
    EmailNotValidError = ValueError
    validate_email = None

from app.extensions import db
from app.models import ActivityLog, User, UserSubscription, generate_referral_code, utcnow


class AuthService:
    REFERRAL_REWARD_STEP = 2
    REFERRAL_REWARD_DAYS = 1
    IST_TZ = pytz.timezone("Asia/Kolkata")
    PASSWORD_RESET_KEY_LENGTH = 4
    PASSWORD_RESET_ICON_CHOICES = (
        "bi-key-fill",
        "bi-shield-lock-fill",
        "bi-safe2-fill",
        "bi-fingerprint",
        "bi-stars",
        "bi-lock-fill",
    )
    EMAIL_PATTERN = re.compile(r"^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$")
    DISPOSABLE_EMAIL_DOMAINS = {
        "mailinator.com",
        "10minutemail.com",
        "guerrillamail.com",
        "tempmail.com",
        "yopmail.com",
        "trashmail.com",
    }
    ROLE_LEVELS = {
        "user": 0,
        "support": 1,
        "admin": 2,
        "owner": 3,
    }
    ADMIN_PORTAL_ROLES = {"support", "admin", "owner"}

    @staticmethod
    def _normalize_email(email: str) -> str:
        return (email or "").strip().lower()

    @staticmethod
    def _validate_email_address(email: str) -> str:
        normalized_email = AuthService._normalize_email(email)
        if not normalized_email:
            raise ValueError("Email is required.")
        if len(normalized_email) > 255 or " " in normalized_email or ".." in normalized_email:
            raise ValueError("Please enter a valid email address.")
        if not AuthService.EMAIL_PATTERN.fullmatch(normalized_email):
            raise ValueError("Please enter a valid email address.")
        email_parts = normalized_email.rsplit("@", 1)
        if len(email_parts) != 2:
            raise ValueError("Please enter a valid email address.")
        domain = email_parts[1]
        if domain in AuthService.DISPOSABLE_EMAIL_DOMAINS:
            raise ValueError("Disposable email domains are not allowed. Use your real email address.")
        if validate_email:
            try:
                check_deliverability = bool(current_app.config.get("CHECK_EMAIL_DELIVERABILITY", True))
                result = validate_email(normalized_email, check_deliverability=check_deliverability)
                normalized_email = AuthService._normalize_email(result.normalized)
            except EmailNotValidError as exc:
                raise ValueError("Please enter a valid reachable email address.") from exc
        return normalized_email

    @staticmethod
    def validate_email_address(email: str) -> str:
        return AuthService._validate_email_address(email)

    @staticmethod
    def _role_email_allowlists() -> dict[str, set[str]]:
        owner_emails: set[str] = set()
        admin_emails: set[str] = set()
        support_emails: set[str] = set()

        owner_values = (
            current_app.config.get("ADMIN_OWNER_EMAILS", ""),
        )
        admin_values = (
            current_app.config.get("ADMIN_ALLOWED_EMAILS", ""),
            current_app.config.get("ADMIN_EMAIL", ""),
            current_app.config.get("ADMIN_SEED_EMAIL", ""),
        )
        support_values = (
            current_app.config.get("ADMIN_SUPPORT_EMAILS", ""),
        )

        for raw_value in owner_values:
            for item in str(raw_value or "").split(","):
                normalized = AuthService._normalize_email(item)
                if normalized:
                    owner_emails.add(normalized)

        for raw_value in admin_values:
            for item in str(raw_value or "").split(","):
                normalized = AuthService._normalize_email(item)
                if normalized:
                    admin_emails.add(normalized)

        for raw_value in support_values:
            for item in str(raw_value or "").split(","):
                normalized = AuthService._normalize_email(item)
                if normalized:
                    support_emails.add(normalized)

        # Higher privileged lists are implicitly valid for lower gates.
        admin_emails.update(owner_emails)
        support_emails.update(admin_emails)

        return {
            "owner": owner_emails,
            "admin": admin_emails,
            "support": support_emails,
        }

    @staticmethod
    def _normalize_role(role: str | None) -> str:
        candidate = (role or "").strip().lower()
        if candidate in AuthService.ROLE_LEVELS:
            return candidate
        return "user"

    @staticmethod
    def _validate_password_strength(password: str) -> None:
        value = (password or "").strip()
        if len(value) < 8:
            raise ValueError("Password must be at least 8 characters.")
        if value.isalpha() or value.isdigit():
            raise ValueError("Password must include both letters and numbers.")

    @staticmethod
    def password_reset_icon_choices() -> tuple[str, ...]:
        return AuthService.PASSWORD_RESET_ICON_CHOICES

    @staticmethod
    def _is_valid_reset_icon(icon_name: str | None) -> bool:
        return (icon_name or "").strip() in AuthService.PASSWORD_RESET_ICON_CHOICES

    @staticmethod
    def is_valid_password_reset_icon(icon_name: str | None) -> bool:
        return AuthService._is_valid_reset_icon(icon_name)

    @staticmethod
    def _default_reset_icon() -> str:
        return secrets.choice(AuthService.PASSWORD_RESET_ICON_CHOICES)

    @staticmethod
    def _normalize_password_reset_key(reset_key: str) -> str:
        return "".join(ch for ch in (reset_key or "").strip() if ch.isdigit())

    @staticmethod
    def generate_password_reset_key() -> str:
        digits = string.digits
        return "".join(secrets.choice(digits) for _ in range(AuthService.PASSWORD_RESET_KEY_LENGTH))

    @staticmethod
    def set_password_reset_key(
        user: User,
        reset_key: str,
        *,
        icon_name: str = "",
        commit: bool = True,
        log_activity: bool = True,
    ) -> str:
        if not user:
            raise ValueError("User account is required.")
        normalized_key = AuthService._normalize_password_reset_key(reset_key)
        if len(normalized_key) != AuthService.PASSWORD_RESET_KEY_LENGTH:
            raise ValueError("Password reset key must be exactly 4 digits.")

        selected_icon = (icon_name or "").strip()
        if not AuthService._is_valid_reset_icon(selected_icon):
            selected_icon = (
                user.password_reset_key_icon
                if AuthService._is_valid_reset_icon(getattr(user, "password_reset_key_icon", ""))
                else AuthService._default_reset_icon()
            )

        user.password_reset_key_hash = generate_password_hash(normalized_key)
        user.password_reset_key_icon = selected_icon

        if commit:
            db.session.commit()
        if log_activity:
            AuthService._log_activity_safely(
                user.id,
                "user.password_reset_key.updated",
                "user",
                str(user.id),
                details={"icon": selected_icon},
            )
        return normalized_key

    @staticmethod
    def ensure_password_reset_key(user: User, *, commit: bool = True) -> str:
        if not user:
            return ""
        generated_key = ""
        if not (user.password_reset_key_hash or "").strip():
            generated_key = AuthService.generate_password_reset_key()
            AuthService.set_password_reset_key(
                user,
                generated_key,
                icon_name=user.password_reset_key_icon or AuthService._default_reset_icon(),
                commit=False,
                log_activity=False,
            )
        elif not AuthService._is_valid_reset_icon(user.password_reset_key_icon):
            user.password_reset_key_icon = AuthService._default_reset_icon()

        if commit and (generated_key or db.session.is_modified(user)):
            db.session.commit()

        if generated_key:
            AuthService._log_activity_safely(
                user.id,
                "user.password_reset_key.generated",
                "user",
                str(user.id),
                details={"icon": user.password_reset_key_icon},
            )
        return generated_key

    @staticmethod
    def verify_password_reset_key(user: User, reset_key: str) -> bool:
        if not user or not (user.password_reset_key_hash or "").strip():
            return False
        normalized_key = AuthService._normalize_password_reset_key(reset_key)
        if len(normalized_key) != AuthService.PASSWORD_RESET_KEY_LENGTH:
            return False
        return check_password_hash(user.password_reset_key_hash, normalized_key)

    @staticmethod
    def _password_reset_serializer() -> URLSafeTimedSerializer:
        return URLSafeTimedSerializer(
            str(current_app.config.get("SECRET_KEY") or ""),
            salt="pdfmaster-password-reset-v1",
        )

    @staticmethod
    def _email_verification_serializer() -> URLSafeTimedSerializer:
        return URLSafeTimedSerializer(
            str(current_app.config.get("SECRET_KEY") or ""),
            salt="pdfmaster-email-verify-v1",
        )

    @staticmethod
    def generate_email_verification_token(email: str) -> str:
        normalized_email = AuthService._validate_email_address(email)
        serializer = AuthService._email_verification_serializer()
        return serializer.dumps(
            {
                "email": normalized_email,
                "nonce": secrets.token_urlsafe(8),
            }
        )

    @staticmethod
    def resolve_user_from_email_verification_token(
        token: str,
        max_age_seconds: int,
    ) -> User | None:
        serializer = AuthService._email_verification_serializer()
        try:
            payload = serializer.loads((token or "").strip(), max_age=max_age_seconds)
        except (BadSignature, SignatureExpired):
            return None

        normalized_email = AuthService._normalize_email(str(payload.get("email", "")))
        if not normalized_email:
            return None

        user = User.query.filter_by(email=normalized_email).first()
        if not user or not user.is_active:
            return None
        return user

    @staticmethod
    def mark_email_verified(user: User) -> None:
        if not user:
            raise ValueError("User account is required.")
        changed = False
        if not user.is_active:
            raise ValueError("This account is disabled.")
        if not user.is_verified:
            user.is_verified = True
            changed = True
            if user.referred_by:
                try:
                    AuthService._apply_referral_reward(user, user.referred_by)
                except Exception:
                    current_app.logger.exception(
                        "Referral reward apply failed during email verification. user_id=%s referred_by=%s",
                        user.id,
                        user.referred_by,
                    )
        if changed:
            db.session.commit()
            AuthService._log_activity_safely(
                user.id,
                "user.email.verified",
                "user",
                str(user.id),
                details={"provider": "local"},
            )

    @staticmethod
    def generate_password_reset_token(email: str) -> str:
        normalized_email = AuthService._normalize_email(email)
        if not normalized_email:
            raise ValueError("Email is required.")
        serializer = AuthService._password_reset_serializer()
        return serializer.dumps(
            {
                "email": normalized_email,
                "nonce": secrets.token_urlsafe(8),
            }
        )

    @staticmethod
    def resolve_user_from_password_reset_token(token: str, max_age_seconds: int) -> User | None:
        serializer = AuthService._password_reset_serializer()
        try:
            payload = serializer.loads((token or "").strip(), max_age=max_age_seconds)
        except (BadSignature, SignatureExpired):
            return None

        normalized_email = AuthService._normalize_email(str(payload.get("email", "")))
        if not normalized_email:
            return None

        user = User.query.filter_by(email=normalized_email).first()
        if not user or not user.is_active:
            return None
        return user

    @staticmethod
    def update_password(user: User, new_password: str) -> None:
        if not user:
            raise ValueError("User account is required.")
        AuthService._validate_password_strength(new_password)
        user.set_password(new_password)
        user.is_verified = True
        user.last_login_at = utcnow()
        db.session.commit()
        AuthService._log_activity_safely(
            user.id,
            "user.password.reset",
            "user",
            str(user.id),
            details={"provider": "local"},
        )

    @staticmethod
    def _normalize_full_name(full_name: str, email: str) -> str:
        cleaned = (full_name or "").strip()
        if cleaned:
            return cleaned
        return email.split("@", 1)[0]

    @staticmethod
    def is_admin_email(email: str | None) -> bool:
        normalized = AuthService._normalize_email(email or "")
        if not normalized:
            return False
        allowlists = AuthService._role_email_allowlists()
        return normalized in allowlists["admin"] or normalized in allowlists["owner"]

    @staticmethod
    def is_owner_email(email: str | None) -> bool:
        normalized = AuthService._normalize_email(email or "")
        if not normalized:
            return False
        return normalized in AuthService._role_email_allowlists()["owner"]

    @staticmethod
    def is_support_email(email: str | None) -> bool:
        normalized = AuthService._normalize_email(email or "")
        if not normalized:
            return False
        return normalized in AuthService._role_email_allowlists()["support"]

    @staticmethod
    def effective_portal_role(user: User | None) -> str:
        if not user:
            return "user"
        role = AuthService._normalize_role(getattr(user, "role", "user"))
        email = getattr(user, "email", "") or ""
        if AuthService.is_owner_email(email):
            return "owner"
        if AuthService.is_admin_email(email):
            if role in {"owner", "admin"}:
                return role
            return "admin"
        if role in AuthService.ADMIN_PORTAL_ROLES:
            return role
        if AuthService.is_support_email(email):
            return "support"
        return role

    @staticmethod
    def has_admin_access(user: User | None, *, min_role: str = "support") -> bool:
        if not user:
            return False
        required = AuthService.ROLE_LEVELS.get(AuthService._normalize_role(min_role), 1)
        current = AuthService.ROLE_LEVELS.get(AuthService.effective_portal_role(user), 0)
        return current >= required

    @staticmethod
    def can_access_admin_panel(user: User | None) -> bool:
        return AuthService.has_admin_access(user, min_role="support")

    @staticmethod
    def sync_role_from_allowlists(user: User | None, *, commit: bool = True) -> str:
        if not user:
            return "user"
        desired_role = AuthService.effective_portal_role(user)
        if desired_role not in {"owner", "admin"}:
            return desired_role
        if AuthService._normalize_role(getattr(user, "role", "")) != desired_role:
            user.role = desired_role
            if commit:
                db.session.commit()
        return desired_role

    @staticmethod
    def should_grant_admin(user: User | None) -> bool:
        if not user:
            return False
        return AuthService.has_admin_access(user, min_role="admin")

    @staticmethod
    def _normalize_referral_code(referral_code: str | None) -> str:
        return (referral_code or "").strip().upper()

    @staticmethod
    def _generate_unique_referral_code() -> str:
        for _ in range(24):
            code = generate_referral_code()
            if not User.query.filter_by(referral_code=code).first():
                return code
        raise ValueError("Unable to generate unique referral code. Please retry.")

    @staticmethod
    def ensure_user_referral_code(user: User) -> str:
        if user.referral_code:
            return user.referral_code
        user.referral_code = AuthService._generate_unique_referral_code()
        db.session.commit()
        return user.referral_code

    @staticmethod
    def create_local_user(
        *,
        full_name: str,
        email: str,
        password: str,
        referral_code: str | None = None,
    ) -> User:
        normalized_email = AuthService._validate_email_address(email)
        AuthService._validate_password_strength(password)

        existing_user = User.query.filter_by(email=normalized_email).first()
        if existing_user and existing_user.is_verified:
            raise ValueError("An account with this email already exists.")
        if existing_user and existing_user.google_id:
            raise ValueError("This email is already linked with Google login. Use Google sign-in.")
        if existing_user and existing_user.is_verified and not existing_user.is_active:
            raise ValueError("This account is disabled.")

        normalized_referral = AuthService._normalize_referral_code(referral_code)
        if normalized_referral:
            referrer = User.query.filter_by(referral_code=normalized_referral).first()
            if not referrer:
                raise ValueError("Referral code is invalid.")
            if AuthService._normalize_email(referrer.email) == normalized_email:
                raise ValueError("Self referral is not allowed.")

        try:
            created = False
            if existing_user:
                user = existing_user
                user.full_name = AuthService._normalize_full_name(full_name, normalized_email)
                user.set_password(password)
                user.is_active = True
                user.is_verified = False
                user.last_login_at = None
                user.password_reset_key_hash = ""
                if not user.referred_by and normalized_referral:
                    user.referred_by = normalized_referral
            else:
                user = User(
                    full_name=AuthService._normalize_full_name(full_name, normalized_email),
                    email=normalized_email,
                    referral_code=AuthService._generate_unique_referral_code(),
                    referred_by=normalized_referral or None,
                    is_verified=False,
                    is_active=True,
                    last_login_at=None,
                )
                user.set_password(password)
                db.session.add(user)
                db.session.flush()
                created = True

            if AuthService.is_owner_email(user.email):
                user.role = "owner"
            elif AuthService.is_admin_email(user.email):
                user.role = "admin"
            db.session.commit()
        except Exception:
            db.session.rollback()
            current_app.logger.exception("Local signup failed for %s", normalized_email)
            raise

        AuthService._log_activity_safely(
            user.id,
            "user.signup.local.pending",
            "user",
            str(user.id),
            details={
                "provider": "local",
                "referred_by": normalized_referral or None,
                "created": created,
                "email_verified": False,
            },
        )
        return user

    @staticmethod
    def authenticate_local_user(email: str, password: str) -> User:
        normalized_email = AuthService._normalize_email(email)
        user = User.query.filter_by(email=normalized_email).first()
        if not user or not user.check_password(password):
            raise ValueError("Invalid email or password.")
        if not user.is_active:
            AuthService._log_activity_safely(
                user.id,
                "user.login.blocked.disabled",
                "user",
                str(user.id),
                details={"provider": "local"},
            )
            raise ValueError("This account is disabled.")
        if not user.is_verified:
            AuthService._log_activity_safely(
                user.id,
                "user.login.blocked.unverified",
                "user",
                str(user.id),
                details={"provider": "local"},
            )
            raise ValueError("Please verify your email before login.")

        AuthService.sync_role_from_allowlists(user, commit=False)
        user.last_login_at = utcnow()
        generated_key = AuthService.ensure_password_reset_key(user, commit=False)
        db.session.commit()
        if generated_key:
            setattr(user, "generated_password_reset_key", generated_key)

        AuthService._log_activity_safely(
            user.id,
            "user.login.local",
            "user",
            str(user.id),
            details={"provider": "local"},
        )
        return user

    @staticmethod
    def _as_utc(dt: datetime | None) -> datetime:
        if not dt:
            return utcnow()
        if dt.tzinfo is None:
            return dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)

    @staticmethod
    def _as_ist(dt: datetime | None) -> datetime:
        if not dt:
            return datetime.now(AuthService.IST_TZ)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(AuthService.IST_TZ)

    @staticmethod
    def _apply_referral_reward(new_user: User, referral_code: str | None) -> None:
        normalized_code = AuthService._normalize_referral_code(referral_code)
        if not normalized_code:
            return

        referrer = User.query.filter_by(referral_code=normalized_code).first()
        if not referrer:
            raise ValueError("Referral code is invalid.")
        if referrer.id == new_user.id:
            raise ValueError("Self referral is not allowed.")

        referrer.total_referrals = int(referrer.total_referrals or 0) + 1
        referral_credit_log = ActivityLog(
            user_id=referrer.id,
            action="referral.signup.credit",
            target_type="user",
            target_id=str(new_user.id),
            ip_address=request.headers.get("X-Forwarded-For", request.remote_addr or ""),
            user_agent=request.headers.get("User-Agent", ""),
            details_json={
                "referral_code": normalized_code,
                "total_referrals": referrer.total_referrals,
            },
        )
        db.session.add(referral_credit_log)
        reward_due = referrer.total_referrals % AuthService.REFERRAL_REWARD_STEP == 0
        if not reward_due:
            return

        now_ist = datetime.now(AuthService.IST_TZ)
        now_utc = now_ist.astimezone(timezone.utc)
        active_subscription = (
            UserSubscription.query.filter(
                UserSubscription.user_id == referrer.id,
                UserSubscription.status.in_(["active", "expiring_soon"]),
            )
            .order_by(UserSubscription.expires_at.desc())
            .first()
        )

        if active_subscription and AuthService._as_utc(active_subscription.expires_at) > now_utc:
            current_expiry_ist = AuthService._as_ist(active_subscription.expires_at)
            base_ist = current_expiry_ist if current_expiry_ist > now_ist else now_ist
            new_expiry_ist = base_ist + timedelta(days=AuthService.REFERRAL_REWARD_DAYS)
            active_subscription.expires_at = new_expiry_ist.astimezone(timezone.utc)
            active_subscription.status = "active"
            active_subscription.cancelled_at = None
            metadata = active_subscription.metadata_json or {}
            metadata["referral_reward_days"] = int(metadata.get("referral_reward_days", 0)) + 1
            metadata["last_referral_reward_ist"] = now_ist.isoformat()
            active_subscription.metadata_json = metadata
        else:
            reward_subscription = UserSubscription(
                user_id=referrer.id,
                plan_key="referral_reward",
                plan_name="Referral Reward",
                status="active",
                price_paise=0,
                started_at=now_utc,
                expires_at=(now_ist + timedelta(days=AuthService.REFERRAL_REWARD_DAYS)).astimezone(timezone.utc),
                metadata_json={
                    "source": "referral_reward",
                    "reward_days": AuthService.REFERRAL_REWARD_DAYS,
                    "awarded_at_ist": now_ist.isoformat(),
                },
            )
            db.session.add(reward_subscription)

        log = ActivityLog(
            user_id=referrer.id,
            action="referral.reward.applied",
            target_type="user",
            target_id=str(new_user.id),
            ip_address=request.headers.get("X-Forwarded-For", request.remote_addr or ""),
            user_agent=request.headers.get("User-Agent", ""),
            details_json={
                "referral_code": normalized_code,
                "total_referrals": referrer.total_referrals,
                "reward_days": AuthService.REFERRAL_REWARD_DAYS,
                "rule": "2=1",
            },
        )
        db.session.add(log)

    @staticmethod
    def _log_activity_safely(
        user_id: int | None,
        action: str,
        target_type: str,
        target_id: str,
        details: dict | None = None,
    ) -> None:
        try:
            AuthService.log_activity(
                user_id,
                action,
                target_type,
                target_id,
                details=details,
            )
        except Exception:
            db.session.rollback()
            current_app.logger.exception(
                "Activity log write failed. action=%s target_type=%s target_id=%s",
                action,
                target_type,
                target_id,
            )

    @staticmethod
    def upsert_google_user(
        *,
        email: str,
        full_name: str = "",
        google_id: str = "",
        profile_picture: str = "",
        email_verified: bool | None = None,
        referral_code: str | None = None,
    ) -> tuple[User, bool]:
        normalized_email = AuthService._normalize_email(email)
        cleaned_name = (full_name or "").strip()
        normalized_google_id = (google_id or "").strip()
        normalized_profile_picture = (profile_picture or "").strip()
        if not normalized_email:
            raise ValueError("Google account email is unavailable.")
        if email_verified is not True:
            raise ValueError("Google account email is not verified.")

        normalized_referral = AuthService._normalize_referral_code(referral_code)
        if normalized_referral:
            referrer = User.query.filter_by(referral_code=normalized_referral).first()
            if not referrer:
                current_app.logger.warning(
                    "Ignoring unknown referral code during Google sign-in. referral_code=%s email=%s",
                    normalized_referral,
                    normalized_email,
                )
                normalized_referral = ""
            elif AuthService._normalize_email(referrer.email) == normalized_email:
                current_app.logger.warning(
                    "Ignoring self-referral during Google sign-in. referral_code=%s email=%s",
                    normalized_referral,
                    normalized_email,
                )
                normalized_referral = ""

        user = None
        if normalized_google_id:
            user = User.query.filter_by(google_id=normalized_google_id).first()
        if not user:
            user = User.query.filter_by(email=normalized_email).first()
        created = False
        generated_key = ""
        try:
            if user:
                if not user.is_active:
                    raise ValueError("This account is disabled.")
                if cleaned_name and not user.full_name:
                    user.full_name = cleaned_name
                if normalized_google_id and user.google_id != normalized_google_id:
                    user.google_id = normalized_google_id
                if normalized_profile_picture:
                    user.profile_picture = normalized_profile_picture
            else:
                user = User(
                    full_name=cleaned_name or normalized_email.split("@", 1)[0],
                    email=normalized_email,
                    google_id=normalized_google_id or None,
                    profile_picture=normalized_profile_picture,
                    referral_code=AuthService._generate_unique_referral_code(),
                    referred_by=normalized_referral or None,
                    is_verified=True,
                    is_active=True,
                    last_login_at=utcnow(),
                )
                user.set_password(secrets.token_urlsafe(24))
                generated_key = AuthService.generate_password_reset_key()
                AuthService.set_password_reset_key(
                    user,
                    generated_key,
                    icon_name=AuthService._default_reset_icon(),
                    commit=False,
                    log_activity=False,
                )
                if AuthService.is_owner_email(user.email):
                    user.role = "owner"
                elif AuthService.is_admin_email(user.email):
                    user.role = "admin"
                db.session.add(user)
                db.session.flush()
                if normalized_referral:
                    AuthService._apply_referral_reward(user, normalized_referral)
                created = True

            AuthService.sync_role_from_allowlists(user, commit=False)
            user.last_login_at = utcnow()
            if not user.is_verified:
                user.is_verified = True
            if not generated_key:
                generated_key = AuthService.ensure_password_reset_key(user, commit=False)
            db.session.commit()
        except Exception:
            db.session.rollback()
            current_app.logger.exception(
                "Google user sync failed for %s",
                normalized_email,
            )
            raise

        AuthService._log_activity_safely(
            user.id,
            "user.signup.google" if created else "user.login.google",
            "user",
            str(user.id),
            details={
                "provider": "google",
                "created": created,
                "google_id": normalized_google_id or None,
                "referred_by": normalized_referral or None,
            },
        )
        if generated_key:
            setattr(user, "generated_password_reset_key", generated_key)
        return user, created

    @staticmethod
    def log_activity(
        user_id: int | None,
        action: str,
        target_type: str,
        target_id: str,
        details: dict | None = None,
    ) -> None:
        log = ActivityLog(
            user_id=user_id,
            action=action,
            target_type=target_type,
            target_id=target_id,
            ip_address=request.headers.get("X-Forwarded-For", request.remote_addr or ""),
            user_agent=request.headers.get("User-Agent", ""),
            details_json=details or {},
        )
        db.session.add(log)
        db.session.commit()


def admin_required(view_func=None, *, min_role: str = "admin"):
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            if not current_user.is_authenticated:
                abort(401)
            role = AuthService.sync_role_from_allowlists(current_user, commit=True)
            if not AuthService.has_admin_access(current_user, min_role=min_role):
                abort(403)
            session["is_admin_session"] = True
            session["admin_role"] = role
            return func(*args, **kwargs)

        return wrapper

    if view_func is None:
        return decorator
    return decorator(view_func)

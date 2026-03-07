from __future__ import annotations

import secrets
from datetime import datetime, timedelta, timezone
from functools import wraps

import pytz
from flask import abort, current_app, request
from flask_login import current_user
from flask_mail import Message
from werkzeug.security import check_password_hash, generate_password_hash

from app.extensions import db, mail
from app.models import (
    ActivityLog,
    EmailOTPChallenge,
    PasswordResetToken,
    User,
    UserSubscription,
    generate_referral_code,
    utcnow,
)


class AuthService:
    OTP_PURPOSE_SIGNUP = "signup"
    OTP_PURPOSE_LOGIN = "login"
    REFERRAL_REWARD_STEP = 2
    REFERRAL_REWARD_DAYS = 1
    IST_TZ = pytz.timezone("Asia/Kolkata")

    @staticmethod
    def _normalize_email(email: str) -> str:
        return (email or "").strip().lower()

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
    def _missing_mail_config_keys() -> list[str]:
        missing = []
        for key in ("MAIL_SERVER", "MAIL_USERNAME", "MAIL_PASSWORD"):
            value = (current_app.config.get(key) or "").strip()
            if not value:
                missing.append(key)
        mail_port = current_app.config.get("MAIL_PORT", 0)
        if not isinstance(mail_port, int) or mail_port <= 0:
            missing.append("MAIL_PORT")
        return missing

    @staticmethod
    def _generate_otp_code() -> str:
        return f"{secrets.randbelow(1_000_000):06d}"

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
                UserSubscription.status == "active",
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
    def _send_otp_email(email: str, otp_code: str, purpose: str) -> None:
        missing = AuthService._missing_mail_config_keys()
        if missing:
            raise ValueError("Unable to send OTP. Please check server settings.")
        ttl_minutes = int(current_app.config["OTP_TTL_MINUTES"])
        app_name = current_app.config["APP_NAME"]
        product_name = "PDF Master Ultra Suite"
        if purpose == AuthService.OTP_PURPOSE_SIGNUP:
            subject = f"{app_name} Signup Verification Code"
        else:
            subject = f"{app_name} Login Verification Code"
        body = (
            f"Welcome to {product_name}.\n\n"
            "To ensure the security of your account, please use the following "
            "One-Time Password (OTP) to verify your email address.\n\n"
            f"OTP: {otp_code}\n\n"
            f"For your security, this code will expire in exactly {ttl_minutes} minutes. "
            "If you did not initiate this request, please safely ignore this email."
        )
        html = f"""
<!doctype html>
<html lang="en">
  <body style="margin:0;padding:0;background:#f4f7f6;font-family:Arial,Helvetica,system-ui,sans-serif;">
    <table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="background:#f4f7f6;padding:28px 12px;">
      <tr>
        <td align="center">
          <table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="max-width:620px;background:#ffffff;border-radius:16px;box-shadow:0 10px 30px rgba(17,24,39,0.10);overflow:hidden;">
            <tr>
              <td style="padding:28px 34px 16px 34px;text-align:center;border-bottom:1px solid #edf1f0;">
                <div style="font-size:30px;line-height:1.2;font-weight:800;letter-spacing:0.2px;color:#116e54;">
                  PDF Master Ultra Suite
                </div>
              </td>
            </tr>
            <tr>
              <td style="padding:26px 34px 10px 34px;color:#1f2937;font-size:16px;line-height:1.7;">
                Welcome to PDF Master Ultra Suite. To ensure the security of your account, please use the following
                One-Time Password (OTP) to verify your email address.
              </td>
            </tr>
            <tr>
              <td style="padding:14px 34px 12px 34px;">
                <div style="background:#e8f2ff;border:1px solid #bfd6ff;border-radius:12px;padding:18px 14px;text-align:center;color:#0f3266;font-weight:800;font-size:34px;letter-spacing:10px;line-height:1;user-select:all;">
                  {otp_code}
                </div>
              </td>
            </tr>
            <tr>
              <td style="padding:16px 34px 30px 34px;color:#6b7280;font-size:13px;line-height:1.7;">
                For your security, this code will expire in exactly {ttl_minutes} minutes.
                If you did not initiate this request, please safely ignore this email.
              </td>
            </tr>
          </table>
        </td>
      </tr>
    </table>
  </body>
</html>
"""
        try:
            message = Message(subject=subject, recipients=[email], body=body, html=html)
            mail.send(message)
        except Exception as e:
            print(e)
            current_app.logger.exception("OTP email delivery failed for %s", email)
            raise ValueError("Unable to send OTP. Please check server settings.")

    @staticmethod
    def _issue_otp_challenge(email: str, purpose: str, payload: dict | None = None) -> EmailOTPChallenge:
        otp_code = AuthService._generate_otp_code()
        now = utcnow()
        try:
            active_challenges = EmailOTPChallenge.query.filter(
                EmailOTPChallenge.email == email,
                EmailOTPChallenge.purpose == purpose,
                EmailOTPChallenge.used_at.is_(None),
            ).all()
            for challenge in active_challenges:
                challenge.used_at = now

            challenge = EmailOTPChallenge(
                purpose=purpose,
                email=email,
                token=secrets.token_urlsafe(42),
                otp_hash=generate_password_hash(otp_code),
                payload_json=payload or {},
                expires_at=now + timedelta(minutes=int(current_app.config["OTP_TTL_MINUTES"])),
                max_attempts=int(current_app.config["OTP_MAX_ATTEMPTS"]),
                attempt_count=0,
            )
            db.session.add(challenge)
            AuthService._send_otp_email(email, otp_code, purpose)
            db.session.commit()
            return challenge
        except Exception:
            db.session.rollback()
            raise

    @staticmethod
    def start_signup_otp(
        full_name: str,
        email: str,
        password: str,
        referral_code: str | None = None,
    ) -> EmailOTPChallenge:
        normalized_email = AuthService._normalize_email(email)
        cleaned_name = (full_name or "").strip()
        if not cleaned_name:
            raise ValueError("Full name is required.")
        if len(cleaned_name) < 2:
            raise ValueError("Full name must be at least 2 characters.")
        if not normalized_email:
            raise ValueError("Email is required.")
        if User.query.filter_by(email=normalized_email).first():
            raise ValueError("An account with that email already exists.")
        raw_password = (password or "").strip()
        if len(raw_password) < 8:
            raise ValueError("Password must be at least 8 characters.")
        normalized_referral = AuthService._normalize_referral_code(referral_code)
        if normalized_referral:
            referrer = User.query.filter_by(referral_code=normalized_referral).first()
            if not referrer:
                raise ValueError("Referral code is invalid.")
            if AuthService._normalize_email(referrer.email) == normalized_email:
                raise ValueError("You cannot use your own referral code.")

        payload = {
            "full_name": cleaned_name,
            "password_hash": generate_password_hash(raw_password),
            "referral_code": normalized_referral,
        }
        challenge = AuthService._issue_otp_challenge(
            email=normalized_email,
            purpose=AuthService.OTP_PURPOSE_SIGNUP,
            payload=payload,
        )
        AuthService.log_activity(None, "otp.signup.sent", "email", normalized_email)
        return challenge

    @staticmethod
    def start_login_otp(email: str) -> EmailOTPChallenge:
        normalized_email = AuthService._normalize_email(email)
        user = User.query.filter_by(email=normalized_email).first()
        if not user:
            raise ValueError("No account found for that email.")
        if not user.is_active:
            raise ValueError("This account is disabled.")

        challenge = AuthService._issue_otp_challenge(
            email=normalized_email,
            purpose=AuthService.OTP_PURPOSE_LOGIN,
            payload={"user_id": user.id},
        )
        AuthService.log_activity(user.id, "otp.login.sent", "user", str(user.id))
        return challenge

    @staticmethod
    def get_active_otp_challenge(token: str, purpose: str) -> EmailOTPChallenge:
        challenge = EmailOTPChallenge.query.filter_by(
            token=(token or "").strip(),
            purpose=(purpose or "").strip().lower(),
        ).first()
        if not challenge:
            raise ValueError("OTP session not found.")
        if challenge.used_at:
            raise ValueError("OTP session already used. Request a new OTP.")
        if AuthService._as_utc(challenge.expires_at) <= utcnow():
            challenge.used_at = utcnow()
            db.session.commit()
            raise ValueError("OTP expired. Request a new OTP.")
        if challenge.attempt_count >= challenge.max_attempts:
            challenge.used_at = utcnow()
            db.session.commit()
            raise ValueError("Maximum OTP attempts exceeded. Request a new OTP.")
        return challenge

    @staticmethod
    def verify_otp(token: str, purpose: str, otp_input: str) -> User:
        challenge = AuthService.get_active_otp_challenge(token, purpose)
        otp_code = (otp_input or "").strip()
        if not otp_code.isdigit() or len(otp_code) != 6:
            raise ValueError("Enter a valid 6-digit OTP.")
        if not check_password_hash(challenge.otp_hash, otp_code):
            challenge.attempt_count += 1
            remaining = max(0, challenge.max_attempts - challenge.attempt_count)
            if challenge.attempt_count >= challenge.max_attempts:
                challenge.used_at = utcnow()
            db.session.commit()
            if remaining <= 0:
                raise ValueError("OTP is incorrect. Maximum attempts reached.")
            raise ValueError(f"OTP is incorrect. {remaining} attempt(s) remaining.")

        purpose_key = challenge.purpose
        if purpose_key == AuthService.OTP_PURPOSE_SIGNUP:
            if User.query.filter_by(email=challenge.email).first():
                challenge.used_at = utcnow()
                db.session.commit()
                raise ValueError("An account with this email already exists. Please login.")
            payload = challenge.payload_json or {}
            full_name = (payload.get("full_name") or "").strip()
            password_hash = (payload.get("password_hash") or "").strip()
            if not full_name or not password_hash:
                challenge.used_at = utcnow()
                db.session.commit()
                raise ValueError("Signup session is invalid. Please try again.")
            user = User(
                full_name=full_name,
                email=challenge.email,
                password_hash=password_hash,
                referral_code=AuthService._generate_unique_referral_code(),
                referred_by=(payload.get("referral_code") or "").strip() or None,
                is_verified=True,
                is_active=True,
                last_login_at=utcnow(),
            )
            db.session.add(user)
            db.session.flush()
            AuthService._apply_referral_reward(user, payload.get("referral_code"))
            challenge.used_at = utcnow()
            db.session.commit()
            AuthService.log_activity(user.id, "user.signup.otp_verified", "user", str(user.id))
            return user

        if purpose_key == AuthService.OTP_PURPOSE_LOGIN:
            user_id = int((challenge.payload_json or {}).get("user_id") or 0)
            user = db.session.get(User, user_id)
            if not user:
                challenge.used_at = utcnow()
                db.session.commit()
                raise ValueError("User not found for this OTP session.")
            if not user.is_active:
                challenge.used_at = utcnow()
                db.session.commit()
                raise ValueError("This account is disabled.")
            user.last_login_at = utcnow()
            if not user.is_verified:
                user.is_verified = True
            challenge.used_at = utcnow()
            db.session.commit()
            AuthService.log_activity(user.id, "user.login.otp_verified", "user", str(user.id))
            return user

        challenge.used_at = utcnow()
        db.session.commit()
        raise ValueError("Unsupported OTP session.")

    @staticmethod
    def register_user(full_name: str, email: str, password: str) -> User:
        email = AuthService._normalize_email(email)
        if User.query.filter_by(email=email).first():
            raise ValueError("An account with that email already exists.")
        user = User(full_name=full_name.strip(), email=email)
        user.referral_code = AuthService._generate_unique_referral_code()
        user.set_password(password)
        db.session.add(user)
        db.session.commit()
        AuthService.log_activity(user.id, "user.signup", "user", str(user.id))
        return user

    @staticmethod
    def authenticate(email: str, password: str) -> User:
        user = User.query.filter_by(email=AuthService._normalize_email(email)).first()
        if not user or not user.check_password(password):
            raise ValueError("Invalid email or password.")
        if not user.is_active:
            raise ValueError("This account is disabled.")
        user.last_login_at = utcnow()
        db.session.commit()
        AuthService.log_activity(user.id, "user.login", "user", str(user.id))
        return user

    @staticmethod
    def create_reset_token(email: str) -> PasswordResetToken:
        user = User.query.filter_by(email=AuthService._normalize_email(email)).first()
        if not user:
            raise ValueError("No user found for that email.")
        token = PasswordResetToken(
            user_id=user.id,
            token=secrets.token_urlsafe(32),
            expires_at=utcnow()
            + timedelta(minutes=current_app.config["PASSWORD_RESET_TTL_MINUTES"]),
        )
        db.session.add(token)
        db.session.commit()
        AuthService.log_activity(user.id, "password.reset.requested", "user", str(user.id))
        return token

    @staticmethod
    def reset_password(token_value: str, new_password: str) -> User:
        token = PasswordResetToken.query.filter_by(token=token_value).first()
        if not token or token.used_at or AuthService._as_utc(token.expires_at) < utcnow():
            raise ValueError("Password reset link is invalid or expired.")
        user = token.user
        user.set_password(new_password)
        token.used_at = utcnow()
        db.session.commit()
        AuthService.log_activity(user.id, "password.reset.completed", "user", str(user.id))
        return user

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


def admin_required(view_func):
    @wraps(view_func)
    def wrapper(*args, **kwargs):
        if not current_user.is_authenticated:
            abort(401)
        if not current_user.is_admin:
            abort(403)
        return view_func(*args, **kwargs)

    return wrapper

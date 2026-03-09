from __future__ import annotations

import hashlib

from flask import (
    Blueprint,
    current_app,
    flash,
    redirect,
    render_template,
    request,
    session,
    url_for,
)
from flask_login import current_user, login_required, login_user, logout_user
from markupsafe import Markup, escape

from app.extensions import limiter, oauth
from app.services.auth_service import AuthService
from app.services.notification_service import NotificationService
from app.services.url_service import UrlService

auth_bp = Blueprint("auth", __name__)


def _safe_next_url(raw: str | None) -> str:
    candidate = (raw or "").strip()
    if candidate.startswith("/") and not candidate.startswith("//"):
        return candidate
    return url_for("main.dashboard")


def _is_google_oauth_configured() -> bool:
    return bool(
        not getattr(oauth, "is_stub", False)
        and (current_app.config.get("GOOGLE_CLIENT_ID") or "").strip()
        and (current_app.config.get("GOOGLE_CLIENT_SECRET") or "").strip()
    )


def _sync_admin_session(user) -> None:
    session.permanent = True
    session["is_admin_session"] = bool(AuthService.should_grant_admin(user))


def _normalize_referral_arg(raw: str | None) -> str:
    return (raw or "").strip().upper()


def _build_google_entry_url(next_url: str, referral_code: str = "") -> str:
    if not _is_google_oauth_configured():
        return url_for("auth.login")

    params: dict[str, str] = {}
    if next_url and next_url != url_for("main.dashboard"):
        params["next"] = next_url
    if referral_code:
        params["ref"] = referral_code
    return url_for("auth.google_login", **params)


def _build_google_callback_url() -> str:
    return UrlService.build_external_url("auth.google_auth")


def _google_setup_help() -> str:
    if _is_google_oauth_configured():
        return ""
    if getattr(oauth, "is_stub", False):
        return "Google sign-in is unavailable because Authlib is not installed on this server."
    return "Google sign-in is not configured yet. Set GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET."


def _password_reset_ttl_seconds() -> int:
    return int(current_app.config.get("PASSWORD_RESET_TOKEN_TTL_SECONDS") or 3600)


def _email_verification_ttl_seconds() -> int:
    raw_ttl = int(current_app.config.get("EMAIL_VERIFICATION_TOKEN_TTL_SECONDS") or 86400)
    return max(300, raw_ttl)


def _coerce_bool(value) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes", "on"}:
            return True
        if normalized in {"0", "false", "no", "off"}:
            return False
    if value is None:
        return None
    return bool(value)


def _send_email_verification_link(user, *, next_url: str = "") -> tuple[bool, str]:
    token = AuthService.generate_email_verification_token(getattr(user, "email", ""))
    verify_url = UrlService.build_external_url("auth.verify_email", token=token)
    app_name = current_app.config.get("APP_NAME", "PDFMaster Ultra Suite")
    ttl_seconds = _email_verification_ttl_seconds()
    ttl_minutes = max(1, ttl_seconds // 60)
    safe_next = _safe_next_url(next_url)
    login_url = (
        UrlService.build_external_url("auth.login", next=safe_next)
        if safe_next and safe_next != url_for("main.dashboard")
        else UrlService.build_external_url("auth.login")
    )
    body_text = (
        f"Hello {getattr(user, 'full_name', '') or 'User'},\n\n"
        f"Please verify your email to activate your {app_name} account.\n\n"
        f"Verify now: {verify_url}\n\n"
        f"This link expires in {ttl_minutes} minute(s).\n"
        f"After verification, login here: {login_url}\n\n"
        "If you did not create this account, you can ignore this email."
    )
    body_html = (
        f"<p>Hello {escape(getattr(user, 'full_name', '') or 'User')},</p>"
        f"<p>Please verify your email to activate your <strong>{escape(app_name)}</strong> account.</p>"
        f"<p><a href=\"{escape(verify_url)}\">Verify email</a></p>"
        f"<p>This link expires in {ttl_minutes} minute(s).</p>"
        "<p>If you did not create this account, you can ignore this email.</p>"
    )
    return NotificationService.send_email(
        to_email=getattr(user, "email", ""),
        subject=f"Verify your email - {app_name}",
        body_text=body_text,
        body_html=body_html,
    )


def _password_reset_help_email() -> str:
    configured = (current_app.config.get("PASSWORD_RESET_HELP_EMAIL") or "").strip()
    if configured:
        return configured
    admin_email = (current_app.config.get("ADMIN_EMAIL") or "").strip()
    if admin_email:
        return admin_email
    return "pdfmasterultrasuite@gmail.com"


def _password_reset_help_link(account_email: str = "") -> str:
    normalized_email = (account_email or "").strip().lower()
    subject = f"{current_app.config['APP_NAME']} - Help to forget key"
    body_lines = [
        "Hello Admin,",
        "",
        "I forgot my 4-digit password reset key. Please help me regain access.",
    ]
    if normalized_email:
        body_lines.append(f"Account email: {normalized_email}")
    body_lines.extend(
        [
            "Reason: ",
            "",
            "Thanks.",
        ]
    )
    return UrlService.gmail_compose_url(
        to_email=_password_reset_help_email(),
        subject=subject,
        body="\n".join(body_lines),
    )


def _password_reset_verified_session_key(user_id: int, token: str) -> str:
    token_value = (token or "").strip().encode("utf-8")
    digest = hashlib.sha256(token_value).hexdigest()[:24]
    return f"password_reset_verified:{int(user_id)}:{digest}"


def _render_google_auth_page(mode: str):
    next_url = _safe_next_url(request.args.get("next") or request.form.get("next"))
    referral_code = ""
    if mode == "signup":
        referral_code = _normalize_referral_arg(
            request.args.get("ref")
            or request.args.get("referral_code")
            or request.form.get("referral_code")
            or session.get("pending_referral_code")
        )
        if referral_code:
            session["pending_referral_code"] = referral_code
        else:
            session.pop("pending_referral_code", None)
    else:
        session.pop("pending_referral_code", None)

    return render_template(
        f"auth/{mode}.html",
        google_oauth_enabled=_is_google_oauth_configured(),
        google_login_url=_build_google_entry_url(next_url, referral_code),
        google_setup_help=_google_setup_help(),
        email_auth_enabled=True,
        next_url=next_url,
        referral_code=referral_code,
    )


@auth_bp.route("/signup", methods=["GET", "POST"])
@limiter.limit("3 per minute", methods=["POST"])
def signup():
    if current_user.is_authenticated:
        return redirect(url_for("main.dashboard"))
    if request.method == "POST":
        next_url = _safe_next_url(request.form.get("next"))
        password = (request.form.get("password") or "").strip()
        confirm_password = (request.form.get("confirm_password") or "").strip()
        referral_code = _normalize_referral_arg(request.form.get("referral_code"))
        if not NotificationService.is_email_enabled():
            flash(
                "Email signup is unavailable because verification email is not configured. Please use Google login or configure SMTP.",
                "danger",
            )
            return _render_google_auth_page("signup")
        if password != confirm_password:
            flash("Password and confirm password do not match.", "danger")
            return _render_google_auth_page("signup")
        try:
            user = AuthService.create_local_user(
                full_name=request.form.get("full_name", ""),
                email=request.form.get("email", ""),
                password=password,
                referral_code=referral_code or session.get("pending_referral_code"),
            )
            session.pop("pending_referral_code", None)
            delivered, reason = _send_email_verification_link(user, next_url=next_url)
            if delivered:
                flash("Account created. Verification link has been sent to your email.", "success")
            else:
                current_app.logger.warning(
                    "Failed to send verification email. email=%s reason=%s",
                    getattr(user, "email", ""),
                    reason,
                )
                flash(
                    "Account created but verification email could not be sent. Use 'Resend verification email' on login page.",
                    "warning",
                )
            return redirect(url_for("auth.login", next=next_url))
        except Exception as exc:
            current_app.logger.exception("Local signup failed")
            flash(str(exc), "danger")
    return _render_google_auth_page("signup")


@auth_bp.route("/login", methods=["GET", "POST"])
@limiter.limit("5 per minute", methods=["POST"])
def login():
    if current_user.is_authenticated:
        return redirect(url_for("main.dashboard"))
    if request.method == "POST":
        next_url = _safe_next_url(request.form.get("next"))
        action = (request.form.get("action") or "login").strip().lower()
        if action == "resend_verification":
            from app.models import User

            email = AuthService._normalize_email(request.form.get("email", ""))
            if not email:
                flash("Enter your email to resend verification link.", "warning")
                return _render_google_auth_page("login")
            pending_user = User.query.filter_by(
                email=email,
                is_active=True,
                is_verified=False,
            ).first()
            if pending_user:
                delivered, reason = _send_email_verification_link(pending_user, next_url=next_url)
                if not delivered:
                    current_app.logger.warning(
                        "Resend verification failed. email=%s reason=%s",
                        email,
                        reason,
                    )
                    flash("Verification email could not be sent right now. Please try again.", "warning")
                    return _render_google_auth_page("login")
            flash("If your account is pending verification, a new verification email has been sent.", "info")
            return _render_google_auth_page("login")
        try:
            user = AuthService.authenticate_local_user(
                email=request.form.get("email", ""),
                password=request.form.get("password", ""),
            )
            login_user(user, remember=True)
            _sync_admin_session(user)
            flash("Logged in successfully.", "success")
            generated_key = getattr(user, "generated_password_reset_key", "")
            if generated_key:
                flash(
                    Markup(
                        "New 4-digit Password Reset Key generated for your account: "
                        f"<strong>{escape(generated_key)}</strong>. "
                        "Please save it in Profile settings."
                    ),
                    "warning",
                )
            return redirect(next_url)
        except Exception as exc:
            message = str(exc)
            if message == "Please verify your email before login.":
                from app.models import User

                email = AuthService._normalize_email(request.form.get("email", ""))
                pending_user = User.query.filter_by(
                    email=email,
                    is_active=True,
                    is_verified=False,
                ).first()
                if pending_user:
                    delivered, reason = _send_email_verification_link(pending_user, next_url=next_url)
                    if delivered:
                        flash("Verification email resent. Please check your inbox/spam.", "info")
                    else:
                        current_app.logger.warning(
                            "Auto resend verification failed. email=%s reason=%s",
                            email,
                            reason,
                        )
            flash(message, "danger")
    return _render_google_auth_page("login")


@auth_bp.route("/forgot-password", methods=["GET", "POST"])
@limiter.limit("3 per 10 minute", methods=["POST"])
def forgot_password():
    if current_user.is_authenticated:
        return redirect(url_for("main.dashboard"))

    if request.method == "POST":
        from app.models import User

        email = (request.form.get("email") or "").strip().lower()
        if email:
            session["password_reset_prefill_email"] = email
        else:
            session.pop("password_reset_prefill_email", None)
        secret_key = "".join(ch for ch in (request.form.get("reset_key_optional") or "").strip() if ch.isdigit())
        if len(secret_key) == 4:
            session["password_reset_prefill_key"] = secret_key
        else:
            session.pop("password_reset_prefill_key", None)
            flash("Secret Key must be exactly 4 digits.", "warning")
            return redirect(url_for("auth.forgot_password"))

        generic_message = "Unable to verify account with the provided email and Secret Key."

        if email:
            try:
                AuthService.validate_email_address(email)
                account = User.query.filter_by(
                    email=AuthService._normalize_email(email),
                    is_active=True,
                    is_verified=True,
                ).first()

                if account:
                    if not AuthService.verify_password_reset_key(account, secret_key):
                        flash(generic_message, "danger")
                        return redirect(url_for("auth.forgot_password"))
                    token = AuthService.generate_password_reset_token(account.email)
                    verification_key = _password_reset_verified_session_key(account.id, token)
                    session[verification_key] = True
                    session["password_reset_prefill_key"] = secret_key
                    flash("Secret Key verified. Set your new password now.", "success")
                    return redirect(url_for("auth.reset_password", token=token))
            except ValueError:
                pass
            except Exception:
                current_app.logger.exception("Password reset request processing failed.")

        flash(generic_message, "danger")
        return redirect(url_for("auth.forgot_password"))

    return render_template(
        "auth/forgot_password.html",
        email_auth_enabled=True,
        password_reset_help_email=_password_reset_help_email(),
        password_reset_help_link=_password_reset_help_link(),
        prefill_reset_email=(session.get("password_reset_prefill_email") or ""),
        prefill_reset_key=(session.get("password_reset_prefill_key") or ""),
    )


@auth_bp.route("/reset-password/<token>", methods=["GET", "POST"])
def reset_password(token: str):
    if current_user.is_authenticated:
        return redirect(url_for("main.dashboard"))

    ttl_seconds = _password_reset_ttl_seconds()
    user = AuthService.resolve_user_from_password_reset_token(token, ttl_seconds)
    if not user:
        flash("This password reset link is invalid or has expired.", "danger")
        return redirect(url_for("auth.forgot_password"))

    help_link = _password_reset_help_link(getattr(user, "email", ""))
    prefill_reset_key = (session.get("password_reset_prefill_key") or "")
    verification_session_key = _password_reset_verified_session_key(user.id, token)
    key_verified = bool(session.get(verification_session_key))
    if request.method == "POST":
        if not key_verified:
            reset_key = (request.form.get("reset_key") or "").strip()
            digits_only_key = "".join(ch for ch in reset_key if ch.isdigit())
            prefill_reset_key = digits_only_key
            if len(digits_only_key) != 4:
                session.pop("password_reset_prefill_key", None)
                flash("Secret Key must be exactly 4 digits.", "danger")
                return render_template(
                    "auth/reset_password.html",
                    token=token,
                    password_reset_help_email=_password_reset_help_email(),
                    password_reset_help_link=help_link,
                    prefill_reset_key=prefill_reset_key,
                    key_verified=False,
                )
            if not AuthService.verify_password_reset_key(user, digits_only_key):
                flash("Invalid 4-digit Password Reset Key.", "danger")
                return render_template(
                    "auth/reset_password.html",
                    token=token,
                    password_reset_help_email=_password_reset_help_email(),
                    password_reset_help_link=help_link,
                    prefill_reset_key=prefill_reset_key,
                    key_verified=False,
                )
            session["password_reset_prefill_key"] = digits_only_key
            session[verification_session_key] = True
            key_verified = True
            flash("Secret Key verified. Now set your new password.", "success")
            return render_template(
                "auth/reset_password.html",
                token=token,
                password_reset_help_email=_password_reset_help_email(),
                password_reset_help_link=help_link,
                prefill_reset_key=prefill_reset_key,
                key_verified=key_verified,
            )

        password = (request.form.get("password") or "").strip()
        confirm_password = (request.form.get("confirm_password") or "").strip()
        if password != confirm_password:
            flash("Password and confirm password do not match.", "danger")
            return render_template(
                "auth/reset_password.html",
                token=token,
                password_reset_help_email=_password_reset_help_email(),
                password_reset_help_link=help_link,
                prefill_reset_key=prefill_reset_key,
                key_verified=key_verified,
            )
        try:
            AuthService.update_password(user, password)
            session.pop("password_reset_prefill_email", None)
            session.pop("password_reset_prefill_key", None)
            session.pop(verification_session_key, None)
            flash("Password updated successfully. Please log in.", "success")
            return redirect(url_for("auth.login"))
        except Exception as exc:
            current_app.logger.exception("Password reset update failed")
            flash(str(exc), "danger")

    return render_template(
        "auth/reset_password.html",
        token=token,
        password_reset_help_email=_password_reset_help_email(),
        password_reset_help_link=help_link,
        prefill_reset_key=prefill_reset_key,
        key_verified=key_verified,
    )


@auth_bp.route("/request-reset")
def request_reset_legacy():
    return redirect(url_for("auth.forgot_password"), code=302)


@auth_bp.route("/google-login")
@limiter.limit("20 per minute")
def google_login():
    if current_user.is_authenticated:
        return redirect(url_for("main.dashboard"))
    if not _is_google_oauth_configured():
        flash("Google login is not configured yet.", "danger")
        return redirect(url_for("auth.login"))

    next_url = _safe_next_url(request.args.get("next"))
    referral_code = _normalize_referral_arg(request.args.get("ref") or request.args.get("referral_code"))
    session["google_oauth_next"] = next_url
    if referral_code:
        session["pending_referral_code"] = referral_code

    redirect_uri = _build_google_callback_url()
    session["google_oauth_redirect_uri"] = redirect_uri
    try:
        current_app.logger.info("Starting Google OAuth redirect. redirect_uri=%s", redirect_uri)
        return oauth.google.authorize_redirect(redirect_uri, prompt="select_account")
    except Exception:
        current_app.logger.exception("Google OAuth redirect failed")
        flash("Google sign-in could not be started. Please try again.", "danger")
        return redirect(url_for("auth.login"))


@auth_bp.route("/google-auth")
@limiter.limit("20 per minute")
def google_auth():
    if current_user.is_authenticated:
        return redirect(url_for("main.dashboard"))
    if not _is_google_oauth_configured():
        flash("Google login is not configured yet.", "danger")
        return redirect(url_for("auth.login"))

    try:
        redirect_uri = session.pop("google_oauth_redirect_uri", "") or _build_google_callback_url()
        token = oauth.google.authorize_access_token(redirect_uri=redirect_uri)
        user_info = token.get("userinfo") if token else None
        if not user_info:
            user_info = oauth.google.parse_id_token(token)
        if not user_info:
            response = oauth.google.get("userinfo")
            if response.ok:
                user_info = response.json()
        if not user_info:
            raise ValueError("Unable to fetch Google profile.")

        email = (user_info.get("email") or "").strip().lower()
        full_name = (user_info.get("name") or "").strip()
        google_id = (user_info.get("sub") or user_info.get("id") or "").strip()
        profile_picture = (user_info.get("picture") or "").strip()
        email_verified = _coerce_bool(user_info.get("email_verified"))
        if email_verified is None:
            email_verified = _coerce_bool(user_info.get("verified_email"))
        referral_code = session.get("pending_referral_code")
        user, created = AuthService.upsert_google_user(
            email=email,
            full_name=full_name,
            google_id=google_id,
            profile_picture=profile_picture,
            email_verified=email_verified,
            referral_code=referral_code,
        )
        session.pop("pending_referral_code", None)
        login_user(user, remember=True)
        _sync_admin_session(user)
        flash(
            "Account created successfully with Google." if created else "Logged in with Google.",
            "success",
        )
        generated_key = getattr(user, "generated_password_reset_key", "")
        if generated_key:
            flash(
                Markup(
                    "Your 4-digit Password Reset Key is "
                    f"<strong>{escape(generated_key)}</strong>. "
                    "Save it from Profile settings."
                ),
                "warning",
            )
    except Exception:
        current_app.logger.exception("Google OAuth callback failed")
        flash("Google sign-in failed. Please try again.", "danger")
        return redirect(url_for("auth.login"))

    next_url = _safe_next_url(session.pop("google_oauth_next", None))
    return redirect(next_url)


@auth_bp.route("/verify-email/<token>")
@limiter.limit("15 per minute")
def verify_email(token: str):
    if current_user.is_authenticated:
        return redirect(url_for("main.dashboard"))

    user = AuthService.resolve_user_from_email_verification_token(
        token,
        _email_verification_ttl_seconds(),
    )
    if not user:
        flash("This email verification link is invalid or expired.", "danger")
        return redirect(url_for("auth.login"))

    try:
        already_verified = bool(user.is_verified)
        AuthService.mark_email_verified(user)
        if already_verified:
            flash("Email is already verified. Please login.", "info")
        else:
            flash("Email verified successfully. You can now login.", "success")
    except Exception as exc:
        current_app.logger.exception("Email verification failed")
        flash(str(exc), "danger")
    return redirect(url_for("auth.login"))


@auth_bp.route("/google-callback")
def google_auth_legacy():
    current_app.logger.info("Redirecting legacy Google callback to canonical auth callback.")
    return redirect(url_for("auth.google_auth", **request.args), code=302)

@auth_bp.route("/logout", methods=["POST"])
@login_required
def logout():
    session.pop("is_admin_session", None)
    session.pop("google_oauth_next", None)
    session.pop("google_oauth_redirect_uri", None)
    session.pop("pending_referral_code", None)
    logout_user()
    flash("You have been signed out.", "success")
    return redirect(url_for("main.landing"))

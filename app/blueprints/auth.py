from __future__ import annotations

import secrets
from datetime import timezone

import pytz

from flask import Blueprint, current_app, flash, redirect, render_template, request, session, url_for
from flask_login import current_user, login_required, login_user, logout_user

from app.extensions import db, oauth
from app.models import User, utcnow
from app.services.auth_service import AuthService

auth_bp = Blueprint("auth", __name__)
IST_TZ = pytz.timezone("Asia/Kolkata")


def _safe_next_url(raw: str | None) -> str:
    candidate = (raw or "").strip()
    if candidate.startswith("/") and not candidate.startswith("//"):
        return candidate
    return url_for("main.dashboard")


def _is_google_oauth_configured() -> bool:
    return bool(
        not getattr(oauth, "is_stub", False)
        and
        (current_app.config.get("GOOGLE_CLIENT_ID") or "").strip()
        and (current_app.config.get("GOOGLE_CLIENT_SECRET") or "").strip()
    )


def _format_datetime_ist(dt) -> str:
    if not dt:
        return ""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(IST_TZ).strftime("%d %b %Y %I:%M %p")


def _sync_admin_session(user: User) -> None:
    session.permanent = True
    session["is_admin_session"] = bool(AuthService.should_grant_admin(user))


@auth_bp.route("/signup", methods=["GET", "POST"])
def signup():
    if current_user.is_authenticated:
        return redirect(url_for("main.dashboard"))
    form_data = {
        "full_name": request.form.get("full_name", "").strip(),
        "email": request.form.get("email", "").strip(),
        "referral_code": request.form.get("referral_code", "").strip(),
    }
    if request.method == "POST":
        try:
            challenge = AuthService.start_signup_otp(
                full_name=request.form.get("full_name", ""),
                email=request.form.get("email", ""),
                password=request.form.get("password", ""),
                referral_code=request.form.get("referral_code", ""),
            )
        except Exception as exc:
            flash(str(exc), "danger")
        else:
            flash("OTP sent to your email. Verify to create your account.", "success")
            return redirect(
                url_for(
                    "auth.verify_otp",
                    purpose=AuthService.OTP_PURPOSE_SIGNUP,
                    token=challenge.token,
                )
            )
    return render_template(
        "auth/signup.html",
        form_data=form_data,
        google_oauth_enabled=_is_google_oauth_configured(),
    )


@auth_bp.route("/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        return redirect(url_for("main.dashboard"))
    form_data = {"email": request.form.get("email", "").strip()}
    if request.method == "POST":
        try:
            challenge = AuthService.start_login_otp(email=request.form.get("email", ""))
        except Exception as exc:
            flash(str(exc), "danger")
        else:
            flash("Login OTP sent to your email.", "success")
            return redirect(
                url_for(
                    "auth.verify_otp",
                    purpose=AuthService.OTP_PURPOSE_LOGIN,
                    token=challenge.token,
                    next=request.args.get("next", ""),
                )
            )
    return render_template(
        "auth/login.html",
        form_data=form_data,
        google_oauth_enabled=_is_google_oauth_configured(),
    )


@auth_bp.route("/google-login")
def google_login():
    if current_user.is_authenticated:
        return redirect(url_for("main.dashboard"))
    if not _is_google_oauth_configured():
        flash("Google login is not configured yet.", "danger")
        return redirect(url_for("auth.login"))
    session["google_oauth_next"] = _safe_next_url(request.args.get("next"))
    redirect_uri = url_for("main.google_auth_callback", _external=True)
    try:
        return oauth.google.authorize_redirect(redirect_uri, prompt="select_account")
    except Exception:
        current_app.logger.exception("Google OAuth redirect failed")
        flash("Google sign-in could not be started. Please try OTP login.", "danger")
        return redirect(url_for("auth.login"))


@auth_bp.route("/google-auth")
def google_auth():
    if current_user.is_authenticated:
        return redirect(url_for("main.dashboard"))
    if not _is_google_oauth_configured():
        flash("Google login is not configured yet.", "danger")
        return redirect(url_for("auth.login"))

    try:
        token = oauth.google.authorize_access_token()
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
        if not email:
            raise ValueError("Google account email is unavailable.")

        user = User.query.filter_by(email=email).first()
        created = False
        if user:
            if not user.is_active:
                raise ValueError("This account is disabled.")
            if not user.full_name and full_name:
                user.full_name = full_name
        else:
            user = User(
                full_name=full_name or email.split("@", 1)[0],
                email=email,
                referral_code=AuthService._generate_unique_referral_code(),
                is_verified=True,
                is_active=True,
            )
            user.set_password(secrets.token_urlsafe(24))
            db.session.add(user)
            created = True

        if AuthService.is_admin_email(email):
            user.role = "admin"

        user.last_login_at = utcnow()
        if not user.is_verified:
            user.is_verified = True
        db.session.commit()
        AuthService.log_activity(
            user.id,
            "user.signup.google" if created else "user.login.google",
            "user",
            str(user.id),
            details={"provider": "google"},
        )
        login_user(user, remember=True)
        _sync_admin_session(user)
        flash(
            "Account created successfully with Google." if created else "Logged in with Google.",
            "success",
        )
    except Exception:
        current_app.logger.exception("Google OAuth callback failed")
        flash("Google sign-in failed. Please try again or use OTP login.", "danger")
        return redirect(url_for("auth.login"))

    next_url = _safe_next_url(session.pop("google_oauth_next", None))
    return redirect(next_url)


@auth_bp.route("/verify-otp", methods=["GET", "POST"])
def verify_otp():
    if current_user.is_authenticated:
        return redirect(url_for("main.dashboard"))
    purpose = (request.values.get("purpose", "") or "").strip().lower()
    token = (request.values.get("token", "") or "").strip()
    next_url = _safe_next_url(request.values.get("next"))
    if purpose not in {AuthService.OTP_PURPOSE_SIGNUP, AuthService.OTP_PURPOSE_LOGIN} or not token:
        flash("OTP verification session is invalid. Please try again.", "danger")
        return redirect(url_for("auth.login"))
    try:
        challenge = AuthService.get_active_otp_challenge(token, purpose)
    except Exception as exc:
        flash(str(exc), "danger")
        return redirect(url_for("auth.signup" if purpose == AuthService.OTP_PURPOSE_SIGNUP else "auth.login"))

    if request.method == "POST":
        try:
            user = AuthService.verify_otp(
                token=token,
                purpose=purpose,
                otp_input=request.form.get("otp", ""),
            )
        except Exception as exc:
            flash(str(exc), "danger")
        else:
            login_user(user, remember=True)
            _sync_admin_session(user)
            if purpose == AuthService.OTP_PURPOSE_SIGNUP:
                flash("Account verified successfully.", "success")
            else:
                flash("Logged in successfully.", "success")
            return redirect(next_url)
    return render_template(
        "auth/verify_otp.html",
        purpose=purpose,
        token=token,
        next_url=next_url,
        email=challenge.email,
        expires_at_display=_format_datetime_ist(challenge.expires_at),
    )


@auth_bp.route("/logout", methods=["POST"])
@login_required
def logout():
    session.pop("is_admin_session", None)
    logout_user()
    flash("You have been signed out.", "success")
    return redirect(url_for("main.landing"))


@auth_bp.route("/reset-password", methods=["GET", "POST"])
def request_reset():
    token = None
    if request.method == "POST":
        try:
            token = AuthService.create_reset_token(request.form.get("email", ""))
            flash("Password reset token generated. In production this would be emailed.", "success")
        except Exception as exc:
            flash(str(exc), "danger")
    return render_template("auth/reset_request.html", token=token)


@auth_bp.route("/reset-password/<token>", methods=["GET", "POST"])
def reset_password(token: str):
    if request.method == "POST":
        try:
            AuthService.reset_password(token, request.form.get("password", ""))
        except Exception as exc:
            flash(str(exc), "danger")
        else:
            flash("Password updated successfully. Please sign in.", "success")
            return redirect(url_for("auth.login"))
    return render_template("auth/reset_password.html", token=token)

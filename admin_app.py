from __future__ import annotations

import csv
import os
from datetime import datetime, timedelta, timezone
from functools import wraps
from io import BytesIO, StringIO
from pathlib import Path

from dotenv import load_dotenv
from flask import Flask, Response, flash, g, redirect, render_template, request, session, url_for
from flask_migrate import upgrade
from sqlalchemy import func, or_

from app.config import BASE_DIR, config_map
from app.extensions import db
from app.models import (
    ActivityLog,
    Job,
    Payment,
    User,
    UserSubscription,
    utcnow,
)

load_dotenv(BASE_DIR / ".env")
load_dotenv()

ADMIN_APP_STARTED_AT = utcnow()
PLAN_DURATION_LABELS = {30: "1 Month", 90: "3 Months", 180: "6 Months", 365: "12 Months", 730: "24 Months"}


def _normalize_email(email: str) -> str:
    return (email or "").strip().lower()


def _allowed_admin_emails() -> set[str]:
    raw = (os.environ.get("ADMIN_ALLOWED_EMAILS", "") or "").strip()
    return {item.strip().lower() for item in raw.split(",") if item.strip()}


def _safe_admin_redirect(raw: str | None) -> str:
    candidate = (raw or "").strip()
    if candidate.startswith("/admin/") and not candidate.startswith("//"):
        return candidate
    return url_for("admin_dashboard")


def _as_utc(value: datetime | None) -> datetime | None:
    if not value:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _duration_label(days: int) -> str:
    if days in PLAN_DURATION_LABELS:
        return PLAN_DURATION_LABELS[days]
    if days > 0 and days % 30 == 0:
        return f"{days // 30} Months"
    return f"{days} Days" if days > 0 else "-"


def _days_remaining(expires_at: datetime | None, now: datetime | None = None) -> int | None:
    expires = _as_utc(expires_at)
    if not expires:
        return None
    now = _as_utc(now) or utcnow()
    return max((expires.date() - now.date()).days, 0)


def _format_uptime(seconds: int) -> str:
    total = max(0, int(seconds))
    days, rem = divmod(total, 86400)
    hours, rem = divmod(rem, 3600)
    minutes, _ = divmod(rem, 60)
    if days:
        return f"{days}d {hours}h {minutes}m"
    if hours:
        return f"{hours}h {minutes}m"
    return f"{minutes}m"


def _format_bytes(size_bytes: int) -> str:
    size = float(max(0, size_bytes))
    for unit in ["B", "KB", "MB", "GB", "TB"]:
        if size < 1024 or unit == "TB":
            return f"{int(size)} {unit}" if unit == "B" else f"{size:.2f} {unit}"
        size /= 1024
    return f"{int(size_bytes)} B"


def _sqlite_db_info(db_uri: str) -> dict:
    uri = (db_uri or "").strip()
    if not uri.startswith("sqlite:///"):
        return {"path": "External DB", "size_label": "N/A", "size_bytes": 0}
    raw_path = uri.replace("sqlite:///", "", 1)
    db_path = Path(raw_path)
    if not db_path.is_absolute():
        db_path = BASE_DIR / raw_path
    if db_path.exists():
        size_bytes = db_path.stat().st_size
        return {"path": str(db_path), "size_label": _format_bytes(size_bytes), "size_bytes": size_bytes}
    return {"path": str(db_path), "size_label": "0 B", "size_bytes": 0}


def _daily_slot_starts(day_count: int) -> list[datetime]:
    now = utcnow()
    start = datetime(now.year, now.month, now.day, tzinfo=timezone.utc)
    return [start - timedelta(days=offset) for offset in reversed(range(day_count))]


def create_admin_app() -> Flask:
    app = Flask(__name__, template_folder=str(BASE_DIR / "app" / "templates" / "admin_panel"))
    config_name = os.environ.get("APP_CONFIG", "default")
    app.config.from_object(config_map.get(config_name, config_map["default"]))
    app.config["SECRET_KEY"] = (os.environ.get("ADMIN_SECRET_KEY", "") or app.config.get("SECRET_KEY"))
    app.config["ADMIN_ALLOWED_EMAILS"] = sorted(_allowed_admin_emails())

    db.init_app(app)
    with app.app_context():
        try:
            upgrade()
        except Exception:
            app.logger.exception("Could not apply migrations on admin app startup")

    @app.template_filter("dt_local")
    def dt_local_filter(value):
        dt = _as_utc(value)
        if not dt:
            return "-"
        return dt.astimezone().strftime("%d %b %Y %H:%M")

    @app.template_filter("inr")
    def inr_filter(paise: int | None):
        value = int(paise or 0) / 100
        return f"\u20b9{value:,.2f}"

    @app.before_request
    def load_admin_user():
        g.admin_user = None
        user_id = session.get("admin_user_id")
        if not user_id:
            return
        user = db.session.get(User, int(user_id))
        if not user:
            session.pop("admin_user_id", None)
            return
        if not user.is_active or _normalize_email(user.email) not in _allowed_admin_emails():
            session.pop("admin_user_id", None)
            return
        g.admin_user = user

    def admin_login_required(view_func):
        @wraps(view_func)
        def wrapper(*args, **kwargs):
            if not g.admin_user:
                return redirect(url_for("admin_login", next=request.full_path))
            return view_func(*args, **kwargs)

        return wrapper

    def registration_source_map(user_ids: list[int]) -> tuple[dict[int, str], dict[str, int]]:
        if not user_ids:
            return {}, {"google": 0, "manual": 0}
        google_signup_ids = {
            row[0]
            for row in db.session.query(ActivityLog.user_id)
            .filter(ActivityLog.user_id.in_(user_ids), ActivityLog.action == "user.signup.google")
            .distinct()
            .all()
            if row[0] is not None
        }
        manual_signup_ids = {
            row[0]
            for row in db.session.query(ActivityLog.user_id)
            .filter(
                ActivityLog.user_id.in_(user_ids),
                ActivityLog.action.like("user.signup%"),
                ActivityLog.action != "user.signup.google",
            )
            .distinct()
            .all()
            if row[0] is not None
        }
        google_login_ids = {
            row[0]
            for row in db.session.query(ActivityLog.user_id)
            .filter(ActivityLog.user_id.in_(user_ids), ActivityLog.action == "user.login.google")
            .distinct()
            .all()
            if row[0] is not None
        }
        source_map: dict[int, str] = {}
        for user_id in user_ids:
            if user_id in google_signup_ids or user_id in google_login_ids:
                source_map[user_id] = "Google"
            elif user_id in manual_signup_ids:
                source_map[user_id] = "Manual"
            else:
                source_map[user_id] = "Manual"
        source_counts = {
            "google": sum(1 for source in source_map.values() if source == "Google"),
            "manual": sum(1 for source in source_map.values() if source == "Manual"),
        }
        return source_map, source_counts

    def latest_login_issue_map(user_ids: list[int]) -> dict[int, str]:
        if not user_ids:
            return {}
        logs = (
            ActivityLog.query.filter(
                ActivityLog.user_id.in_(user_ids),
                ActivityLog.action.in_(("user.login.blocked.banned", "user.login.blocked.unverified")),
            )
            .order_by(ActivityLog.created_at.desc())
            .all()
        )
        issue_map: dict[int, str] = {}
        for log in logs:
            if log.user_id not in issue_map:
                issue_map[log.user_id] = log.action
        return issue_map

    def latest_subscription_map(user_ids: list[int]) -> dict[int, UserSubscription]:
        if not user_ids:
            return {}
        subscriptions = (
            UserSubscription.query.filter(UserSubscription.user_id.in_(user_ids))
            .order_by(UserSubscription.user_id.asc(), UserSubscription.expires_at.desc(), UserSubscription.updated_at.desc())
            .all()
        )
        latest_by_user: dict[int, UserSubscription] = {}
        for sub in subscriptions:
            if sub.user_id not in latest_by_user:
                latest_by_user[sub.user_id] = sub
        return latest_by_user

    def processed_count_map(user_ids: list[int]) -> dict[int, int]:
        if not user_ids:
            return {}
        rows = (
            db.session.query(Job.user_id, func.count(Job.id))
            .filter(Job.user_id.in_(user_ids), Job.status == "completed")
            .group_by(Job.user_id)
            .all()
        )
        return {row[0]: int(row[1]) for row in rows}

    def financial_overview() -> dict:
        now = utcnow()
        month_start = datetime(now.year, now.month, 1, tzinfo=timezone.utc)
        week_start = now - timedelta(days=7)
        total_revenue_paise = db.session.query(func.sum(Payment.amount_paise)).filter(Payment.status == "success").scalar() or 0
        monthly_earnings_paise = (
            db.session.query(func.sum(Payment.amount_paise))
            .filter(Payment.status == "success", Payment.paid_at >= month_start)
            .scalar()
            or 0
        )
        weekly_earnings_paise = (
            db.session.query(func.sum(Payment.amount_paise))
            .filter(Payment.status == "success", Payment.paid_at >= week_start)
            .scalar()
            or 0
        )
        status_counts = {
            "success": Payment.query.filter_by(status="success").count(),
            "failed": Payment.query.filter_by(status="failed").count(),
            "pending": Payment.query.filter_by(status="pending").count(),
        }

        day_slots = _daily_slot_starts(30)
        slot_index = {slot.date(): index for index, slot in enumerate(day_slots)}
        daily_values_paise = [0 for _ in day_slots]
        success_rows = (
            Payment.query.filter(Payment.status == "success", Payment.paid_at >= day_slots[0])
            .order_by(Payment.paid_at.asc())
            .all()
        )
        for payment in success_rows:
            paid_at = _as_utc(payment.paid_at) or _as_utc(payment.created_at)
            if not paid_at:
                continue
            index = slot_index.get(paid_at.date())
            if index is None:
                continue
            daily_values_paise[index] += int(payment.amount_paise or 0)

        return {
            "total_revenue_paise": int(total_revenue_paise),
            "monthly_earnings_paise": int(monthly_earnings_paise),
            "weekly_earnings_paise": int(weekly_earnings_paise),
            "status_counts": status_counts,
            "chart_labels": [slot.strftime("%d %b") for slot in day_slots],
            "chart_values_inr": [round(value / 100, 2) for value in daily_values_paise],
        }

    def dashboard_metrics() -> dict:
        now = utcnow()
        total_users = User.query.count()
        total_premium_users = (
            db.session.query(UserSubscription.user_id)
            .filter(UserSubscription.status == "active", UserSubscription.expires_at > now)
            .distinct()
            .count()
        )
        joined_last_24h = User.query.filter(User.created_at >= (now - timedelta(hours=24))).count()
        user_ids = [row[0] for row in db.session.query(User.id).all()]
        _, source_counts = registration_source_map(user_ids)
        return {
            "total_users": total_users,
            "total_premium_users": total_premium_users,
            "joined_last_24h": joined_last_24h,
            "google_users": source_counts["google"],
            "manual_users": source_counts["manual"],
        }

    def system_health() -> dict:
        now = utcnow()
        uptime_seconds = int((_as_utc(now) - _as_utc(ADMIN_APP_STARTED_AT)).total_seconds())
        db_info = _sqlite_db_info(app.config.get("SQLALCHEMY_DATABASE_URI", ""))
        return {"uptime_text": _format_uptime(uptime_seconds), "database_size": db_info["size_label"], "database_path": db_info["path"]}

    def build_user_rows(search_query: str, premium_filter: str, status_filter: str, source_filter: str) -> list[dict]:
        now = utcnow()
        query = User.query
        if search_query:
            like = f"%{search_query}%"
            query = query.filter(or_(User.email.ilike(like), User.full_name.ilike(like)))
        if status_filter == "active":
            query = query.filter(User.is_active.is_(True))
        elif status_filter == "banned":
            query = query.filter(User.is_active.is_(False))
        users = query.order_by(User.created_at.desc()).all()
        if not users:
            return []

        user_ids = [user.id for user in users]
        source_map, _ = registration_source_map(user_ids)
        subscription_map = latest_subscription_map(user_ids)
        processed_map = processed_count_map(user_ids)
        issue_log_map = latest_login_issue_map(user_ids)

        rows = []
        for user in users:
            source = source_map.get(user.id, "Manual")
            sub = subscription_map.get(user.id)
            sub_expires = _as_utc(sub.expires_at) if sub else None
            is_premium_active = bool(sub and (sub.status or "").lower() == "active" and sub_expires and sub_expires > now)
            days_left = _days_remaining(sub.expires_at, now) if sub and sub_expires and sub_expires > now else None
            time_remaining = f"{days_left} Days left" if days_left is not None else ("Expired" if sub else "-")
            expires_soon = bool(days_left is not None and days_left < 3)

            if premium_filter == "premium" and not is_premium_active:
                continue
            if premium_filter == "free" and is_premium_active:
                continue
            if source_filter == "google" and source != "Google":
                continue
            if source_filter == "manual" and source != "Manual":
                continue

            if not user.is_active:
                issue_state = "Banned"
            elif not user.is_verified:
                issue_state = "Unverified"
            elif issue_log_map.get(user.id) == "user.login.blocked.unverified":
                issue_state = "Unverified"
            elif issue_log_map.get(user.id) == "user.login.blocked.banned":
                issue_state = "Banned"
            else:
                issue_state = "OK"

            rows.append(
                {
                    "user": user,
                    "registration_source": source,
                    "is_premium": is_premium_active,
                    "subscription": sub,
                    "days_remaining": days_left,
                    "time_remaining": time_remaining,
                    "expires_soon": expires_soon or time_remaining == "Expired",
                    "pdf_processed_count": processed_map.get(user.id, 0),
                    "account_status": "Active" if user.is_active else "Banned",
                    "issue_state": issue_state,
                }
            )
        return rows

    def build_payment_rows(limit: int = 300, status_filter: str = "all") -> list[dict]:
        query = Payment.query.order_by(Payment.created_at.desc())
        if status_filter in {"success", "failed", "pending"}:
            query = query.filter(Payment.status == status_filter)
        payments = query.limit(limit).all()
        if not payments:
            return []
        user_ids = sorted({payment.user_id for payment in payments if payment.user_id})
        sub_map = latest_subscription_map(user_ids)
        now = utcnow()
        rows = []
        for payment in payments:
            user = payment.user
            sub = sub_map.get(payment.user_id)
            sub_expires = _as_utc(sub.expires_at) if sub else None
            days_left = _days_remaining(sub.expires_at, now) if sub and sub_expires and sub_expires > now else None
            rows.append(
                {
                    "payment": payment,
                    "user_email": user.email if user else "-",
                    "plan_taken": payment.plan_name or _duration_label(int(payment.duration_days or 0)),
                    "duration_label": _duration_label(int(payment.duration_days or 0)),
                    "razorpay_id": payment.razorpay_payment_id or payment.razorpay_order_id,
                    "payment_date": _as_utc(payment.paid_at) or _as_utc(payment.created_at),
                    "days_remaining": days_left,
                    "expires_soon": days_left is not None and days_left < 3,
                }
            )
        return rows

    def premium_export_rows() -> list[dict]:
        now = utcnow()
        subscriptions = (
            UserSubscription.query.filter(UserSubscription.status == "active", UserSubscription.expires_at > now)
            .order_by(UserSubscription.expires_at.asc())
            .all()
        )
        if not subscriptions:
            return []
        by_user: dict[int, UserSubscription] = {}
        for sub in subscriptions:
            if sub.user_id not in by_user:
                by_user[sub.user_id] = sub
        user_ids = sorted(by_user.keys())
        payments = (
            Payment.query.filter(Payment.user_id.in_(user_ids), Payment.status == "success")
            .order_by(Payment.paid_at.desc(), Payment.created_at.desc())
            .all()
        )
        latest_payment: dict[int, Payment] = {}
        for payment in payments:
            if payment.user_id not in latest_payment:
                latest_payment[payment.user_id] = payment
        rows = []
        for user_id in user_ids:
            user = db.session.get(User, user_id)
            sub = by_user[user_id]
            pay = latest_payment.get(user_id)
            rows.append(
                {
                    "user_id": user.id if user else user_id,
                    "full_name": user.full_name if user else "-",
                    "email": user.email if user else "-",
                    "plan_name": sub.plan_name,
                    "plan_duration": _duration_label((sub.expires_at - sub.started_at).days) if sub.started_at and sub.expires_at else "-",
                    "days_remaining": _days_remaining(sub.expires_at, now) or 0,
                    "amount_inr": (pay.amount_paise / 100) if pay else 0,
                    "payment_date": (_as_utc(pay.paid_at) or _as_utc(pay.created_at)).isoformat() if pay else "",
                    "razorpay_order_id": pay.razorpay_order_id if pay else "",
                    "razorpay_payment_id": pay.razorpay_payment_id if pay else "",
                    "payment_status": pay.status if pay else "",
                }
            )
        return rows

    def revenue_report_rows() -> list[dict]:
        payments = Payment.query.order_by(Payment.created_at.desc()).all()
        rows = []
        for payment in payments:
            user = payment.user
            rows.append(
                {
                    "User ID": payment.user_id,
                    "User Name": user.full_name if user else "",
                    "User Email": user.email if user else "",
                    "Amount (INR)": round((payment.amount_paise or 0) / 100, 2),
                    "Currency": payment.currency,
                    "Plan": payment.plan_name or payment.plan_key,
                    "Duration": _duration_label(int(payment.duration_days or 0)),
                    "Gateway": payment.gateway,
                    "Status": payment.status,
                    "Razorpay Order ID": payment.razorpay_order_id,
                    "Razorpay Payment ID": payment.razorpay_payment_id or "",
                    "Reference": payment.reference,
                    "Payment Date": (_as_utc(payment.paid_at) or _as_utc(payment.created_at)).isoformat() if (_as_utc(payment.paid_at) or _as_utc(payment.created_at)) else "",
                    "Error Message": payment.error_message or "",
                }
            )
        return rows
    @app.route("/admin")
    def admin_root():
        if g.admin_user:
            return redirect(url_for("admin_dashboard"))
        return redirect(url_for("admin_login"))

    @app.route("/admin/login", methods=["GET", "POST"])
    def admin_login():
        if g.admin_user:
            return redirect(url_for("admin_dashboard"))
        if request.method == "POST":
            email = _normalize_email(request.form.get("email", ""))
            password = (request.form.get("password", "") or "").strip()
            if not email or not password:
                flash("Email and password are required.", "danger")
                return redirect(url_for("admin_login"))
            if email not in _allowed_admin_emails():
                flash("This email is not authorized for admin panel.", "danger")
                return redirect(url_for("admin_login"))
            user = User.query.filter_by(email=email).first()
            if not user or not user.check_password(password):
                flash("Invalid admin credentials.", "danger")
                return redirect(url_for("admin_login"))
            if not user.is_active:
                flash("Your account is banned/disabled.", "danger")
                return redirect(url_for("admin_login"))
            session.clear()
            session["admin_user_id"] = user.id
            user.last_login_at = utcnow()
            db.session.commit()
            flash("Admin login successful.", "success")
            return redirect(_safe_admin_redirect(request.form.get("next")))
        return render_template("admin_login.html", next_url=_safe_admin_redirect(request.args.get("next")))

    @app.post("/admin/logout")
    @admin_login_required
    def admin_logout():
        session.clear()
        flash("Signed out from admin panel.", "success")
        return redirect(url_for("admin_login"))

    @app.route("/admin/dashboard")
    @admin_login_required
    def admin_dashboard():
        q = (request.args.get("q", "") or "").strip()
        premium_filter = (request.args.get("premium", "all") or "all").strip().lower()
        status_filter = (request.args.get("status", "all") or "all").strip().lower()
        source_filter = (request.args.get("source", "all") or "all").strip().lower()
        payment_status = (request.args.get("payment_status", "all") or "all").strip().lower()

        users = build_user_rows(q, premium_filter, status_filter, source_filter)
        payments = build_payment_rows(300, payment_status)
        finances = financial_overview()
        metrics = dashboard_metrics()
        health = system_health()
        return render_template(
            "admin_dashboard.html",
            users=users,
            payments=payments,
            metrics=metrics,
            finances=finances,
            health=health,
            filters={
                "q": q,
                "premium": premium_filter,
                "status": status_filter,
                "source": source_filter,
                "payment_status": payment_status,
            },
        )

    @app.post("/admin/users/<int:user_id>/action")
    @admin_login_required
    def admin_user_action(user_id: int):
        now = utcnow()
        user = db.session.get(User, user_id)
        if not user:
            flash("User not found.", "danger")
            return redirect(url_for("admin_dashboard"))

        action = (request.form.get("action", "") or "").strip().lower()
        if action == "toggle_premium":
            current = (
                UserSubscription.query.filter(
                    UserSubscription.user_id == user.id,
                    UserSubscription.status == "active",
                    UserSubscription.expires_at > now,
                )
                .order_by(UserSubscription.expires_at.desc())
                .first()
            )
            if current:
                current.status = "cancelled"
                current.cancelled_at = now
                current.expires_at = now
                flash(f"Premium revoked for {user.email}.", "warning")
            else:
                db.session.add(
                    UserSubscription(
                        user_id=user.id,
                        plan_key="admin_grant",
                        plan_name="Admin Premium Grant",
                        status="active",
                        price_paise=0,
                        started_at=now,
                        expires_at=now + timedelta(days=365),
                        metadata_json={"granted_by": g.admin_user.email, "source": "admin_panel"},
                    )
                )
                flash(f"Premium granted for {user.email}.", "success")
        elif action == "extend_premium":
            extra_days_raw = (request.form.get("extra_days", "0") or "0").strip()
            extra_months_raw = (request.form.get("extra_months", "0") or "0").strip()
            try:
                extra_days = max(0, int(extra_days_raw))
                extra_months = max(0, int(extra_months_raw))
            except Exception:
                flash("Enter valid days/months for extension.", "danger")
                return redirect(_safe_admin_redirect(request.form.get("return_to")))
            total_days = extra_days + (extra_months * 30)
            if total_days <= 0:
                flash("Extension must be at least 1 day.", "danger")
                return redirect(_safe_admin_redirect(request.form.get("return_to")))

            latest = (
                UserSubscription.query.filter(UserSubscription.user_id == user.id)
                .order_by(UserSubscription.expires_at.desc(), UserSubscription.updated_at.desc())
                .first()
            )
            if latest:
                current_expiry = _as_utc(latest.expires_at) or now
                base = current_expiry if current_expiry > now else now
                latest.status = "active"
                latest.cancelled_at = None
                latest.started_at = _as_utc(latest.started_at) or now
                latest.expires_at = base + timedelta(days=total_days)
                latest.metadata_json = {
                    **(latest.metadata_json or {}),
                    "extended_by_admin": g.admin_user.email,
                    "extended_days": total_days,
                    "extended_at": now.isoformat(),
                }
            else:
                db.session.add(
                    UserSubscription(
                        user_id=user.id,
                        plan_key="admin_extension",
                        plan_name="Admin Extended Plan",
                        status="active",
                        price_paise=0,
                        started_at=now,
                        expires_at=now + timedelta(days=total_days),
                        metadata_json={
                            "source": "admin_panel",
                            "extended_by_admin": g.admin_user.email,
                            "extended_days": total_days,
                        },
                    )
                )
            flash(f"Premium extended by {total_days} day(s) for {user.email}.", "success")
        elif action == "toggle_ban":
            user.is_active = not user.is_active
            flash(
                f"Account {'unbanned' if user.is_active else 'banned'} for {user.email}.",
                "warning" if not user.is_active else "success",
            )
        else:
            flash("Unsupported action.", "danger")
            return redirect(url_for("admin_dashboard"))

        db.session.add(
            ActivityLog(
                user_id=g.admin_user.id,
                action=f"admin.user.{action}",
                target_type="user",
                target_id=str(user.id),
                ip_address=request.remote_addr or "",
                user_agent=request.headers.get("User-Agent", ""),
                details_json={"target_email": user.email},
            )
        )
        db.session.commit()
        return redirect(_safe_admin_redirect(request.form.get("return_to")))

    @app.get("/admin/export/premium-users.csv")
    @admin_login_required
    def export_premium_csv():
        rows = premium_export_rows()
        buffer = StringIO()
        writer = csv.writer(buffer)
        writer.writerow([
            "User ID", "Name", "Email", "Plan", "Plan Duration", "Days Remaining", "Amount Paid (INR)",
            "Payment Date", "Razorpay Order ID", "Razorpay Payment ID", "Payment Status",
        ])
        for row in rows:
            writer.writerow([
                row["user_id"], row["full_name"], row["email"], row["plan_name"], row["plan_duration"],
                row["days_remaining"], f"{row['amount_inr']:.2f}", row["payment_date"], row["razorpay_order_id"],
                row["razorpay_payment_id"], row["payment_status"],
            ])
        return Response(
            buffer.getvalue(),
            mimetype="text/csv",
            headers={"Content-Disposition": "attachment; filename=premium_users_payments.csv"},
        )

    @app.get("/admin/export/revenue-report.xlsx")
    @admin_login_required
    def export_revenue_xlsx():
        rows = revenue_report_rows()
        if not rows:
            flash("No revenue records available to export.", "warning")
            return redirect(url_for("admin_dashboard"))
        try:
            import pandas as pd
        except Exception:
            flash("Pandas is required for Excel export. Please install pandas.", "danger")
            return redirect(url_for("admin_dashboard"))

        dataframe = pd.DataFrame(rows)
        byte_stream = BytesIO()
        with pd.ExcelWriter(byte_stream, engine="openpyxl") as writer:
            dataframe.to_excel(writer, index=False, sheet_name="Revenue Report")
        byte_stream.seek(0)
        return Response(
            byte_stream.getvalue(),
            mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers={"Content-Disposition": "attachment; filename=revenue_report.xlsx"},
        )

    @app.get("/admin/export/revenue-report.csv")
    @admin_login_required
    def export_revenue_csv():
        rows = revenue_report_rows()
        buffer = StringIO()
        fieldnames = list(rows[0].keys()) if rows else [
            "User ID", "User Name", "User Email", "Amount (INR)", "Currency", "Plan", "Duration", "Gateway",
            "Status", "Razorpay Order ID", "Razorpay Payment ID", "Reference", "Payment Date", "Error Message",
        ]
        writer = csv.DictWriter(buffer, fieldnames=fieldnames)
        writer.writeheader()
        if rows:
            writer.writerows(rows)
        return Response(
            buffer.getvalue(),
            mimetype="text/csv",
            headers={"Content-Disposition": "attachment; filename=revenue_report.csv"},
        )

    return app


app = create_admin_app()


if __name__ == "__main__":
    host = os.environ.get("ADMIN_RUN_HOST", "127.0.0.1")
    port = int(os.environ.get("ADMIN_RUN_PORT", "5050"))
    debug = (os.environ.get("ADMIN_DEBUG", "true") or "true").strip().lower() in {"1", "true", "yes", "on"}
    app.run(host=host, port=port, debug=debug)

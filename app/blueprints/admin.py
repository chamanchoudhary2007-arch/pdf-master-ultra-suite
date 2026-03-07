from __future__ import annotations

from datetime import datetime, timedelta, timezone

from flask import Blueprint, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required
from sqlalchemy import or_

from app.extensions import db
from app.models import ActivityLog, Payment, SubscriptionEvent, ToolCatalog, User, UserSubscription
from app.services.analytics_service import AnalyticsService
from app.services.auth_service import AuthService, admin_required
from app.services.subscription_service import SubscriptionService

admin_bp = Blueprint("admin", __name__)


def _safe_admin_return_url(candidate: str | None) -> str:
    target = (candidate or "").strip()
    if target.startswith("/admin/"):
        return target
    return url_for("admin.subscriptions")


@admin_bp.route("/")
@login_required
@admin_required
def dashboard():
    summary = AnalyticsService.admin_summary()
    tool_usage = AnalyticsService.most_used_tools()
    recent_logs = AnalyticsService.recent_logs()
    recent_payments = AnalyticsService.payment_rows()
    tools = ToolCatalog.query.order_by(ToolCatalog.is_enabled.desc(), ToolCatalog.name.asc()).limit(50).all()
    premium_summary = SubscriptionService.premium_analytics_summary()
    return render_template(
        "admin_panel/dashboard.html",
        summary=summary,
        tool_usage=tool_usage,
        recent_logs=recent_logs,
        recent_payments=recent_payments,
        tools=tools,
        premium_summary=premium_summary,
    )


@admin_bp.route("/subscriptions")
@login_required
@admin_required
def subscriptions():
    q = (request.args.get("q", "") or "").strip()
    plan_filter = (request.args.get("plan", "all") or "all").strip()
    subscription_status_filter = (request.args.get("subscription_status", "all") or "all").strip()
    payment_status_filter = (request.args.get("payment_status", "all") or "all").strip()
    source_filter = (request.args.get("source", "all") or "all").strip()
    date_from_raw = (request.args.get("date_from", "") or "").strip()
    date_to_raw = (request.args.get("date_to", "") or "").strip()

    date_from = None
    date_to = None
    if date_from_raw:
        try:
            date_from = datetime.strptime(date_from_raw, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        except ValueError:
            flash("Invalid start date filter ignored.", "warning")
    if date_to_raw:
        try:
            date_to = datetime.strptime(date_to_raw, "%Y-%m-%d").replace(
                tzinfo=timezone.utc
            ) + timedelta(days=1)
        except ValueError:
            flash("Invalid end date filter ignored.", "warning")

    query = UserSubscription.query.join(User, User.id == UserSubscription.user_id)
    if q:
        query = query.filter(
            or_(
                User.email.ilike(f"%{q}%"),
                User.full_name.ilike(f"%{q}%"),
            )
        )
    if plan_filter != "all":
        query = query.filter(UserSubscription.plan_key == plan_filter)
    if date_from:
        query = query.filter(UserSubscription.updated_at >= date_from)
    if date_to:
        query = query.filter(UserSubscription.updated_at < date_to)

    subscriptions = query.order_by(UserSubscription.updated_at.desc()).all()
    rows: list[dict] = []
    source_options: set[str] = set()
    now = datetime.now(timezone.utc)
    seen_users: set[int] = set()

    for sub in subscriptions:
        if sub.user_id in seen_users:
            continue
        seen_users.add(sub.user_id)
        status_summary = SubscriptionService.subscription_status_summary(sub, now=now)
        latest_payment = (
            Payment.query.filter_by(user_id=sub.user_id)
            .order_by(Payment.created_at.desc(), Payment.id.desc())
            .first()
        )
        payment_meta = SubscriptionService.serialize_payment(latest_payment) if latest_payment else None
        latest_event = (
            SubscriptionEvent.query.filter_by(user_id=sub.user_id)
            .order_by(SubscriptionEvent.created_at.desc(), SubscriptionEvent.id.desc())
            .first()
        )
        latest_source = (
            (latest_event.source or "").strip()
            or (sub.metadata_json or {}).get("source")
            or "unknown"
        )
        source_options.add(latest_source)

        if subscription_status_filter != "all" and status_summary["status_key"] != subscription_status_filter:
            continue
        if payment_status_filter != "all":
            if not payment_meta or payment_meta["status"] != payment_status_filter:
                continue
        if source_filter != "all" and latest_source != source_filter:
            continue

        rows.append(
            {
                "subscription": sub,
                "user": sub.user,
                "status_summary": status_summary,
                "latest_payment": payment_meta,
                "latest_event": latest_event,
                "latest_source": latest_source,
            }
        )

    premium_summary = SubscriptionService.premium_analytics_summary()
    plan_options = SubscriptionService.plan_catalog()
    custom_min_days, custom_max_days = SubscriptionService.custom_days_range()

    return render_template(
        "admin_panel/subscriptions.html",
        rows=rows,
        filters={
            "q": q,
            "plan": plan_filter,
            "subscription_status": subscription_status_filter,
            "payment_status": payment_status_filter,
            "source": source_filter,
            "date_from": date_from_raw,
            "date_to": date_to_raw,
        },
        source_options=sorted(source_options),
        plan_options=plan_options,
        custom_min_days=custom_min_days,
        custom_max_days=custom_max_days,
        premium_summary=premium_summary,
    )


@admin_bp.route("/subscriptions/<int:user_id>/action", methods=["POST"])
@login_required
@admin_required
def subscription_action(user_id: int):
    user = User.query.get_or_404(user_id)
    action = (request.form.get("action", "") or "").strip().lower()
    return_to = _safe_admin_return_url(request.form.get("return_to"))
    notes = (request.form.get("notes", "") or "").strip()
    audit_details: dict = {"target_user_id": user.id, "target_email": user.email}

    try:
        if action == "grant":
            plan_key = (request.form.get("plan_key", "") or "").strip()
            custom_days_raw = (request.form.get("custom_days", "") or "").strip()
            custom_days = custom_days_raw or None
            subscription = SubscriptionService.admin_grant_subscription(
                user=user,
                plan_key=plan_key,
                custom_days=custom_days,
                actor=current_user,
                notes=notes,
            )
            flash(
                f"Premium granted/renewed for {user.email}. New expiry: {subscription.expires_at.strftime('%d %b %Y')}.",
                "success",
            )
            audit_details.update(
                {
                    "action": "grant",
                    "plan_key": subscription.plan_key,
                    "expires_at": subscription.expires_at.isoformat() if subscription.expires_at else "",
                }
            )
        elif action == "extend":
            extra_days = int((request.form.get("extra_days", "") or "0").strip() or 0)
            subscription = SubscriptionService.admin_extend_days(
                user=user,
                extra_days=extra_days,
                actor=current_user,
                notes=notes,
            )
            flash(
                f"Extended premium for {user.email} by {extra_days} days. New expiry: {subscription.expires_at.strftime('%d %b %Y')}.",
                "success",
            )
            audit_details.update(
                {
                    "action": "extend",
                    "extra_days": extra_days,
                    "expires_at": subscription.expires_at.isoformat() if subscription.expires_at else "",
                }
            )
        elif action == "revoke":
            subscription = SubscriptionService.admin_revoke_subscription(
                user=user,
                actor=current_user,
                notes=notes,
            )
            flash(f"Premium access revoked for {user.email}.", "warning")
            audit_details.update(
                {
                    "action": "revoke",
                    "expires_at": subscription.expires_at.isoformat() if subscription.expires_at else "",
                }
            )
        else:
            raise ValueError("Invalid admin action.")
    except Exception as exc:
        flash(str(exc), "danger")
        AuthService.log_activity(
            current_user.id,
            "admin.subscription.action_failed",
            "user",
            str(user.id),
            details={**audit_details, "requested_action": action, "error": str(exc)},
        )
        return redirect(return_to)

    AuthService.log_activity(
        current_user.id,
        f"admin.subscription.{action}",
        "user",
        str(user.id),
        details={**audit_details, "notes": notes[:255]},
    )
    return redirect(return_to)


@admin_bp.route("/tools", methods=["GET", "POST"])
@login_required
@admin_required
def tools():
    if request.method == "POST":
        tool = ToolCatalog.query.get_or_404(int(request.form.get("tool_id", "0")))
        tool.is_enabled = bool(request.form.get("is_enabled"))
        price_paise = int(request.form.get("price_paise", tool.price_paise))
        tool.price_paise = min(2500, max(500, price_paise)) if price_paise else 0
        tool.is_subscription_only = bool(request.form.get("is_subscription_only"))
        tool.is_payperuse_allowed = bool(request.form.get("is_payperuse_allowed"))
        db.session.commit()
        flash(f"Updated {tool.name}.", "success")
        return redirect(url_for("admin.tools"))

    search = request.args.get("q", "").strip()
    query = ToolCatalog.query
    if search:
        query = query.filter(ToolCatalog.name.ilike(f"%{search}%"))
    tools = query.order_by(ToolCatalog.category.asc(), ToolCatalog.name.asc()).all()
    return render_template("admin_panel/tools.html", tools=tools, search=search)


@admin_bp.route("/users", methods=["GET", "POST"])
@login_required
@admin_required
def users():
    if request.method == "POST":
        user = User.query.get_or_404(int(request.form.get("user_id", "0")))
        if request.form.get("toggle_active"):
            user.is_active = not user.is_active
        if request.form.get("make_admin"):
            user.role = "admin" if request.form.get("make_admin") == "1" else "user"
        db.session.commit()
        flash(f"Updated user {user.email}.", "success")
        return redirect(url_for("admin.users"))
    users = User.query.order_by(User.created_at.desc()).all()
    return render_template("admin_panel/users.html", users=users)


@admin_bp.route("/payments")
@login_required
@admin_required
def payments():
    rows = AnalyticsService.payment_rows(limit=200)
    return render_template("admin_panel/payments.html", payments=rows)


@admin_bp.route("/logs")
@login_required
@admin_required
def logs():
    rows = ActivityLog.query.order_by(ActivityLog.created_at.desc()).limit(200).all()
    return render_template("admin_panel/logs.html", logs=rows)

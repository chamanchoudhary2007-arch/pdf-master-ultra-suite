from __future__ import annotations

from datetime import datetime, timedelta, timezone
import math

from flask import Blueprint, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required
from sqlalchemy import or_

from app.extensions import db
from app.models import (
    ActivityLog,
    ApiUsageLog,
    BulkBatch,
    CloudIntegrationConnection,
    ComplianceReport,
    FileAccessLog,
    Payment,
    SignatureRequest,
    SubscriptionEvent,
    TeamWorkspace,
    ToolCatalog,
    User,
    UserSubscription,
    WalletTransaction,
)
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
@admin_required(min_role="support")
def dashboard():
    summary = AnalyticsService.admin_summary()
    advanced_summary = AnalyticsService.advanced_feature_summary()
    tool_usage = AnalyticsService.most_used_tools()
    recent_logs = AnalyticsService.recent_logs()
    recent_payments = AnalyticsService.payment_rows()
    tools = ToolCatalog.query.order_by(ToolCatalog.is_enabled.desc(), ToolCatalog.name.asc()).limit(50).all()
    premium_summary = SubscriptionService.premium_analytics_summary()
    return render_template(
        "admin_panel/dashboard.html",
        summary=summary,
        advanced_summary=advanced_summary,
        tool_usage=tool_usage,
        recent_logs=recent_logs,
        recent_payments=recent_payments,
        tools=tools,
        premium_summary=premium_summary,
    )


@admin_bp.route("/subscriptions")
@login_required
@admin_required(min_role="support")
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

    page = max(1, request.args.get("page", 1, type=int) or 1)
    per_page = min(max(request.args.get("per_page", 20, type=int) or 20, 10), 100)
    total_rows = len(rows)
    total_pages = max(1, int(math.ceil(total_rows / float(per_page))))
    if page > total_pages:
        page = total_pages
    start = (page - 1) * per_page
    end = start + per_page
    paged_rows = rows[start:end]
    pagination = {
        "page": page,
        "per_page": per_page,
        "total": total_rows,
        "pages": total_pages,
        "has_prev": page > 1,
        "has_next": page < total_pages,
        "prev_num": page - 1,
        "next_num": page + 1,
    }

    return render_template(
        "admin_panel/subscriptions.html",
        rows=paged_rows,
        filters={
            "q": q,
            "plan": plan_filter,
            "subscription_status": subscription_status_filter,
            "payment_status": payment_status_filter,
            "source": source_filter,
            "date_from": date_from_raw,
            "date_to": date_to_raw,
            "per_page": per_page,
        },
        pagination=pagination,
        source_options=sorted(source_options),
        plan_options=plan_options,
        custom_min_days=custom_min_days,
        custom_max_days=custom_max_days,
        premium_summary=premium_summary,
    )


@admin_bp.route("/subscriptions/<int:user_id>/action", methods=["POST"])
@login_required
@admin_required(min_role="admin")
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
@admin_required(min_role="support")
def tools():
    if request.method == "POST":
        if not AuthService.has_admin_access(current_user, min_role="admin"):
            flash("You have read-only admin access.", "warning")
            return redirect(url_for("admin.tools"))
        try:
            tool_id = int((request.form.get("tool_id", "0") or "0").strip())
            tool = ToolCatalog.query.get_or_404(tool_id)
            tool.is_enabled = bool(request.form.get("is_enabled"))
            price_paise = int((request.form.get("price_paise", str(tool.price_paise)) or "0").strip())
            tool.price_paise = min(2500, max(500, price_paise)) if price_paise else 0
            tool.is_subscription_only = bool(request.form.get("is_subscription_only"))
            tool.is_payperuse_allowed = bool(request.form.get("is_payperuse_allowed"))
            db.session.commit()
            flash(f"Updated {tool.name}.", "success")
        except Exception as exc:
            db.session.rollback()
            flash(str(exc), "danger")
        return redirect(url_for("admin.tools"))

    search = request.args.get("q", "").strip()
    query = ToolCatalog.query
    if search:
        query = query.filter(ToolCatalog.name.ilike(f"%{search}%"))
    page = max(1, request.args.get("page", 1, type=int) or 1)
    per_page = min(max(request.args.get("per_page", 25, type=int) or 25, 10), 100)
    pagination = query.order_by(ToolCatalog.category.asc(), ToolCatalog.name.asc()).paginate(
        page=page,
        per_page=per_page,
        error_out=False,
    )
    tools = pagination.items
    return render_template(
        "admin_panel/tools.html",
        tools=tools,
        search=search,
        pagination=pagination,
        per_page=per_page,
    )


@admin_bp.route("/users", methods=["GET", "POST"])
@login_required
@admin_required(min_role="support")
def users():
    if request.method == "POST":
        if not AuthService.has_admin_access(current_user, min_role="admin"):
            flash("You have read-only admin access.", "warning")
            return redirect(url_for("admin.users"))
        try:
            user_id = int((request.form.get("user_id", "0") or "0").strip())
            user = User.query.get_or_404(user_id)
            if request.form.get("toggle_active"):
                user.is_active = not user.is_active
            if request.form.get("make_admin"):
                user.role = "admin" if request.form.get("make_admin") == "1" else "user"
            db.session.commit()
            flash(f"Updated user {user.email}.", "success")
        except Exception as exc:
            db.session.rollback()
            flash(str(exc), "danger")
        return redirect(url_for("admin.users"))
    q = (request.args.get("q", "") or "").strip()
    page = max(1, request.args.get("page", 1, type=int) or 1)
    per_page = min(max(request.args.get("per_page", 25, type=int) or 25, 10), 100)
    query = User.query
    if q:
        query = query.filter(
            or_(
                User.email.ilike(f"%{q}%"),
                User.full_name.ilike(f"%{q}%"),
            )
        )
    pagination = query.order_by(User.created_at.desc(), User.id.desc()).paginate(
        page=page,
        per_page=per_page,
        error_out=False,
    )
    users = pagination.items
    return render_template(
        "admin_panel/users.html",
        users=users,
        q=q,
        pagination=pagination,
        per_page=per_page,
    )


@admin_bp.route("/payments")
@login_required
@admin_required(min_role="support")
def payments():
    page = max(1, request.args.get("page", 1, type=int) or 1)
    per_page = min(max(request.args.get("per_page", 30, type=int) or 30, 10), 100)
    tx_type = (request.args.get("type", "all") or "all").strip().lower()
    query = WalletTransaction.query
    if tx_type != "all":
        query = query.filter(WalletTransaction.transaction_type == tx_type)
    pagination = query.order_by(WalletTransaction.created_at.desc(), WalletTransaction.id.desc()).paginate(
        page=page,
        per_page=per_page,
        error_out=False,
    )
    return render_template(
        "admin_panel/payments.html",
        payments=pagination.items,
        pagination=pagination,
        tx_type=tx_type,
        per_page=per_page,
    )


@admin_bp.route("/logs")
@login_required
@admin_required(min_role="support")
def logs():
    page = max(1, request.args.get("page", 1, type=int) or 1)
    per_page = min(max(request.args.get("per_page", 30, type=int) or 30, 10), 100)
    q = (request.args.get("q", "") or "").strip()
    query = ActivityLog.query
    if q:
        query = query.filter(
            or_(
                ActivityLog.action.ilike(f"%{q}%"),
                ActivityLog.target_type.ilike(f"%{q}%"),
                ActivityLog.target_id.ilike(f"%{q}%"),
            )
        )
    pagination = query.order_by(ActivityLog.created_at.desc(), ActivityLog.id.desc()).paginate(
        page=page,
        per_page=per_page,
        error_out=False,
    )
    return render_template(
        "admin_panel/logs.html",
        logs=pagination.items,
        q=q,
        pagination=pagination,
        per_page=per_page,
    )


@admin_bp.route("/advanced")
@login_required
@admin_required(min_role="support")
def advanced():
    signature_requests = SignatureRequest.query.order_by(SignatureRequest.created_at.desc()).limit(80).all()
    bulk_batches = BulkBatch.query.order_by(BulkBatch.created_at.desc()).limit(80).all()
    api_usage = ApiUsageLog.query.order_by(ApiUsageLog.created_at.desc()).limit(120).all()
    cloud_connections = (
        CloudIntegrationConnection.query.order_by(CloudIntegrationConnection.updated_at.desc())
        .limit(120)
        .all()
    )
    privacy_logs = FileAccessLog.query.order_by(FileAccessLog.created_at.desc()).limit(120).all()
    compliance_reports = ComplianceReport.query.order_by(ComplianceReport.created_at.desc()).limit(80).all()
    workspaces = TeamWorkspace.query.order_by(TeamWorkspace.created_at.desc()).limit(80).all()
    return render_template(
        "admin_panel/advanced.html",
        signature_requests=signature_requests,
        bulk_batches=bulk_batches,
        api_usage=api_usage,
        cloud_connections=cloud_connections,
        privacy_logs=privacy_logs,
        compliance_reports=compliance_reports,
        workspaces=workspaces,
    )

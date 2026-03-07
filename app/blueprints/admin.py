from __future__ import annotations

from flask import Blueprint, flash, redirect, render_template, request, url_for
from flask_login import login_required

from app.extensions import db
from app.models import ToolCatalog, User
from app.services.analytics_service import AnalyticsService
from app.services.auth_service import admin_required

admin_bp = Blueprint("admin", __name__)


@admin_bp.route("/")
@login_required
@admin_required
def dashboard():
    summary = AnalyticsService.admin_summary()
    tool_usage = AnalyticsService.most_used_tools()
    recent_logs = AnalyticsService.recent_logs()
    recent_payments = AnalyticsService.payment_rows()
    tools = ToolCatalog.query.order_by(ToolCatalog.is_enabled.desc(), ToolCatalog.name.asc()).limit(50).all()
    return render_template(
        "admin_panel/dashboard.html",
        summary=summary,
        tool_usage=tool_usage,
        recent_logs=recent_logs,
        recent_payments=recent_payments,
        tools=tools,
    )


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
    rows = AnalyticsService.recent_logs(limit=200)
    return render_template("admin_panel/logs.html", logs=rows)

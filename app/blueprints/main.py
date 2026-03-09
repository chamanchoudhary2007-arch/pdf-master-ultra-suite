from __future__ import annotations

import csv
import json
from datetime import datetime
from io import StringIO
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

from flask import (
    Blueprint,
    Response,
    abort,
    current_app,
    flash,
    jsonify,
    redirect,
    render_template,
    request,
    send_file,
    url_for,
)
from flask_login import current_user, login_required
from itsdangerous import BadSignature, URLSafeSerializer
from markupsafe import Markup, escape
from werkzeug.exceptions import HTTPException

from app.extensions import csrf, db, limiter
from app.models import Job, ManagedFile, Payment, ToolCatalog, User
from app.services import (
    AnalyticsService,
    AuthService,
    CatalogService,
    PaymentGatewayService,
    PrivacyService,
    PricingService,
    ShareService,
    StorageService,
    SubscriptionService,
)
from app.services.url_service import UrlService

main_bp = Blueprint("main", __name__)

_BILLING_PAYMENT_LINK_SALT = "billing-payment-link-v1"
_BILLING_PAYMENT_LINK_MAX_AGE_SECONDS = 7 * 24 * 60 * 60


def _build_callback_url() -> str:
    callback_url = current_app.config.get("RAZORPAY_CALLBACK_URL") or url_for(
        "main.razorpay_callback",
        _external=True,
    )
    if callback_url and not callback_url.lower().startswith("http"):
        callback_url = f"{request.url_root.rstrip('/')}/{callback_url.lstrip('/')}"
    return callback_url


def _payment_link_serializer() -> URLSafeSerializer:
    secret_key = (current_app.config.get("SECRET_KEY") or "").strip()
    if not secret_key:
        raise ValueError("SECRET_KEY is required for payment links.")
    return URLSafeSerializer(secret_key, salt=_BILLING_PAYMENT_LINK_SALT)


def _encode_payment_link_token(order_id: str, user_id: int) -> str:
    payload = {
        "order_id": (order_id or "").strip(),
        "user_id": int(user_id),
        "issued_at": int(datetime.utcnow().timestamp()),
    }
    return _payment_link_serializer().dumps(payload)


def _decode_payment_link_token(token: str) -> dict | None:
    try:
        payload = _payment_link_serializer().loads((token or "").strip())
    except BadSignature:
        return None
    if not isinstance(payload, dict):
        return None
    order_id = (payload.get("order_id") or "").strip()
    try:
        user_id = int(payload.get("user_id") or 0)
        issued_at = int(payload.get("issued_at") or 0)
    except (TypeError, ValueError):
        return None
    if not order_id or user_id <= 0 or issued_at <= 0:
        return None
    age_seconds = int(datetime.utcnow().timestamp()) - issued_at
    if age_seconds < 0 or age_seconds > _BILLING_PAYMENT_LINK_MAX_AGE_SECONDS:
        return None
    return {
        "order_id": order_id,
        "user_id": user_id,
        "issued_at": issued_at,
    }


@main_bp.route("/google-login")
def google_login_entry():
    return redirect(url_for("auth.google_login", **request.args), code=302)


@main_bp.route("/google-auth")
@main_bp.route("/google-callback")
def google_auth_callback():
    return redirect(url_for("auth.google_auth", **request.args), code=302)

MAIN_TOOL_HUB_DEFINITIONS = [
    {
        "slug": "convert-suite",
        "title": "Convert Suite",
        "icon_name": "bi-arrow-left-right",
        "description": "Convert between PDF, Office and image formats.",
        "tool_keys": [
            "images_to_pdf",
            "jpg_to_pdf",
            "png_to_pdf",
            "webp_to_pdf",
            "heic_to_pdf",
            "svg_to_pdf",
            "word_to_pdf",
            "excel_to_pdf",
            "powerpoint_to_pdf",
            "pdf_to_word",
            "pdf_to_excel",
            "pdf_to_ppt",
            "pdf_to_images",
            "pdf_to_jpg",
            "pdf_to_png",
            "pdf_to_text",
            "pdf_to_html",
        ],
    },
    {
        "slug": "organize-suite",
        "title": "Organize Suite",
        "icon_name": "bi-diagram-3",
        "description": "Merge, split, extract and reorder pages quickly.",
        "tool_keys": [
            "merge_pdf",
            "alternate_mix_pdf",
            "split_pdf",
            "split_by_pages",
            "split_by_bookmarks",
            "split_in_half",
            "split_by_size",
            "split_by_text",
            "extract_pages",
            "delete_pages",
            "reorder_pdf",
            "organize_pdf",
            "rotate_pdf",
            "rotate_pages",
            "create_bookmarks",
        ],
    },
    {
        "slug": "edit-suite",
        "title": "Edit Suite",
        "icon_name": "bi-pencil-square",
        "description": "Edit, watermark, annotate and format PDF pages.",
        "tool_keys": [
            "edit_pdf",
            "pdf_editor",
            "fill_sign_pdf",
            "create_forms",
            "watermark_pdf",
            "page_numbers",
            "header_footer",
            "crop_pdf",
            "resize_pdf",
            "flatten_pdf",
            "grayscale_pdf",
            "extract_images_pdf",
            "remove_annotations_pdf",
            "edit_metadata_pdf",
            "remove_metadata",
            "digital_signature",
            "sign_pdf",
        ],
    },
    {
        "slug": "security-suite",
        "title": "Security Suite",
        "icon_name": "bi-shield-lock",
        "description": "Protect, unlock and secure sensitive documents.",
        "tool_keys": [
            "secure_pdf",
            "protect_pdf",
            "unlock_pdf",
            "redact_pdf",
            "compare_pdf",
            "bates_numbering",
            "repair_pdf",
        ],
    },
    {
        "slug": "scan-ai-suite",
        "title": "Scan + AI Suite",
        "icon_name": "bi-robot",
        "description": "OCR, scanner and AI-powered document workflows.",
        "tool_keys": [
            "document_scanner",
            "scan_to_pdf",
            "deskew_scan_pdf",
            "deskew_pdf",
            "ocr_pdf",
            "smart_pdf_pipeline",
            "ai_document_tools",
            "translate_pdf",
            "n_up_pdf",
        ],
    },
    {
        "slug": "productivity-suite",
        "title": "Productivity Suite",
        "icon_name": "bi-grid-1x2",
        "description": "Study, office, templates, cloud and sharing tools.",
        "tool_keys": [
            "office_mode",
            "student_mode",
            "study_pack_pro",
            "teacher_toolkit",
            "government_office_suite",
            "document_templates",
            "image_utilities",
            "cloud_storage",
            "file_share",
        ],
    },
]


def _build_main_tool_hubs(
    tools: list[ToolCatalog],
    *,
    include_remaining: bool = False,
) -> list[dict]:
    tool_map = {tool.tool_key: tool for tool in tools}
    hubs: list[dict] = []
    used_keys: set[str] = set()
    for definition in MAIN_TOOL_HUB_DEFINITIONS:
        hub_tools = [tool_map[key] for key in definition["tool_keys"] if key in tool_map]
        if not hub_tools:
            continue
        used_keys.update(tool.tool_key for tool in hub_tools)
        hubs.append(
            {
                "slug": definition["slug"],
                "title": definition["title"],
                "icon_name": definition["icon_name"],
                "description": definition["description"],
                "tools": hub_tools,
            }
        )

    if include_remaining:
        remaining_tools = [tool for tool in tools if tool.tool_key not in used_keys]
        if remaining_tools:
            hubs.append(
                {
                    "slug": "additional-tools",
                    "title": "Additional Tools",
                    "icon_name": "bi-grid-3x3-gap",
                    "description": "Filtered results outside the main categories.",
                    "tools": remaining_tools[:30],
                }
            )
    return hubs


@main_bp.route("/")
def landing():
    all_enabled_tools = ToolCatalog.query.filter_by(is_enabled=True).order_by(ToolCatalog.name.asc()).all()
    featured_tools = all_enabled_tools[:12]
    main_tool_hubs = _build_main_tool_hubs(all_enabled_tools)
    summary = AnalyticsService.admin_summary()
    return render_template(
        "landing.html",
        featured_tools=featured_tools,
        summary=summary,
        main_tool_hubs=main_tool_hubs,
        payment_mode=(current_app.config.get("PAYMENT_MODE") or "demo").strip().lower(),
    )


@main_bp.route("/robots.txt")
def robots_txt():
    content = "\n".join(
        [
            "User-agent: *",
            "Allow: /",
            "Disallow: /admin/",
            "Disallow: /dashboard",
            "Disallow: /tools/",
            "Disallow: /workspace/",
            f"Sitemap: {UrlService.build_external_url('main.sitemap_xml')}",
        ]
    )
    return Response(content, mimetype="text/plain")


@main_bp.route("/manifest.webmanifest")
def web_manifest():
    return send_file(
        Path(current_app.static_folder) / "manifest.webmanifest",
        mimetype="application/manifest+json",
        max_age=3600,
    )


@main_bp.route("/service-worker.js")
def service_worker():
    response = send_file(
        Path(current_app.static_folder) / "service-worker.js",
        mimetype="application/javascript",
        max_age=300,
    )
    response.headers["Service-Worker-Allowed"] = "/"
    return response


@main_bp.route("/sitemap.xml")
def sitemap_xml():
    pages = [
        {
            "loc": UrlService.build_external_url("main.landing"),
            "changefreq": "daily",
            "priority": "1.0",
        },
        {
            "loc": UrlService.build_external_url("auth.login"),
            "changefreq": "monthly",
            "priority": "0.7",
        },
        {
            "loc": UrlService.build_external_url("auth.signup"),
            "changefreq": "monthly",
            "priority": "0.8",
        },
    ]
    return Response(
        render_template("sitemap.xml", pages=pages),
        mimetype="application/xml",
    )


@main_bp.route("/dashboard")
@login_required
def dashboard():
    search = request.args.get("q", "").strip()
    category = request.args.get("category", "").strip()
    tools = CatalogService.get_enabled_tools(search=search, category=category)
    main_tool_hubs = _build_main_tool_hubs(
        tools,
        include_remaining=bool(search or category),
    )
    favorite_keys = CatalogService.favorite_keys_for_user(current_user.id)
    recent_jobs = (
        Job.query.filter_by(user_id=current_user.id)
        .order_by(Job.created_at.desc())
        .limit(10)
        .all()
    )
    recent_keys = CatalogService.recent_tool_keys(current_user.id, limit=8)
    if recent_keys:
        recent_tool_map = {
            tool.tool_key: tool
            for tool in ToolCatalog.query.filter(ToolCatalog.tool_key.in_(recent_keys)).all()
        }
        recent_tools = [recent_tool_map[key] for key in recent_keys if key in recent_tool_map]
    else:
        recent_tools = []
    output_files = (
        ManagedFile.query.filter_by(user_id=current_user.id, storage_kind="output", is_deleted=False)
        .order_by(ManagedFile.created_at.desc())
        .limit(8)
        .all()
    )
    cloud_files = StorageService.list_cloud_files(current_user.id)[:8]
    active_subscription = SubscriptionService.active_subscription_for_user(current_user.id)
    plan_catalog = SubscriptionService.plan_catalog()
    is_premium_user = SubscriptionService.is_user_premium(current_user)
    total_jobs = Job.query.filter_by(user_id=current_user.id).count()
    completed_jobs = Job.query.filter_by(user_id=current_user.id, status="completed").count()
    failed_jobs = Job.query.filter_by(user_id=current_user.id, status="failed").count()
    premium_tools = [tool for tool in tools if tool.is_subscription_only][:6]
    essential_tool_order = [
        "merge_pdf",
        "compress_pdf",
        "images_to_pdf",
        "pdf_to_word",
        "edit_pdf",
        "protect_pdf",
        "unlock_pdf",
        "split_pdf",
        "ocr_pdf",
        "watermark_pdf",
    ]
    essential_descriptions = {
        "merge_pdf": "Join multiple PDFs into one file. Free up to 3 files.",
        "compress_pdf": "Reduce PDF file size for fast sharing and uploads.",
        "images_to_pdf": "Convert photos and scanned images into clean PDFs.",
        "pdf_to_word": "Convert PDF into editable DOCX documents.",
        "edit_pdf": "Add text, shapes, and annotations to PDF pages.",
        "protect_pdf": "Lock sensitive documents with password protection.",
        "unlock_pdf": "Remove password protection from permitted PDFs.",
        "split_pdf": "Extract selected pages from large PDF documents.",
        "ocr_pdf": "Extract searchable text from scans and images.",
        "watermark_pdf": "Add custom text or image watermark to each page.",
    }
    essential_icon_overrides = {
        "merge_pdf": "bi-files",
        "compress_pdf": "bi-file-zip",
        "images_to_pdf": "bi-file-earmark-image",
        "pdf_to_word": "bi-file-earmark-word",
        "edit_pdf": "bi-pencil-square",
        "protect_pdf": "bi-shield-lock",
        "unlock_pdf": "bi-unlock",
        "split_pdf": "bi-scissors",
        "ocr_pdf": "bi-binoculars",
        "watermark_pdf": "bi-droplet-half",
    }
    essential_pro_keys = {
        "compress_pdf",
        "pdf_to_word",
        "edit_pdf",
        "protect_pdf",
        "unlock_pdf",
        "split_pdf",
        "ocr_pdf",
        "watermark_pdf",
    }
    essential_tool_lookup = {
        tool.tool_key: tool
        for tool in ToolCatalog.query.filter(ToolCatalog.tool_key.in_(essential_tool_order)).all()
    }
    essential_tools = []
    for key in essential_tool_order:
        tool = essential_tool_lookup.get(key)
        if not tool:
            continue
        is_pro_tool = key in essential_pro_keys
        essential_tools.append(
            {
                "tool": tool,
                "short_description": essential_descriptions.get(key, tool.description),
                "icon_name": essential_icon_overrides.get(key, tool.icon_name),
                "is_pro": is_pro_tool,
                "premium_locked": is_pro_tool and not (is_premium_user or current_user.is_admin),
                "access_label": "FREE (up to 3 files)" if key == "merge_pdf" else "FREE",
            }
        )
    return render_template(
        "dashboard.html",
        tools=tools,
        main_tool_hubs=main_tool_hubs,
        favorite_keys=favorite_keys,
        recent_jobs=recent_jobs,
        recent_tools=recent_tools,
        output_files=output_files,
        cloud_files=cloud_files,
        search=search,
        active_category=category,
        active_subscription=active_subscription,
        is_premium_user=is_premium_user,
        plan_catalog=plan_catalog,
        premium_tools=premium_tools,
        essential_tools=essential_tools,
        total_jobs=total_jobs,
        completed_jobs=completed_jobs,
        failed_jobs=failed_jobs,
    )


@main_bp.route("/settings")
@login_required
def settings():
    active_subscription = SubscriptionService.active_subscription_for_user(current_user.id)
    plan_catalog = SubscriptionService.plan_catalog()
    active_subscription_summary = (
        SubscriptionService.subscription_status_summary(active_subscription)
        if active_subscription
        else None
    )
    active_plan_key = (
        active_subscription_summary.get("plan_key")
        if active_subscription_summary
        else None
    )
    plan_view_models = SubscriptionService.plan_view_models(active_plan_key=active_plan_key)
    referral_code = AuthService.ensure_user_referral_code(current_user)
    referral_step = AuthService.REFERRAL_REWARD_STEP
    total_referrals = int(current_user.total_referrals or 0)
    referrals_to_next_reward = referral_step - (total_referrals % referral_step)
    referral_progress_percent = int(((total_referrals % referral_step) / referral_step) * 100)
    share_link = url_for("main.landing", _external=True)
    custom_daily_rate_paise = SubscriptionService.custom_daily_rate_paise()
    custom_min_days, custom_max_days = SubscriptionService.custom_days_range()
    custom_quick_chips = [
        day
        for day in SubscriptionService.CUSTOM_QUICK_CHIPS
        if custom_min_days <= day <= custom_max_days
    ]
    recent_transactions = SubscriptionService.list_user_transactions(current_user.id, limit=8)
    generated_reset_key = AuthService.ensure_password_reset_key(current_user)
    if generated_reset_key:
        flash(
            Markup(
                "Your new 4-digit Password Reset Key is "
                f"<strong>{escape(generated_reset_key)}</strong>. "
                "Save it safely in your personal notes."
            ),
            "warning",
        )
    password_reset_help_email = (
        current_app.config.get("PASSWORD_RESET_HELP_EMAIL")
        or current_app.config.get("ADMIN_EMAIL")
        or "pdfmasterultrasuite@gmail.com"
    ).strip()
    password_reset_help_link = UrlService.gmail_compose_url(
        to_email=password_reset_help_email,
        subject=f"{current_app.config['APP_NAME']} - Help to forget key",
        body=(
            "Hello Admin,\n\n"
            "I forgot my 4-digit password reset key. Please help me regain access.\n"
            f"Account email: {current_user.email}\n"
            "Reason: \n\n"
            "Thanks."
        ),
    )

    return render_template(
        "settings.html",
        active_subscription=active_subscription,
        active_subscription_summary=active_subscription_summary,
        plan_catalog=plan_catalog,
        plan_view_models=plan_view_models,
        premium_benefits=SubscriptionService.PREMIUM_BENEFITS,
        active_price_profile=SubscriptionService.active_price_profile_key(),
        expiring_soon_days=SubscriptionService.expiring_soon_days(),
        referral_code=referral_code,
        total_referrals=total_referrals,
        referral_step=referral_step,
        referrals_to_next_reward=referrals_to_next_reward,
        referral_progress_percent=referral_progress_percent,
        share_link=share_link,
        recent_transactions=recent_transactions,
        custom_daily_rate_paise=custom_daily_rate_paise,
        custom_daily_rate_rupees=custom_daily_rate_paise / 100,
        custom_min_days=custom_min_days,
        custom_max_days=custom_max_days,
        custom_quick_chips=custom_quick_chips,
        payment_mode=(current_app.config.get("PAYMENT_MODE") or "demo").strip().lower(),
        password_reset_icon_choices=AuthService.password_reset_icon_choices(),
        password_reset_help_email=password_reset_help_email,
        password_reset_help_link=password_reset_help_link,
    )


@main_bp.route("/settings/billing/transactions")
@login_required
def billing_transactions():
    page = request.args.get("page", 1, type=int)
    per_page = request.args.get("per_page", 20, type=int)
    per_page = min(max(per_page or 20, 10), 100)
    pagination, transactions = SubscriptionService.paginated_user_transactions(
        current_user.id,
        page=page or 1,
        per_page=per_page,
    )
    active_subscription = SubscriptionService.active_subscription_for_user(current_user.id)
    active_subscription_summary = (
        SubscriptionService.subscription_status_summary(active_subscription)
        if active_subscription
        else None
    )
    return render_template(
        "billing_transactions.html",
        transactions=transactions,
        pagination=pagination,
        per_page=per_page,
        active_subscription_summary=active_subscription_summary,
    )


@main_bp.route("/settings/profile-photo", methods=["POST"])
@login_required
def update_profile_photo():
    redirect_target = url_for("main.settings", tab="profile", _anchor="profile-settings")
    upload = request.files.get("profile_photo")
    if not upload or not upload.filename:
        flash("Please select an image for profile photo.", "danger")
        return redirect(redirect_target)
    try:
        previous_photos = (
            ManagedFile.query.filter_by(
                user_id=current_user.id,
                label="profile_photo",
                is_deleted=False,
            )
            .order_by(ManagedFile.created_at.desc())
            .all()
        )
        for file_record in previous_photos:
            try:
                absolute = StorageService.absolute_path(file_record)
                if absolute.exists():
                    absolute.unlink()
            except Exception:
                pass
            file_record.is_deleted = True
        StorageService.save_uploaded_file(
            upload,
            user_id=current_user.id,
            kind="cloud",
            label="profile_photo",
        )
        flash("Profile photo updated.", "success")
    except Exception as exc:
        flash(str(exc), "danger")
    return redirect(redirect_target)


@main_bp.route("/settings/password-reset-key", methods=["POST"])
@login_required
def update_password_reset_key():
    action = (request.form.get("action") or "set").strip().lower()
    selected_icon = (request.form.get("reset_key_icon") or "").strip()
    redirect_target = url_for("main.settings", tab="security", _anchor="profile-security")
    try:
        if action == "generate":
            new_key = AuthService.generate_password_reset_key()
            AuthService.set_password_reset_key(
                current_user,
                new_key,
                icon_name=selected_icon,
            )
            flash(
                Markup(
                    "New 4-digit Password Reset Key generated: "
                    f"<strong>{escape(new_key)}</strong>. Save it now."
                ),
                "success",
            )
            return redirect(redirect_target)

        if action == "icon":
            if not AuthService.is_valid_password_reset_icon(selected_icon):
                raise ValueError("Please select a valid reset key icon.")
            current_user.password_reset_key_icon = selected_icon
            db.session.commit()
            flash("Password Reset Key icon updated.", "success")
            return redirect(redirect_target)

        reset_key = (request.form.get("reset_key") or "").strip()
        confirm_reset_key = (request.form.get("confirm_reset_key") or "").strip()
        if reset_key != confirm_reset_key:
            raise ValueError("Reset key and confirm key do not match.")
        AuthService.set_password_reset_key(
            current_user,
            reset_key,
            icon_name=selected_icon,
        )
        flash("Password Reset Key updated successfully.", "success")
    except Exception as exc:
        flash(str(exc), "danger")
    return redirect(redirect_target)


@main_bp.route("/all-tools")
@login_required
def all_tools():
    tools = CatalogService.get_enabled_tools()
    tool_map = {tool.tool_key: tool for tool in tools}
    favorite_keys = CatalogService.favorite_keys_for_user(current_user.id)
    recent_keys = CatalogService.recent_tool_keys(current_user.id, limit=12)

    def pick(keys: list[str]) -> list[ToolCatalog]:
        return [tool_map[key] for key in keys if key in tool_map]

    group_key_map = {
        "Favorites": "favorites",
        "Frequently used": "frequently-used",
        "Convert to PDF": "convert-to-pdf",
        "Convert from PDF": "convert-from-pdf",
        "Organize PDF": "organize-pdf",
        "Edit PDF": "edit-pdf",
        "Security + OCR": "security-ocr",
        "More": "more",
        "Desktop": "desktop",
    }
    group_meta = {
        "favorites": {
            "icon": "bi-star-fill",
            "description": "Your pinned tools for one-tap access.",
        },
        "frequently-used": {
            "icon": "bi-clock-history",
            "description": "Recently used tools for faster repeat workflows.",
        },
        "convert-to-pdf": {
            "icon": "bi-file-earmark-plus",
            "description": "Bring Office, images, and scans into PDF format.",
        },
        "convert-from-pdf": {
            "icon": "bi-arrow-repeat",
            "description": "Export PDF content into editable formats quickly.",
        },
        "organize-pdf": {
            "icon": "bi-diagram-3",
            "description": "Merge, split, reorder, and structure PDF pages.",
        },
        "edit-pdf": {
            "icon": "bi-pencil-square",
            "description": "Edit text, layout, signatures, and page appearance.",
        },
        "security-ocr": {
            "icon": "bi-shield-lock",
            "description": "Protect files, unlock PDFs, and run OCR operations.",
        },
        "more": {
            "icon": "bi-grid-3x3-gap",
            "description": "AI, office, and specialized productivity tools.",
        },
        "desktop": {
            "icon": "bi-laptop",
            "description": "Cloud, sharing, and scanner workspace utilities.",
        },
    }

    section_data = [
        ("Favorites", pick(favorite_keys)),
        ("Frequently used", pick(recent_keys)),
        (
            "Convert to PDF",
            pick(
                [
                    "images_to_pdf",
                    "jpg_to_pdf",
                    "png_to_pdf",
                    "webp_to_pdf",
                    "heic_to_pdf",
                    "svg_to_pdf",
                    "tiff_to_pdf",
                    "word_to_pdf",
                    "doc_to_pdf",
                    "docx_to_pdf",
                    "powerpoint_to_pdf",
                    "ppt_to_pdf",
                    "pptx_to_pdf",
                    "excel_to_pdf",
                    "xls_to_pdf",
                    "xlsx_to_pdf",
                    "html_to_pdf",
                    "text_to_pdf",
                    "document_scanner",
                    "document_templates",
                ]
            ),
        ),
        (
            "Convert from PDF",
            pick(
                [
                    "pdf_to_word",
                    "pdf_to_docx",
                    "pdf_to_excel",
                    "pdf_to_xlsx",
                    "pdf_to_ppt",
                    "pdf_to_pptx",
                    "pdf_to_images",
                    "pdf_to_jpg",
                    "pdf_to_png",
                    "pdf_to_text",
                    "pdf_to_html",
                    "pdf_to_rtf",
                ]
            ),
        ),
        (
            "Organize PDF",
            pick(
                [
                    "merge_pdf",
                    "alternate_mix_pdf",
                    "split_pdf",
                    "split_by_pages",
                    "split_by_bookmarks",
                    "split_in_half",
                    "split_by_size",
                    "split_by_text",
                    "extract_pages",
                    "delete_pages",
                    "reorder_pdf",
                    "organize_pdf",
                    "rotate_pdf",
                    "rotate_pages",
                    "create_bookmarks",
                ]
            ),
        ),
        (
            "Edit PDF",
            pick(
                [
                    "watermark_pdf",
                    "page_numbers",
                    "header_footer",
                    "pdf_editor",
                    "edit_pdf",
                    "fill_sign_pdf",
                    "create_forms",
                    "crop_pdf",
                    "resize_pdf",
                    "flatten_pdf",
                    "grayscale_pdf",
                    "extract_images_pdf",
                    "remove_annotations_pdf",
                    "edit_metadata_pdf",
                    "remove_metadata",
                    "digital_signature",
                    "sign_pdf",
                ]
            ),
        ),
        (
            "Security + OCR",
            pick(
                [
                    "secure_pdf",
                    "protect_pdf",
                    "unlock_pdf",
                    "redact_pdf",
                    "compare_pdf",
                    "bates_numbering",
                    "repair_pdf",
                    "ocr_pdf",
                    "scan_to_pdf",
                    "deskew_scan_pdf",
                    "deskew_pdf",
                    "n_up_pdf",
                    "smart_pdf_pipeline",
                ]
            ),
        ),
        (
            "More",
            pick(
                [
                    "image_utilities",
                    "office_mode",
                    "ai_document_tools",
                    "translate_pdf",
                    "student_mode",
                    "study_pack_pro",
                    "teacher_toolkit",
                    "government_office_suite",
                ]
            ),
        ),
        (
            "Desktop",
            pick(
                [
                    "cloud_storage",
                    "file_share",
                    "document_scanner",
                ]
            ),
        ),
    ]

    featured_sections: list[dict] = []
    tool_groups: list[dict] = []
    for title, section_tools in section_data:
        if not section_tools:
            continue
        section_id = group_key_map.get(title, title.lower().replace(" ", "-"))
        meta = group_meta.get(section_id, {})
        group = {
            "id": section_id,
            "title": title,
            "tools": section_tools,
            "icon": meta.get("icon", "bi-grid-3x3-gap"),
            "description": meta.get("description", "Browse related tools."),
            "search_index": " ".join(
                [
                    title,
                    *[tool.name for tool in section_tools],
                    *[tool.description for tool in section_tools],
                ]
            ).lower(),
        }
        if section_id in {"favorites", "frequently-used"}:
            featured_sections.append(group)
        else:
            tool_groups.append(group)

    for group in tool_groups:
        group["default_open"] = False

    return render_template(
        "all_tools.html",
        featured_sections=featured_sections,
        tool_groups=tool_groups,
        total_tool_count=len(tools),
        favorite_keys=favorite_keys,
    )


@main_bp.route("/wallet/top-up", methods=["POST"])
@login_required
def wallet_top_up():
    redirect_target = request.referrer or url_for("main.settings")
    if PaymentGatewayService.is_live_mode():
        flash(
            "Wallet top-up via mock mode is disabled in live payment mode.",
            "warning",
        )
        return redirect(redirect_target)
    amount_raw = request.form.get("amount_rupees", "500").strip()
    try:
        amount_rupees = int(amount_raw)
    except ValueError:
        flash("Invalid top-up amount.", "danger")
        return redirect(redirect_target)
    amount_rupees = max(5, min(50000, amount_rupees))
    try:
        PricingService.top_up_wallet(
            current_user,
            amount_paise=amount_rupees * 100,
            reference=f"TOPUP-{current_user.id}",
        )
    except Exception as exc:
        flash(str(exc), "danger")
    else:
        flash("Wallet balance updated in demo payment mode.", "success")
    return redirect(redirect_target)


@main_bp.route("/billing/subscribe", methods=["POST"])
@login_required
@limiter.limit("12 per minute", methods=["POST"])
def subscribe():
    plan_key = (request.form.get("plan_key", "") or "").strip()
    custom_days = (request.form.get("custom_days", "") or "").strip() or None
    if not plan_key:
        return jsonify({"error": "Plan key is required."}), 400
    try:
        plan = SubscriptionService.resolve_plan_purchase(plan_key, custom_days=custom_days)
        callback_url = _build_callback_url()
        order = PaymentGatewayService.create_subscription_order(
            current_user,
            plan["plan_key"],
            custom_days=plan.get("custom_days"),
        )
    except Exception as exc:
        return jsonify({"error": str(exc)}), 400

    if PaymentGatewayService.is_demo_mode():
        return jsonify(
            {
                "mode": "demo",
                "message": "Demo payment mode is enabled. No real charge will be made.",
                "order_id": order["id"],
                "amount": int(order.get("amount") or plan["price_paise"]),
                "currency": order.get("currency") or current_app.config["RAZORPAY_CURRENCY"],
                "plan_name": plan["name"],
                "plan_key": plan["plan_key"],
                "duration_days": int(plan["duration_days"]),
                "demo_success_url": url_for(
                    "main.demo_payment_complete",
                    order_id=order["id"],
                    state="success",
                ),
                "demo_failure_url": url_for(
                    "main.demo_payment_complete",
                    order_id=order["id"],
                    state="failed",
                ),
            }
        )
    payment_link_token = _encode_payment_link_token(order["id"], current_user.id)
    payment_link_url = url_for(
        "main.billing_pay_link",
        token=payment_link_token,
        _external=True,
    )
    return jsonify(
        {
            "mode": "live",
            "key_id": current_app.config["RAZORPAY_KEY_ID"],
            "order_id": order["id"],
            "amount": int(order.get("amount") or plan["price_paise"]),
            "currency": order.get("currency") or current_app.config["RAZORPAY_CURRENCY"],
            "plan_name": plan["name"],
            "plan_key": plan["plan_key"],
            "duration_days": int(plan["duration_days"]),
            "callback_url": callback_url,
            "prefill": {
                "name": current_user.full_name,
                "email": current_user.email,
            },
            "confirm_url": url_for("main.razorpay_confirm"),
            "status_poll_url": url_for("main.razorpay_order_status", order_id=order["id"]),
            "client_failure_url": url_for("main.razorpay_client_failure"),
            "status_url_on_dismiss": url_for(
                "main.billing_status",
                state="pending",
                message="Payment verification is in progress. Please wait a moment.",
                order_id=order["id"],
            ),
            "payment_link_url": payment_link_url,
            "app_name": current_app.config["APP_NAME"],
        }
    )


@main_bp.route("/billing/demo/<order_id>/complete")
@login_required
def demo_payment_complete(order_id: str):
    if PaymentGatewayService.is_live_mode():
        abort(404)
    state = (request.args.get("state", "success") or "success").strip().lower()
    if state not in {"success", "failed"}:
        state = "failed"

    if state == "failed":
        PaymentGatewayService.mark_payment_failed(
            order_id,
            error_message="Demo payment marked as failed by user.",
        )
        return redirect(
            url_for(
                "main.billing_status",
                state="failed",
                message="Demo payment was cancelled.",
                order_id=order_id,
            )
        )

    try:
        subscription = PaymentGatewayService.confirm_demo_payment(current_user, order_id)
    except Exception as exc:
        PaymentGatewayService.mark_payment_failed(
            order_id,
            error_message=str(exc),
        )
        return redirect(
            url_for(
                "main.billing_status",
                state="failed",
                message=str(exc),
                order_id=order_id,
            )
        )

    return redirect(
        url_for(
            "main.billing_status",
            state="success",
            plan=subscription.plan_name,
            message="Demo payment completed successfully.",
            order_id=order_id,
            payment_id="demo",
        )
    )


@main_bp.route("/billing/razorpay/callback", methods=["POST"])
@csrf.exempt
def razorpay_callback():
    payment_id = (request.form.get("razorpay_payment_id", "") or "").strip()
    order_id = (request.form.get("razorpay_order_id", "") or "").strip()
    signature = (request.form.get("razorpay_signature", "") or "").strip()
    signature_verified = False
    try:
        if not payment_id or not order_id or not signature:
            raise ValueError("Incomplete payment response.")
        PaymentGatewayService.verify_signature(order_id, payment_id, signature)
        signature_verified = True
        order = PaymentGatewayService.fetch_order(order_id)
        payment_row = PaymentGatewayService.payment_row_for_order(order_id=order_id)
        PaymentGatewayService.validate_order_vs_payment_row(order, payment_row)
        notes = order.get("notes") or {}
        user_id = int(notes.get("user_id", "0") or (payment_row.user_id if payment_row else 0) or 0)
        plan_key = (notes.get("plan_key") or (payment_row.plan_key if payment_row else "") or "").strip()
        custom_days = (notes.get("custom_days") or "").strip() or None
        user = db.session.get(User, user_id)
        if not user:
            raise ValueError("User not found for this payment.")
        plan = SubscriptionService.resolve_plan_purchase(plan_key, custom_days=custom_days)
        if int(order.get("amount") or 0) != int(plan["price_paise"]):
            raise ValueError("Order amount mismatch.")
        order_currency = (order.get("currency") or "").strip().upper()
        expected_currency = current_app.config["RAZORPAY_CURRENCY"].upper()
        if order_currency != expected_currency:
            raise ValueError("Order currency mismatch.")

        subscription = SubscriptionService.activate_after_gateway_payment(
            user=user,
            plan_key=plan["plan_key"],
            payment_id=payment_id,
            order_id=order_id,
            custom_days=plan.get("custom_days"),
            gateway_payload={
                "order": order,
                "callback_fields": {
                    "razorpay_payment_id": payment_id,
                    "razorpay_order_id": order_id,
                    "razorpay_signature": signature,
                },
            },
        )
    except Exception as exc:
        error_text = str(exc)
        normalized_error = error_text.lower()
        hard_fail_markers = (
            "signature",
            "incomplete payment response",
            "order amount mismatch",
            "order currency mismatch",
            "user not found for this payment",
        )
        should_mark_failed = (not signature_verified) or any(marker in normalized_error for marker in hard_fail_markers)
        if signature_verified and not should_mark_failed:
            return redirect(
                url_for(
                    "main.billing_status",
                    state="pending",
                    message="Payment received. Verification is in progress.",
                    order_id=order_id,
                    payment_id=payment_id,
                )
            )
        try:
            if should_mark_failed:
                PaymentGatewayService.mark_payment_failed(
                    order_id,
                    payment_id=payment_id,
                    error_message=error_text,
                )
        except Exception:
            current_app.logger.exception("Failed to persist payment failure state")
        return redirect(
            url_for(
                "main.billing_status",
                state="failed",
                message=error_text,
                order_id=order_id,
                payment_id=payment_id,
            )
        )
    return redirect(
        url_for(
            "main.billing_status",
            state="success",
            plan=subscription.plan_name,
            payment_id=payment_id,
            order_id=order_id,
        )
    )


@main_bp.route("/billing/razorpay/confirm", methods=["POST"])
@login_required
@limiter.limit("20 per minute", methods=["POST"])
def razorpay_confirm():
    payment_id = (request.form.get("razorpay_payment_id", "") or "").strip()
    order_id = (request.form.get("razorpay_order_id", "") or "").strip()
    signature = (request.form.get("razorpay_signature", "") or "").strip()

    try:
        if not payment_id or not order_id or not signature:
            raise ValueError("Incomplete payment response.")
        PaymentGatewayService.verify_signature(order_id, payment_id, signature)
        payment_row = PaymentGatewayService.payment_row_for_order(
            order_id=order_id,
            user_id=current_user.id,
        )
        if not payment_row:
            raise ValueError("Order not found for this account.")
        order = PaymentGatewayService.fetch_order(order_id)
        PaymentGatewayService.validate_order_vs_payment_row(
            order,
            payment_row,
            expected_user_id=current_user.id,
        )
        notes = order.get("notes") or {}
        order_user_id = int(notes.get("user_id", "0") or current_user.id)
        if order_user_id != current_user.id:
            raise ValueError("Order does not belong to current user.")

        plan_key = (notes.get("plan_key") or payment_row.plan_key or "").strip()
        custom_days = (notes.get("custom_days") or "").strip() or None
        if (
            not custom_days
            and plan_key == "pro_custom"
        ):
            if payment_row and int(payment_row.duration_days or 0) > 0:
                custom_days = str(int(payment_row.duration_days))
        plan = SubscriptionService.resolve_plan_purchase(plan_key, custom_days=custom_days)
        if int(order.get("amount") or 0) != int(plan["price_paise"]):
            raise ValueError("Order amount mismatch.")
        if (order.get("currency") or "").strip().upper() != current_app.config["RAZORPAY_CURRENCY"].upper():
            raise ValueError("Order currency mismatch.")

        subscription = SubscriptionService.activate_after_gateway_payment(
            user=current_user,
            plan_key=plan["plan_key"],
            payment_id=payment_id,
            order_id=order_id,
            custom_days=plan.get("custom_days"),
            gateway_payload={
                "order": order,
                "callback_fields": {
                    "razorpay_payment_id": payment_id,
                    "razorpay_order_id": order_id,
                    "razorpay_signature": signature,
                },
            },
        )
        return jsonify(
            {
                "state": "success",
                "message": "Your premium plan has been activated successfully.",
                "redirect_url": url_for(
                    "main.billing_status",
                    state="success",
                    plan=subscription.plan_name,
                    order_id=order_id,
                    payment_id=payment_id,
                ),
            }
        )
    except Exception as exc:
        current_app.logger.exception("Razorpay instant confirm failed. order_id=%s", order_id)
        existing_success = Payment.query.filter_by(
            user_id=current_user.id,
            razorpay_order_id=order_id,
            status="success",
        ).first()
        if existing_success:
            return jsonify(
                {
                    "state": "success",
                    "message": "Your premium plan has been activated successfully.",
                    "redirect_url": url_for(
                        "main.billing_status",
                        state="success",
                        plan=existing_success.plan_name,
                        order_id=order_id,
                        payment_id=existing_success.razorpay_payment_id or payment_id,
                    ),
                }
            )

        error_text = str(exc)
        normalized_error = error_text.lower()
        hard_fail_markers = (
            "signature",
            "incomplete payment response",
            "order amount mismatch",
            "order currency mismatch",
            "does not belong",
        )
        should_fail = any(marker in normalized_error for marker in hard_fail_markers)
        if should_fail:
            try:
                PaymentGatewayService.mark_payment_failed(
                    order_id,
                    payment_id=payment_id,
                    error_message=error_text,
                )
            except Exception:
                current_app.logger.exception("Failed to persist instant-confirm failure")
            return jsonify(
                {
                    "state": "failed",
                    "message": error_text,
                    "redirect_url": url_for(
                        "main.billing_status",
                        state="failed",
                        message=error_text,
                        order_id=order_id,
                        payment_id=payment_id,
                    ),
                }
            ), 400

        return jsonify(
            {
                "state": "pending",
                "message": "Payment received. Verification is in progress.",
                "redirect_url": url_for(
                    "main.billing_status",
                    state="pending",
                    message="Payment received. Verification is in progress.",
                    order_id=order_id,
                    payment_id=payment_id,
                ),
            }
        ), 202


@main_bp.route("/billing/razorpay/client-failure", methods=["POST"])
@login_required
@limiter.limit("30 per minute", methods=["POST"])
def razorpay_client_failure():
    order_id = (
        request.form.get("razorpay_order_id")
        or request.form.get("order_id")
        or ""
    ).strip()
    payment_id = (
        request.form.get("razorpay_payment_id")
        or request.form.get("payment_id")
        or ""
    ).strip()
    error_message = (
        request.form.get("error_message")
        or request.form.get("message")
        or "Payment could not be completed."
    ).strip()

    if not order_id:
        return jsonify({"state": "failed", "message": "Missing order id."}), 400

    payment_row = Payment.query.filter_by(
        user_id=current_user.id,
        razorpay_order_id=order_id,
    ).first()
    if not payment_row:
        return jsonify({"state": "failed", "message": "Order not found for this account."}), 404

    if (payment_row.status or "").strip().lower() == "success":
        return jsonify(
            {
                "state": "success",
                "message": "Your premium plan has been activated successfully.",
                "redirect_url": url_for(
                    "main.billing_status",
                    state="success",
                    plan=payment_row.plan_name,
                    order_id=order_id,
                    payment_id=payment_row.razorpay_payment_id or payment_id,
                ),
            }
        )

    if (payment_row.status or "").strip().lower() != "failed":
        try:
            PaymentGatewayService.mark_payment_failed(
                order_id,
                payment_id=payment_id,
                error_message=error_message,
            )
        except Exception:
            current_app.logger.exception("Failed to persist client-side payment failure. order_id=%s", order_id)

    return jsonify(
        {
            "state": "failed",
            "message": error_message,
            "redirect_url": url_for(
                "main.billing_status",
                state="failed",
                message=error_message,
                order_id=order_id,
                payment_id=payment_id,
            ),
        }
    )


@main_bp.route("/billing/razorpay/webhook", methods=["POST"])
@csrf.exempt
def razorpay_webhook():
    if PaymentGatewayService.is_demo_mode():
        return jsonify({"status": "ignored", "reason": "demo_mode"}), 200

    raw_body = request.get_data() or b""
    signature = (request.headers.get("X-Razorpay-Signature", "") or "").strip()
    try:
        PaymentGatewayService.verify_webhook_signature(raw_body, signature)
        event_payload = json.loads(raw_body.decode("utf-8"))
        event_name = (event_payload.get("event") or "").strip()
        if event_name not in {"payment.captured", "order.paid"}:
            return jsonify({"status": "ignored", "event": event_name}), 200

        payment_entity = (
            event_payload.get("payload", {})
            .get("payment", {})
            .get("entity", {})
        )
        order_id = (payment_entity.get("order_id") or "").strip()
        payment_id = (payment_entity.get("id") or "").strip()
        if not order_id or not payment_id:
            raise ValueError("Missing payment details in webhook payload.")

        order = PaymentGatewayService.fetch_order(order_id)
        payment_row = PaymentGatewayService.payment_row_for_order(order_id=order_id)
        PaymentGatewayService.validate_order_vs_payment_row(order, payment_row)
        notes = order.get("notes") or {}
        user_id = int(notes.get("user_id", "0") or (payment_row.user_id if payment_row else 0) or 0)
        plan_key = (notes.get("plan_key") or (payment_row.plan_key if payment_row else "") or "").strip()
        custom_days = (notes.get("custom_days") or "").strip() or None
        user = db.session.get(User, user_id)
        if not user:
            raise ValueError("User not found for webhook payment.")

        plan = SubscriptionService.resolve_plan_purchase(plan_key, custom_days=custom_days)
        if int(order.get("amount") or 0) != int(plan["price_paise"]):
            raise ValueError("Webhook order amount mismatch.")
        if (order.get("currency") or "").strip().upper() != current_app.config["RAZORPAY_CURRENCY"].upper():
            raise ValueError("Webhook order currency mismatch.")

        SubscriptionService.activate_after_gateway_payment(
            user=user,
            plan_key=plan["plan_key"],
            payment_id=payment_id,
            order_id=order_id,
            custom_days=plan.get("custom_days"),
            gateway_payload={
                "event": event_name,
                "order": order,
                "callback_fields": {
                    "razorpay_payment_id": payment_id,
                    "razorpay_order_id": order_id,
                    "razorpay_signature": signature,
                },
            },
        )
    except Exception as exc:
        current_app.logger.exception("Razorpay webhook processing failed")
        order_id = (
            (
                event_payload.get("payload", {})
                .get("payment", {})
                .get("entity", {})
                .get("order_id", "")
            )
            if "event_payload" in locals()
            else ""
        )
        payment_id = (
            (
                event_payload.get("payload", {})
                .get("payment", {})
                .get("entity", {})
                .get("id", "")
            )
            if "event_payload" in locals()
            else ""
        )
        if order_id:
            try:
                PaymentGatewayService.mark_payment_failed(
                    order_id,
                    payment_id=payment_id,
                    error_message=str(exc),
                )
            except Exception:
                current_app.logger.exception("Failed to mark webhook payment as failed")
        return jsonify({"status": "error", "message": str(exc)}), 400

    return jsonify({"status": "ok"}), 200


@main_bp.route("/billing/razorpay/order-status/<order_id>")
@login_required
@limiter.limit("60 per minute")
def razorpay_order_status(order_id: str):
    order_id = (order_id or "").strip()
    if not order_id:
        return jsonify({"state": "failed", "message": "Missing order id."}), 400

    payment_row = Payment.query.filter_by(
        user_id=current_user.id,
        razorpay_order_id=order_id,
    ).first()
    if not payment_row:
        return jsonify({"state": "failed", "message": "Order not found for this account."}), 404

    if (payment_row.status or "").strip().lower() == "success":
        return jsonify(
            {
                "state": "success",
                "message": "Your premium plan has been activated successfully.",
                "redirect_url": url_for(
                    "main.billing_status",
                    state="success",
                    plan=payment_row.plan_name,
                    order_id=order_id,
                    payment_id=payment_row.razorpay_payment_id or "",
                ),
            }
        )

    if PaymentGatewayService.is_demo_mode():
        if (payment_row.status or "").strip().lower() == "failed":
            return jsonify(
                {
                    "state": "failed",
                    "message": (payment_row.error_message or "").strip() or "Payment could not be completed.",
                    "redirect_url": url_for(
                        "main.billing_status",
                        state="failed",
                        message=(payment_row.error_message or "Payment could not be completed."),
                        order_id=order_id,
                    ),
                }
            )
        return jsonify(
            {
                "state": "pending",
                "message": "Payment is still processing. Please wait.",
            }
        )

    try:
        order = PaymentGatewayService.fetch_order(order_id)
        PaymentGatewayService.validate_order_vs_payment_row(
            order,
            payment_row,
            expected_user_id=current_user.id,
        )
        notes = order.get("notes") or {}
        order_user_id = int(notes.get("user_id", "0") or current_user.id)
        if order_user_id != current_user.id:
            raise ValueError("Order does not belong to current user.")

        plan_key = (notes.get("plan_key") or payment_row.plan_key or "").strip()
        custom_days = (notes.get("custom_days") or "").strip() or None
        if (
            not custom_days
            and plan_key == "pro_custom"
            and int(payment_row.duration_days or 0) > 0
        ):
            custom_days = str(int(payment_row.duration_days))
        if not plan_key:
            raise ValueError("Unable to resolve plan key for this order.")

        plan = SubscriptionService.resolve_plan_purchase(plan_key, custom_days=custom_days)
        if int(order.get("amount") or 0) != int(plan["price_paise"]):
            raise ValueError("Order amount mismatch.")
        if (order.get("currency") or "").strip().upper() != current_app.config["RAZORPAY_CURRENCY"].upper():
            raise ValueError("Order currency mismatch.")

        order_payments = PaymentGatewayService.fetch_order_payments(order_id)
        successful_payment = next(
            (item for item in order_payments if (item.get("status") or "").strip().lower() == "captured"),
            None,
        )
        if not successful_payment:
            successful_payment = next(
                (item for item in order_payments if (item.get("status") or "").strip().lower() == "authorized"),
                None,
            )

        if successful_payment:
            payment_id = (successful_payment.get("id") or "").strip()
            if not payment_id:
                raise ValueError("Missing payment id in successful payment record.")
            subscription = SubscriptionService.activate_after_gateway_payment(
                user=current_user,
                plan_key=plan["plan_key"],
                payment_id=payment_id,
                order_id=order_id,
                custom_days=plan.get("custom_days"),
                gateway_payload={
                    "order": order,
                    "status_check": successful_payment,
                    "callback_fields": {
                        "razorpay_payment_id": payment_id,
                        "razorpay_order_id": order_id,
                        "razorpay_signature": "",
                    },
                },
            )
            return jsonify(
                {
                    "state": "success",
                    "message": "Your premium plan has been activated successfully.",
                    "redirect_url": url_for(
                        "main.billing_status",
                        state="success",
                        plan=subscription.plan_name,
                        order_id=order_id,
                        payment_id=payment_id,
                    ),
                }
            )

        failed_payment = next(
            (item for item in order_payments if (item.get("status") or "").strip().lower() == "failed"),
            None,
        )
        if failed_payment:
            failed_payment_id = (failed_payment.get("id") or "").strip()
            failure_message = (
                (failed_payment.get("error_description") or "").strip()
                or (failed_payment.get("error_reason") or "").strip()
                or (failed_payment.get("error_code") or "").strip()
                or "Payment failed at gateway."
            )
            try:
                PaymentGatewayService.mark_payment_failed(
                    order_id,
                    payment_id=failed_payment_id,
                    error_message=failure_message,
                )
            except Exception:
                current_app.logger.exception("Failed to persist failed payment status. order_id=%s", order_id)
            return jsonify(
                {
                    "state": "failed",
                    "message": failure_message,
                    "redirect_url": url_for(
                        "main.billing_status",
                        state="failed",
                        message=failure_message,
                        order_id=order_id,
                        payment_id=failed_payment_id,
                    ),
                }
            )
    except Exception:
        current_app.logger.exception("Razorpay order status reconciliation failed. order_id=%s", order_id)

    if (payment_row.status or "").strip().lower() == "failed":
        return jsonify(
            {
                "state": "failed",
                "message": (payment_row.error_message or "").strip() or "Payment could not be completed.",
                "redirect_url": url_for(
                    "main.billing_status",
                    state="failed",
                    message=(payment_row.error_message or "Payment could not be completed."),
                    order_id=order_id,
                    payment_id=payment_row.razorpay_payment_id or "",
                ),
            }
        )

    return jsonify(
        {
            "state": "pending",
            "message": "Payment verification is in progress. Please wait a moment.",
        }
    )


@main_bp.route("/billing/pay/<token>")
def billing_pay_link(token: str):
    token_payload = _decode_payment_link_token(token)
    if not token_payload:
        return render_template(
            "billing_pay_link.html",
            is_valid=False,
            error_message="This payment link is invalid or expired. Please generate a new one.",
            can_pay=False,
        )

    order_id = token_payload["order_id"]
    user_id = token_payload["user_id"]
    payment_row = Payment.query.filter_by(
        razorpay_order_id=order_id,
        user_id=user_id,
    ).first()
    if not payment_row:
        return render_template(
            "billing_pay_link.html",
            is_valid=False,
            error_message="Payment order not found. Please generate a fresh payment link.",
            can_pay=False,
        )

    status_key = (payment_row.status or "pending").strip().lower()
    can_pay = PaymentGatewayService.is_live_mode() and status_key != "success"
    callback_url = _build_callback_url()
    payment_link_url = url_for("main.billing_pay_link", token=token, _external=True)
    status_url = url_for(
        "main.billing_status",
        order_id=order_id,
        state="pending" if status_key != "success" else "success",
    )
    return render_template(
        "billing_pay_link.html",
        is_valid=True,
        can_pay=can_pay,
        payment_mode="live" if PaymentGatewayService.is_live_mode() else "demo",
        order_id=order_id,
        plan_name=(payment_row.plan_name or "Premium Plan"),
        amount_paise=int(payment_row.amount_paise or 0),
        currency=((payment_row.currency or "INR").strip().upper() or "INR"),
        status_key=status_key,
        status_message=(
            "Payment already completed for this plan."
            if status_key == "success"
            else "Use the secure payment button below to complete this order."
        ),
        callback_url=callback_url,
        key_id=(current_app.config.get("RAZORPAY_KEY_ID") or "").strip(),
        app_name=current_app.config.get("APP_NAME") or "PDFMaster Ultra Suite",
        payment_link_url=payment_link_url,
        status_url=status_url,
    )


@main_bp.route("/billing/status")
def billing_status():
    state = (request.args.get("state", "failed") or "failed").strip().lower()
    if state not in {"success", "failed", "pending"}:
        state = "failed"
    message = (request.args.get("message", "") or "").strip()
    plan = (request.args.get("plan", "") or "").strip()
    order_id = (request.args.get("order_id", "") or "").strip()
    payment_id = (request.args.get("payment_id", "") or "").strip()

    if order_id:
        payment_query = Payment.query.filter_by(razorpay_order_id=order_id)
        if current_user.is_authenticated:
            payment_query = payment_query.filter_by(user_id=current_user.id)
        payment_row = payment_query.first()
        if payment_row:
            persisted_status = (payment_row.status or "pending").strip().lower()
            if persisted_status == "success":
                state = "success"
                if not plan:
                    plan = (payment_row.plan_name or "").strip()
                if not payment_id:
                    payment_id = (payment_row.razorpay_payment_id or "").strip()
                if not message:
                    message = "Your premium plan has been activated successfully."
            elif persisted_status == "pending" and state == "failed":
                state = "pending"
                if not message:
                    message = "Payment verification is in progress. Please wait a moment."
            elif persisted_status == "failed" and not message:
                message = (payment_row.error_message or "").strip() or "Payment could not be completed."

    if not message:
        if state == "success":
            message = "Your premium plan has been activated successfully."
        elif state == "pending":
            message = "Payment verification is in progress. Please wait a moment."
        else:
            message = "Payment could not be completed."
    return render_template(
        "billing_status.html",
        state=state,
        message=message,
        plan=plan,
        order_id=order_id,
        payment_id=payment_id,
        status_poll_url=(
            url_for("main.razorpay_order_status", order_id=order_id)
            if order_id and state == "pending"
            else ""
        ),
    )


@main_bp.route("/dashboard/insights/export")
@login_required
def export_insights():
    insights = AnalyticsService.user_dashboard_summary(current_user.id)
    recent_jobs = (
        Job.query.filter_by(user_id=current_user.id)
        .order_by(Job.created_at.desc())
        .limit(50)
        .all()
    )

    buffer = StringIO()
    writer = csv.writer(buffer)
    writer.writerow(["Metric", "Value"])
    writer.writerow(["Window (days)", insights["window_days"]])
    writer.writerow(["Total jobs", insights["total_jobs"]])
    writer.writerow(["Completed jobs", insights["completed_jobs"]])
    writer.writerow(["Failed jobs", insights["failed_jobs"]])
    writer.writerow(["Success rate (%)", insights["success_rate"]])
    writer.writerow(["Spend in window (paise)", insights["window_spend_paise"]])
    writer.writerow(["Top-up in window (paise)", insights["window_topups_paise"]])
    writer.writerow([])
    writer.writerow(["Recent jobs"])
    writer.writerow(["Job ID", "Tool", "Status", "Price (paise)", "Created at"])
    for job in recent_jobs:
        writer.writerow(
            [
                job.id,
                job.tool_key,
                job.status,
                job.price,
                job.created_at.isoformat() if job.created_at else "",
            ]
        )

    filename = f"pdfmaster_insights_user_{current_user.id}.csv"
    return Response(
        buffer.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


@main_bp.route("/favorites/<tool_key>/toggle", methods=["POST"])
@login_required
def toggle_favorite(tool_key: str):
    is_favorite = CatalogService.toggle_favorite(current_user.id, tool_key)
    flash(
        "Tool added to favorites." if is_favorite else "Tool removed from favorites.",
        "success",
    )
    return redirect(request.referrer or url_for("main.dashboard"))


@main_bp.route("/files/<int:file_id>/download")
@login_required
def download_file(file_id: int):
    file_record = ManagedFile.query.filter_by(id=file_id, user_id=current_user.id, is_deleted=False).first()
    if not file_record:
        abort(404)
    absolute_path = StorageService.absolute_path(file_record)
    if not absolute_path.exists():
        file_record.is_deleted = True
        db.session.commit()
        flash("Requested file is no longer available.", "warning")
        return redirect(url_for("main.dashboard"))
    PrivacyService.log_file_access(
        owner_user_id=current_user.id,
        actor_user_id=current_user.id,
        file_id=file_record.id,
        action="download",
        ip_address=request.headers.get("X-Forwarded-For", request.remote_addr or ""),
        user_agent=request.headers.get("User-Agent", ""),
        details={"source": "dashboard"},
    )
    return send_file(
        absolute_path,
        as_attachment=True,
        download_name=file_record.original_name,
        mimetype=file_record.mime_type,
    )


@main_bp.route("/files/<int:file_id>/preview")
@login_required
def preview_file(file_id: int):
    file_record = ManagedFile.query.filter_by(id=file_id, user_id=current_user.id, is_deleted=False).first()
    if not file_record:
        abort(404)
    absolute_path = StorageService.absolute_path(file_record)
    if not absolute_path.exists():
        file_record.is_deleted = True
        db.session.commit()
        flash("Requested file preview is no longer available.", "warning")
        return redirect(url_for("main.dashboard"))
    PrivacyService.log_file_access(
        owner_user_id=current_user.id,
        actor_user_id=current_user.id,
        file_id=file_record.id,
        action="preview",
        ip_address=request.headers.get("X-Forwarded-For", request.remote_addr or ""),
        user_agent=request.headers.get("User-Agent", ""),
        details={"source": "dashboard"},
    )
    return send_file(
        absolute_path,
        as_attachment=False,
        download_name=file_record.original_name,
        mimetype=file_record.mime_type,
    )


@main_bp.route("/files/downloads/recent.zip")
@login_required
def download_recent_outputs_zip():
    recent_outputs = (
        ManagedFile.query.filter_by(user_id=current_user.id, storage_kind="output", is_deleted=False)
        .order_by(ManagedFile.created_at.desc())
        .limit(50)
        .all()
    )
    if not recent_outputs:
        flash("No processed files available for ZIP download.", "warning")
        return redirect(url_for("main.dashboard"))

    bundle_root = Path(current_app.config["OUTPUT_ROOT"]) / str(current_user.id) / "_bundles"
    bundle_root.mkdir(parents=True, exist_ok=True)
    bundle_path = bundle_root / f"recent_outputs_{datetime.now().strftime('%Y%m%d_%H%M%S')}.zip"

    archived_count = 0
    used_names: dict[str, int] = {}
    with ZipFile(bundle_path, "w", compression=ZIP_DEFLATED) as archive:
        for file_record in recent_outputs:
            source_path = StorageService.absolute_path(file_record)
            if not source_path.exists():
                continue
            original_name = (file_record.original_name or source_path.name).strip() or source_path.name
            seen = used_names.get(original_name, 0)
            used_names[original_name] = seen + 1
            if seen:
                stem = Path(original_name).stem or "file"
                suffix = Path(original_name).suffix
                archive_name = f"{stem}_{seen + 1}{suffix}"
            else:
                archive_name = original_name
            archive.write(source_path, arcname=archive_name)
            archived_count += 1

    if archived_count == 0:
        if bundle_path.exists():
            bundle_path.unlink()
        flash("No valid files found to bundle.", "warning")
        return redirect(url_for("main.dashboard"))

    return send_file(
        bundle_path,
        as_attachment=True,
        download_name=bundle_path.name,
        mimetype="application/zip",
    )


@main_bp.route("/jobs/<int:job_id>")
@login_required
def job_status(job_id: int):
    job = Job.query.filter_by(id=job_id, user_id=current_user.id).first_or_404()
    return render_template("partials/job_status.html", job=job)


@main_bp.route("/cloud/upload", methods=["POST"])
@login_required
def cloud_upload():
    upload = request.files.get("cloud_file")
    try:
        StorageService.save_uploaded_file(upload, current_user.id, kind="cloud", label="Cloud storage item")
    except Exception as exc:
        flash(str(exc), "danger")
    else:
        flash("File added to your personal cloud storage.", "success")
    return redirect(url_for("tools.tool_detail", tool_key="cloud_storage"))


@main_bp.route("/cloud/<int:file_id>/rename", methods=["POST"])
@login_required
def rename_cloud_file(file_id: int):
    try:
        StorageService.rename_file(file_id, current_user.id, request.form.get("new_name", ""))
    except Exception as exc:
        flash(str(exc), "danger")
    else:
        flash("Cloud file renamed.", "success")
    return redirect(url_for("tools.tool_detail", tool_key="cloud_storage"))


@main_bp.route("/cloud/<int:file_id>/delete", methods=["POST"])
@login_required
def delete_cloud_file(file_id: int):
    try:
        StorageService.delete_file(file_id, current_user.id)
    except Exception as exc:
        flash(str(exc), "danger")
    else:
        flash("Cloud file deleted.", "success")
    return redirect(url_for("tools.tool_detail", tool_key="cloud_storage"))


@main_bp.route("/share/<token>", methods=["GET", "POST"])
def access_share_link(token: str):
    password = request.form.get("password", "") if request.method == "POST" else ""
    error = ""
    share_link = None
    try:
        if request.method == "POST":
            share_link = ShareService.validate_link(token, password=password, check_password=True)
        else:
            share_link = ShareService.get_link_for_access(token)
            PrivacyService.log_share_access(
                share_link_id=share_link.id,
                event="view",
                status="ok",
                ip_address=request.headers.get("X-Forwarded-For", request.remote_addr or ""),
                user_agent=request.headers.get("User-Agent", ""),
                details={"token": token},
            )
        file_record = share_link.file
        if request.method == "POST" or not share_link.password_hash:
            ShareService.mark_download(share_link)
            PrivacyService.log_share_access(
                share_link_id=share_link.id,
                event="download",
                status="ok",
                ip_address=request.headers.get("X-Forwarded-For", request.remote_addr or ""),
                user_agent=request.headers.get("User-Agent", ""),
                details={"token": token},
            )
            absolute_path = StorageService.absolute_path(file_record)
            if not absolute_path.exists():
                raise FileNotFoundError("Shared file is no longer available.")
            return send_file(
                absolute_path,
                as_attachment=True,
                download_name=file_record.original_name,
                mimetype=file_record.mime_type,
            )
    except HTTPException:
        error = "Share link is invalid or no longer available."
    except Exception as exc:
        current_app.logger.exception("Share access failed for token=%s", token)
        error = str(exc) if isinstance(exc, ValueError) else "Unable to open this share link."
        if share_link:
            try:
                PrivacyService.log_share_access(
                    share_link_id=share_link.id,
                    event="error",
                    status="failed",
                    ip_address=request.headers.get("X-Forwarded-For", request.remote_addr or ""),
                    user_agent=request.headers.get("User-Agent", ""),
                    details={"token": token, "error": error},
                )
            except Exception:
                current_app.logger.exception("Failed to write share access log")
    return render_template("share_access.html", token=token, error=error, share_link=share_link)

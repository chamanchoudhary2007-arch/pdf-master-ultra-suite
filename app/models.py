from __future__ import annotations

import secrets
import string
from datetime import datetime, timezone

from flask_login import UserMixin
from sqlalchemy import UniqueConstraint
from werkzeug.security import check_password_hash, generate_password_hash

from app.extensions import db


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def generate_referral_code() -> str:
    alphabet = string.ascii_uppercase + string.digits
    return "PDFM-" + "".join(secrets.choice(alphabet) for _ in range(6))


class TimestampMixin:
    created_at = db.Column(db.DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at = db.Column(
        db.DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False
    )


class User(UserMixin, TimestampMixin, db.Model):
    __tablename__ = "users"

    id = db.Column(db.Integer, primary_key=True)
    full_name = db.Column(db.String(120), nullable=False)
    email = db.Column(db.String(255), unique=True, index=True, nullable=False)
    google_id = db.Column(db.String(255), unique=True, index=True)
    profile_picture = db.Column(db.String(500), default="", nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    password_reset_key_hash = db.Column(db.String(255), default="", nullable=False)
    password_reset_key_icon = db.Column(db.String(40), default="bi-key-fill", nullable=False)
    role = db.Column(db.String(32), default="user", nullable=False)
    is_active = db.Column(db.Boolean, default=True, nullable=False)
    is_verified = db.Column(db.Boolean, default=False, nullable=False)
    preferred_mode = db.Column(db.String(32), default="general", nullable=False)
    theme = db.Column(db.String(16), default="light", nullable=False)
    wallet_balance_paise = db.Column(db.Integer, default=0, nullable=False)
    referral_code = db.Column(
        db.String(16),
        unique=True,
        index=True,
        nullable=False,
        default=generate_referral_code,
    )
    referred_by = db.Column(db.String(16), nullable=True, index=True)
    total_referrals = db.Column(db.Integer, default=0, nullable=False)
    last_login_at = db.Column(db.DateTime(timezone=True))

    jobs = db.relationship("Job", back_populates="user", lazy="dynamic")
    files = db.relationship("ManagedFile", back_populates="user", lazy="dynamic")
    transactions = db.relationship(
        "WalletTransaction", back_populates="user", lazy="dynamic"
    )
    payments = db.relationship("Payment", back_populates="user", lazy="dynamic")
    favorites = db.relationship("FavoriteTool", back_populates="user", lazy="dynamic")
    subscriptions = db.relationship(
        "UserSubscription", back_populates="user", lazy="dynamic"
    )
    subscription_events = db.relationship(
        "SubscriptionEvent",
        foreign_keys="SubscriptionEvent.user_id",
        back_populates="user",
        lazy="dynamic",
    )
    acted_subscription_events = db.relationship(
        "SubscriptionEvent",
        foreign_keys="SubscriptionEvent.actor_id",
        back_populates="actor",
        lazy="dynamic",
    )

    def set_password(self, password: str) -> None:
        self.password_hash = generate_password_hash(password)

    def check_password(self, password: str) -> bool:
        return check_password_hash(self.password_hash, password)

    @property
    def is_admin(self) -> bool:
        return self.role == "admin"

    @property
    def name(self) -> str:
        return self.full_name

    @name.setter
    def name(self, value: str) -> None:
        self.full_name = (value or "").strip()


class ToolCatalog(TimestampMixin, db.Model):
    __tablename__ = "tool_catalog"

    id = db.Column(db.Integer, primary_key=True)
    tool_key = db.Column(db.String(80), unique=True, index=True, nullable=False)
    name = db.Column(db.String(140), nullable=False)
    category = db.Column(db.String(60), index=True, nullable=False)
    description = db.Column(db.Text, nullable=False)
    price_paise = db.Column(db.Integer, nullable=False, default=500)
    is_enabled = db.Column(db.Boolean, default=True, nullable=False)
    is_subscription_only = db.Column(db.Boolean, default=False, nullable=False)
    is_payperuse_allowed = db.Column(db.Boolean, default=True, nullable=False)
    icon_name = db.Column(db.String(40), default="bi-tools", nullable=False)
    route_name = db.Column(db.String(120), default="tools.tool_detail", nullable=False)
    template_name = db.Column(
        db.String(120), default="tool_pages/tool_detail.html", nullable=False
    )
    keywords = db.Column(db.String(255), default="", nullable=False)
    is_beta = db.Column(db.Boolean, default=False, nullable=False)


class ManagedFile(TimestampMixin, db.Model):
    __tablename__ = "managed_files"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)
    storage_kind = db.Column(db.String(32), nullable=False, index=True)
    original_name = db.Column(db.String(255), nullable=False)
    stored_name = db.Column(db.String(255), unique=True, nullable=False)
    relative_path = db.Column(db.String(500), unique=True, nullable=False)
    mime_type = db.Column(db.String(120), nullable=False)
    size_bytes = db.Column(db.BigInteger, nullable=False)
    label = db.Column(db.String(120), default="", nullable=False)
    file_hash = db.Column(db.String(128), default="", nullable=False)
    is_deleted = db.Column(db.Boolean, default=False, nullable=False)

    user = db.relationship("User", back_populates="files")
    jobs = db.relationship("Job", back_populates="output_file", lazy="dynamic")
    share_links = db.relationship("ShareLink", back_populates="file", lazy="dynamic")


class Job(TimestampMixin, db.Model):
    __tablename__ = "jobs"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)
    tool_key = db.Column(db.String(80), index=True, nullable=False)
    status = db.Column(db.String(20), default="queued", nullable=False, index=True)
    price = db.Column(db.Integer, default=0, nullable=False)
    progress = db.Column(db.Integer, default=0, nullable=False)
    input_filename = db.Column(db.String(255), default="", nullable=False)
    output_filename = db.Column(db.String(255), default="", nullable=False)
    error_message = db.Column(db.Text, default="", nullable=False)
    options_json = db.Column(db.JSON, default=dict, nullable=False)
    result_json = db.Column(db.JSON, default=dict, nullable=False)
    completed_at = db.Column(db.DateTime(timezone=True))
    output_file_id = db.Column(
        db.Integer, db.ForeignKey("managed_files.id"), nullable=True, index=True
    )

    user = db.relationship("User", back_populates="jobs")
    output_file = db.relationship("ManagedFile", back_populates="jobs")


class WalletTransaction(TimestampMixin, db.Model):
    __tablename__ = "wallet_transactions"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)
    transaction_type = db.Column(db.String(32), nullable=False, index=True)
    amount_paise = db.Column(db.Integer, nullable=False)
    balance_after_paise = db.Column(db.Integer, nullable=False)
    reference = db.Column(db.String(120), default="", nullable=False)
    note = db.Column(db.String(255), default="", nullable=False)

    user = db.relationship("User", back_populates="transactions")


class Payment(TimestampMixin, db.Model):
    __tablename__ = "payments"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)
    gateway = db.Column(db.String(32), nullable=False, default="razorpay")
    status = db.Column(db.String(20), nullable=False, default="pending", index=True)
    amount_paise = db.Column(db.Integer, nullable=False, default=0)
    currency = db.Column(db.String(8), nullable=False, default="INR")
    plan_key = db.Column(db.String(40), nullable=False, default="", index=True)
    plan_name = db.Column(db.String(80), nullable=False, default="")
    duration_days = db.Column(db.Integer, nullable=False, default=0)
    razorpay_order_id = db.Column(db.String(120), unique=True, nullable=False, index=True)
    razorpay_payment_id = db.Column(db.String(120), unique=True, index=True)
    razorpay_signature = db.Column(db.String(255), nullable=False, default="")
    reference = db.Column(db.String(120), nullable=False, default="", index=True)
    paid_at = db.Column(db.DateTime(timezone=True))
    failed_at = db.Column(db.DateTime(timezone=True))
    error_message = db.Column(db.Text, nullable=False, default="")
    notes_json = db.Column(db.JSON, nullable=False, default=dict)

    user = db.relationship("User", back_populates="payments")


class FavoriteTool(TimestampMixin, db.Model):
    __tablename__ = "favorite_tools"
    __table_args__ = (UniqueConstraint("user_id", "tool_key", name="uq_user_tool_fav"),)

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)
    tool_key = db.Column(db.String(80), nullable=False, index=True)

    user = db.relationship("User", back_populates="favorites")


class ShareLink(TimestampMixin, db.Model):
    __tablename__ = "share_links"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)
    file_id = db.Column(
        db.Integer, db.ForeignKey("managed_files.id"), nullable=False, index=True
    )
    token = db.Column(db.String(120), unique=True, nullable=False, index=True)
    expires_at = db.Column(db.DateTime(timezone=True), nullable=False)
    password_hash = db.Column(db.String(255), default="", nullable=False)
    max_downloads = db.Column(db.Integer, default=10, nullable=False)
    download_count = db.Column(db.Integer, default=0, nullable=False)
    is_active = db.Column(db.Boolean, default=True, nullable=False)

    file = db.relationship("ManagedFile", back_populates="share_links")

    def set_password(self, password: str) -> None:
        self.password_hash = generate_password_hash(password) if password else ""

    def check_password(self, password: str) -> bool:
        if not self.password_hash:
            return True
        return check_password_hash(self.password_hash, password)


class UserSubscription(TimestampMixin, db.Model):
    __tablename__ = "user_subscriptions"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)
    plan_key = db.Column(db.String(40), nullable=False, index=True)
    plan_name = db.Column(db.String(80), nullable=False)
    status = db.Column(db.String(20), nullable=False, default="active", index=True)
    price_paise = db.Column(db.Integer, nullable=False, default=0)
    started_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow)
    expires_at = db.Column(db.DateTime(timezone=True), nullable=False)
    cancelled_at = db.Column(db.DateTime(timezone=True))
    metadata_json = db.Column(db.JSON, default=dict, nullable=False)

    user = db.relationship("User", back_populates="subscriptions")


class SubscriptionEvent(TimestampMixin, db.Model):
    __tablename__ = "subscription_events"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)
    event_type = db.Column(db.String(40), nullable=False, index=True)
    source = db.Column(db.String(40), nullable=False, default="system", index=True)
    delta_days = db.Column(db.Integer, nullable=False, default=0)
    previous_expiry = db.Column(db.DateTime(timezone=True))
    new_expiry = db.Column(db.DateTime(timezone=True))
    payment_ref = db.Column(db.String(120), nullable=False, default="", index=True)
    actor_id = db.Column(db.Integer, db.ForeignKey("users.id"), index=True)
    notes = db.Column(db.String(255), nullable=False, default="")
    metadata_json = db.Column(db.JSON, nullable=False, default=dict)

    user = db.relationship("User", foreign_keys=[user_id], back_populates="subscription_events")
    actor = db.relationship("User", foreign_keys=[actor_id], back_populates="acted_subscription_events")


class ActivityLog(TimestampMixin, db.Model):
    __tablename__ = "activity_logs"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), index=True)
    action = db.Column(db.String(80), nullable=False, index=True)
    target_type = db.Column(db.String(80), default="", nullable=False)
    target_id = db.Column(db.String(80), default="", nullable=False)
    ip_address = db.Column(db.String(80), default="", nullable=False)
    user_agent = db.Column(db.String(255), default="", nullable=False)
    details_json = db.Column(db.JSON, default=dict, nullable=False)


class DocumentChatSession(TimestampMixin, db.Model):
    __tablename__ = "document_chat_sessions"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)
    source_file_id = db.Column(db.Integer, db.ForeignKey("managed_files.id"), index=True)
    title = db.Column(db.String(255), nullable=False, default="Untitled Document")
    status = db.Column(db.String(20), nullable=False, default="ready", index=True)
    extraction_strategy = db.Column(db.String(40), nullable=False, default="text_then_ocr")
    metadata_json = db.Column(db.JSON, nullable=False, default=dict)
    last_asked_at = db.Column(db.DateTime(timezone=True))

    user = db.relationship("User")
    source_file = db.relationship("ManagedFile")
    chunks = db.relationship(
        "DocumentChatChunk",
        back_populates="session",
        lazy="dynamic",
        cascade="all, delete-orphan",
    )
    messages = db.relationship(
        "DocumentChatMessage",
        back_populates="session",
        lazy="dynamic",
        cascade="all, delete-orphan",
    )


class DocumentChatChunk(TimestampMixin, db.Model):
    __tablename__ = "document_chat_chunks"

    id = db.Column(db.Integer, primary_key=True)
    session_id = db.Column(
        db.Integer, db.ForeignKey("document_chat_sessions.id"), nullable=False, index=True
    )
    page_number = db.Column(db.Integer, nullable=False, default=1, index=True)
    chunk_index = db.Column(db.Integer, nullable=False, default=0)
    token_count = db.Column(db.Integer, nullable=False, default=0)
    content = db.Column(db.Text, nullable=False, default="")
    keywords_json = db.Column(db.JSON, nullable=False, default=list)

    session = db.relationship("DocumentChatSession", back_populates="chunks")


class DocumentChatMessage(TimestampMixin, db.Model):
    __tablename__ = "document_chat_messages"

    id = db.Column(db.Integer, primary_key=True)
    session_id = db.Column(
        db.Integer, db.ForeignKey("document_chat_sessions.id"), nullable=False, index=True
    )
    role = db.Column(db.String(20), nullable=False, default="user", index=True)
    question = db.Column(db.Text, nullable=False, default="")
    answer = db.Column(db.Text, nullable=False, default="")
    sources_json = db.Column(db.JSON, nullable=False, default=list)
    latency_ms = db.Column(db.Integer, nullable=False, default=0)

    session = db.relationship("DocumentChatSession", back_populates="messages")


class BulkBatch(TimestampMixin, db.Model):
    __tablename__ = "bulk_batches"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)
    name = db.Column(db.String(160), nullable=False, default="Bulk batch")
    tool_key = db.Column(db.String(80), nullable=False, index=True)
    status = db.Column(db.String(20), nullable=False, default="pending", index=True)
    total_files = db.Column(db.Integer, nullable=False, default=0)
    processed_files = db.Column(db.Integer, nullable=False, default=0)
    failed_files = db.Column(db.Integer, nullable=False, default=0)
    options_json = db.Column(db.JSON, nullable=False, default=dict)
    result_json = db.Column(db.JSON, nullable=False, default=dict)
    started_at = db.Column(db.DateTime(timezone=True))
    finished_at = db.Column(db.DateTime(timezone=True))
    output_archive_file_id = db.Column(db.Integer, db.ForeignKey("managed_files.id"), index=True)

    user = db.relationship("User")
    output_archive_file = db.relationship("ManagedFile")
    items = db.relationship(
        "BulkBatchItem",
        back_populates="batch",
        lazy="dynamic",
        cascade="all, delete-orphan",
    )


class BulkBatchItem(TimestampMixin, db.Model):
    __tablename__ = "bulk_batch_items"

    id = db.Column(db.Integer, primary_key=True)
    batch_id = db.Column(db.Integer, db.ForeignKey("bulk_batches.id"), nullable=False, index=True)
    input_file_id = db.Column(db.Integer, db.ForeignKey("managed_files.id"), index=True)
    output_file_id = db.Column(db.Integer, db.ForeignKey("managed_files.id"), index=True)
    status = db.Column(db.String(20), nullable=False, default="pending", index=True)
    log_message = db.Column(db.Text, nullable=False, default="")
    error_message = db.Column(db.Text, nullable=False, default="")
    started_at = db.Column(db.DateTime(timezone=True))
    finished_at = db.Column(db.DateTime(timezone=True))
    sequence_index = db.Column(db.Integer, nullable=False, default=0)

    batch = db.relationship("BulkBatch", back_populates="items")
    input_file = db.relationship("ManagedFile", foreign_keys=[input_file_id])
    output_file = db.relationship("ManagedFile", foreign_keys=[output_file_id])


class SignatureRequest(TimestampMixin, db.Model):
    __tablename__ = "signature_requests"

    id = db.Column(db.Integer, primary_key=True)
    requester_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)
    file_id = db.Column(db.Integer, db.ForeignKey("managed_files.id"), nullable=False, index=True)
    signed_file_id = db.Column(db.Integer, db.ForeignKey("managed_files.id"), index=True)
    title = db.Column(db.String(255), nullable=False, default="Signature Request")
    message = db.Column(db.Text, nullable=False, default="")
    status = db.Column(db.String(20), nullable=False, default="draft", index=True)
    access_token = db.Column(
        db.String(120),
        nullable=False,
        unique=True,
        index=True,
        default=lambda: secrets.token_urlsafe(32),
    )
    signer_order_enforced = db.Column(db.Boolean, nullable=False, default=True)
    current_order = db.Column(db.Integer, nullable=False, default=1)
    expires_at = db.Column(db.DateTime(timezone=True), nullable=True)
    sent_at = db.Column(db.DateTime(timezone=True))
    completed_at = db.Column(db.DateTime(timezone=True))
    metadata_json = db.Column(db.JSON, nullable=False, default=dict)

    requester = db.relationship("User")
    file = db.relationship("ManagedFile", foreign_keys=[file_id])
    signed_file = db.relationship("ManagedFile", foreign_keys=[signed_file_id])
    signers = db.relationship(
        "SignatureSigner",
        back_populates="request",
        lazy="dynamic",
        cascade="all, delete-orphan",
    )
    fields = db.relationship(
        "SignatureField",
        back_populates="request",
        lazy="dynamic",
        cascade="all, delete-orphan",
    )
    events = db.relationship(
        "SignatureEvent",
        back_populates="request",
        lazy="dynamic",
        cascade="all, delete-orphan",
    )


class SignatureSigner(TimestampMixin, db.Model):
    __tablename__ = "signature_signers"
    __table_args__ = (
        UniqueConstraint("request_id", "email", name="uq_signature_signer_request_email"),
    )

    id = db.Column(db.Integer, primary_key=True)
    request_id = db.Column(
        db.Integer, db.ForeignKey("signature_requests.id"), nullable=False, index=True
    )
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), index=True)
    name = db.Column(db.String(140), nullable=False, default="")
    email = db.Column(db.String(255), nullable=False, index=True)
    signer_order = db.Column(db.Integer, nullable=False, default=1)
    status = db.Column(db.String(20), nullable=False, default="sent", index=True)
    verification_code = db.Column(db.String(12), nullable=False, default="")
    viewed_at = db.Column(db.DateTime(timezone=True))
    signed_at = db.Column(db.DateTime(timezone=True))
    reminder_count = db.Column(db.Integer, nullable=False, default=0)
    metadata_json = db.Column(db.JSON, nullable=False, default=dict)

    request = db.relationship("SignatureRequest", back_populates="signers")
    user = db.relationship("User")


class SignatureField(TimestampMixin, db.Model):
    __tablename__ = "signature_fields"

    id = db.Column(db.Integer, primary_key=True)
    request_id = db.Column(
        db.Integer, db.ForeignKey("signature_requests.id"), nullable=False, index=True
    )
    signer_id = db.Column(db.Integer, db.ForeignKey("signature_signers.id"), index=True)
    page = db.Column(db.Integer, nullable=False, default=1)
    field_type = db.Column(db.String(24), nullable=False, default="signature", index=True)
    label = db.Column(db.String(120), nullable=False, default="")
    x = db.Column(db.Float, nullable=False, default=72.0)
    y = db.Column(db.Float, nullable=False, default=72.0)
    width = db.Column(db.Float, nullable=False, default=140.0)
    height = db.Column(db.Float, nullable=False, default=42.0)
    required = db.Column(db.Boolean, nullable=False, default=True)
    value = db.Column(db.Text, nullable=False, default="")

    request = db.relationship("SignatureRequest", back_populates="fields")
    signer = db.relationship("SignatureSigner")


class SignatureEvent(TimestampMixin, db.Model):
    __tablename__ = "signature_events"

    id = db.Column(db.Integer, primary_key=True)
    request_id = db.Column(
        db.Integer, db.ForeignKey("signature_requests.id"), nullable=False, index=True
    )
    signer_id = db.Column(db.Integer, db.ForeignKey("signature_signers.id"), index=True)
    event_type = db.Column(db.String(32), nullable=False, index=True)
    ip_address = db.Column(db.String(80), nullable=False, default="")
    user_agent = db.Column(db.String(255), nullable=False, default="")
    details_json = db.Column(db.JSON, nullable=False, default=dict)

    request = db.relationship("SignatureRequest", back_populates="events")
    signer = db.relationship("SignatureSigner")


class FormTemplate(TimestampMixin, db.Model):
    __tablename__ = "form_templates"

    id = db.Column(db.Integer, primary_key=True)
    owner_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)
    source_file_id = db.Column(db.Integer, db.ForeignKey("managed_files.id"), nullable=False, index=True)
    name = db.Column(db.String(180), nullable=False)
    description = db.Column(db.Text, nullable=False, default="")
    share_token = db.Column(
        db.String(120),
        nullable=False,
        unique=True,
        index=True,
        default=lambda: secrets.token_urlsafe(24),
    )
    is_active = db.Column(db.Boolean, nullable=False, default=True)
    metadata_json = db.Column(db.JSON, nullable=False, default=dict)

    owner = db.relationship("User")
    source_file = db.relationship("ManagedFile")
    fields = db.relationship(
        "FormTemplateField",
        back_populates="template",
        lazy="dynamic",
        cascade="all, delete-orphan",
    )
    submissions = db.relationship(
        "FormSubmission",
        back_populates="template",
        lazy="dynamic",
        cascade="all, delete-orphan",
    )


class FormTemplateField(TimestampMixin, db.Model):
    __tablename__ = "form_template_fields"

    id = db.Column(db.Integer, primary_key=True)
    template_id = db.Column(db.Integer, db.ForeignKey("form_templates.id"), nullable=False, index=True)
    page = db.Column(db.Integer, nullable=False, default=1)
    field_type = db.Column(db.String(24), nullable=False, default="text", index=True)
    name = db.Column(db.String(120), nullable=False, default="")
    label = db.Column(db.String(120), nullable=False, default="")
    placeholder = db.Column(db.String(255), nullable=False, default="")
    options_json = db.Column(db.JSON, nullable=False, default=list)
    x = db.Column(db.Float, nullable=False, default=72.0)
    y = db.Column(db.Float, nullable=False, default=72.0)
    width = db.Column(db.Float, nullable=False, default=180.0)
    height = db.Column(db.Float, nullable=False, default=24.0)
    required = db.Column(db.Boolean, nullable=False, default=False)
    default_value = db.Column(db.String(255), nullable=False, default="")

    template = db.relationship("FormTemplate", back_populates="fields")


class FormSubmission(TimestampMixin, db.Model):
    __tablename__ = "form_submissions"

    id = db.Column(db.Integer, primary_key=True)
    template_id = db.Column(db.Integer, db.ForeignKey("form_templates.id"), nullable=False, index=True)
    submitted_by_user_id = db.Column(db.Integer, db.ForeignKey("users.id"), index=True)
    output_file_id = db.Column(db.Integer, db.ForeignKey("managed_files.id"), index=True)
    status = db.Column(db.String(20), nullable=False, default="submitted", index=True)
    values_json = db.Column(db.JSON, nullable=False, default=dict)
    submit_token = db.Column(
        db.String(120),
        nullable=False,
        unique=True,
        index=True,
        default=lambda: secrets.token_urlsafe(24),
    )
    submitted_at = db.Column(db.DateTime(timezone=True), default=utcnow, nullable=False)

    template = db.relationship("FormTemplate", back_populates="submissions")
    submitted_by_user = db.relationship("User")
    output_file = db.relationship("ManagedFile")


class CloudIntegrationConnection(TimestampMixin, db.Model):
    __tablename__ = "cloud_integration_connections"
    __table_args__ = (
        UniqueConstraint("user_id", "provider", name="uq_cloud_connection_user_provider"),
    )

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)
    provider = db.Column(db.String(24), nullable=False, index=True)
    account_email = db.Column(db.String(255), nullable=False, default="")
    access_token = db.Column(db.Text, nullable=False, default="")
    refresh_token = db.Column(db.Text, nullable=False, default="")
    token_expires_at = db.Column(db.DateTime(timezone=True))
    status = db.Column(db.String(20), nullable=False, default="disconnected", index=True)
    metadata_json = db.Column(db.JSON, nullable=False, default=dict)
    last_sync_at = db.Column(db.DateTime(timezone=True))

    user = db.relationship("User")


class CloudTransferLog(TimestampMixin, db.Model):
    __tablename__ = "cloud_transfer_logs"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)
    provider = db.Column(db.String(24), nullable=False, index=True)
    direction = db.Column(db.String(12), nullable=False, index=True)
    source_file_id = db.Column(db.Integer, db.ForeignKey("managed_files.id"), index=True)
    target_file_id = db.Column(db.Integer, db.ForeignKey("managed_files.id"), index=True)
    status = db.Column(db.String(20), nullable=False, default="completed", index=True)
    details_json = db.Column(db.JSON, nullable=False, default=dict)

    user = db.relationship("User")
    source_file = db.relationship("ManagedFile", foreign_keys=[source_file_id])
    target_file = db.relationship("ManagedFile", foreign_keys=[target_file_id])


class DocumentVersionGroup(TimestampMixin, db.Model):
    __tablename__ = "document_version_groups"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)
    name = db.Column(db.String(180), nullable=False)
    description = db.Column(db.Text, nullable=False, default="")
    metadata_json = db.Column(db.JSON, nullable=False, default=dict)

    user = db.relationship("User")
    versions = db.relationship(
        "DocumentVersion",
        back_populates="group",
        lazy="dynamic",
        cascade="all, delete-orphan",
    )


class DocumentVersion(TimestampMixin, db.Model):
    __tablename__ = "document_versions"

    id = db.Column(db.Integer, primary_key=True)
    group_id = db.Column(
        db.Integer, db.ForeignKey("document_version_groups.id"), nullable=False, index=True
    )
    file_id = db.Column(db.Integer, db.ForeignKey("managed_files.id"), nullable=False, index=True)
    version_label = db.Column(db.String(120), nullable=False, default="")
    notes = db.Column(db.Text, nullable=False, default="")

    group = db.relationship("DocumentVersionGroup", back_populates="versions")
    file = db.relationship("ManagedFile")


class DocumentComparison(TimestampMixin, db.Model):
    __tablename__ = "document_comparisons"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)
    version_a_id = db.Column(db.Integer, db.ForeignKey("document_versions.id"), nullable=False, index=True)
    version_b_id = db.Column(db.Integer, db.ForeignKey("document_versions.id"), nullable=False, index=True)
    report_file_id = db.Column(db.Integer, db.ForeignKey("managed_files.id"), index=True)
    summary = db.Column(db.Text, nullable=False, default="")
    diff_json = db.Column(db.JSON, nullable=False, default=dict)

    user = db.relationship("User")
    version_a = db.relationship("DocumentVersion", foreign_keys=[version_a_id])
    version_b = db.relationship("DocumentVersion", foreign_keys=[version_b_id])
    report_file = db.relationship("ManagedFile")


class ComplianceReport(TimestampMixin, db.Model):
    __tablename__ = "compliance_reports"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)
    file_id = db.Column(db.Integer, db.ForeignKey("managed_files.id"), nullable=False, index=True)
    output_file_id = db.Column(db.Integer, db.ForeignKey("managed_files.id"), index=True)
    report_type = db.Column(db.String(40), nullable=False, default="pdfa_check", index=True)
    status = db.Column(db.String(20), nullable=False, default="completed", index=True)
    issue_count = db.Column(db.Integer, nullable=False, default=0)
    report_json = db.Column(db.JSON, nullable=False, default=dict)

    user = db.relationship("User")
    file = db.relationship("ManagedFile", foreign_keys=[file_id])
    output_file = db.relationship("ManagedFile", foreign_keys=[output_file_id])


class TeamWorkspace(TimestampMixin, db.Model):
    __tablename__ = "team_workspaces"

    id = db.Column(db.Integer, primary_key=True)
    owner_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)
    name = db.Column(db.String(160), nullable=False)
    description = db.Column(db.Text, nullable=False, default="")
    is_personal = db.Column(db.Boolean, nullable=False, default=False)
    status = db.Column(db.String(20), nullable=False, default="active", index=True)

    owner = db.relationship("User")
    members = db.relationship(
        "TeamWorkspaceMember",
        back_populates="workspace",
        lazy="dynamic",
        cascade="all, delete-orphan",
    )
    projects = db.relationship(
        "TeamProject",
        back_populates="workspace",
        lazy="dynamic",
        cascade="all, delete-orphan",
    )


class TeamWorkspaceMember(TimestampMixin, db.Model):
    __tablename__ = "team_workspace_members"
    __table_args__ = (
        UniqueConstraint("workspace_id", "email", name="uq_workspace_member_email"),
    )

    id = db.Column(db.Integer, primary_key=True)
    workspace_id = db.Column(db.Integer, db.ForeignKey("team_workspaces.id"), nullable=False, index=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), index=True)
    invited_by_user_id = db.Column(db.Integer, db.ForeignKey("users.id"), index=True)
    email = db.Column(db.String(255), nullable=False, index=True)
    role = db.Column(db.String(20), nullable=False, default="viewer", index=True)
    status = db.Column(db.String(20), nullable=False, default="pending", index=True)
    joined_at = db.Column(db.DateTime(timezone=True))

    workspace = db.relationship("TeamWorkspace", back_populates="members")
    user = db.relationship("User", foreign_keys=[user_id])
    invited_by = db.relationship("User", foreign_keys=[invited_by_user_id])


class TeamProject(TimestampMixin, db.Model):
    __tablename__ = "team_projects"

    id = db.Column(db.Integer, primary_key=True)
    workspace_id = db.Column(db.Integer, db.ForeignKey("team_workspaces.id"), nullable=False, index=True)
    owner_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)
    name = db.Column(db.String(180), nullable=False)
    description = db.Column(db.Text, nullable=False, default="")
    approval_status = db.Column(db.String(20), nullable=False, default="pending", index=True)
    metadata_json = db.Column(db.JSON, nullable=False, default=dict)

    workspace = db.relationship("TeamWorkspace", back_populates="projects")
    owner = db.relationship("User")
    project_files = db.relationship(
        "TeamProjectFile",
        back_populates="project",
        lazy="dynamic",
        cascade="all, delete-orphan",
    )
    comments = db.relationship(
        "TeamComment",
        back_populates="project",
        lazy="dynamic",
        cascade="all, delete-orphan",
    )


class TeamProjectFile(TimestampMixin, db.Model):
    __tablename__ = "team_project_files"

    id = db.Column(db.Integer, primary_key=True)
    project_id = db.Column(db.Integer, db.ForeignKey("team_projects.id"), nullable=False, index=True)
    file_id = db.Column(db.Integer, db.ForeignKey("managed_files.id"), nullable=False, index=True)
    added_by_user_id = db.Column(db.Integer, db.ForeignKey("users.id"), index=True)

    project = db.relationship("TeamProject", back_populates="project_files")
    file = db.relationship("ManagedFile")
    added_by = db.relationship("User")


class TeamComment(TimestampMixin, db.Model):
    __tablename__ = "team_comments"

    id = db.Column(db.Integer, primary_key=True)
    project_id = db.Column(db.Integer, db.ForeignKey("team_projects.id"), nullable=False, index=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)
    content = db.Column(db.Text, nullable=False, default="")
    target_type = db.Column(db.String(40), nullable=False, default="project", index=True)
    target_id = db.Column(db.String(120), nullable=False, default="")

    project = db.relationship("TeamProject", back_populates="comments")
    user = db.relationship("User")


class TeamActivity(TimestampMixin, db.Model):
    __tablename__ = "team_activities"

    id = db.Column(db.Integer, primary_key=True)
    workspace_id = db.Column(db.Integer, db.ForeignKey("team_workspaces.id"), nullable=False, index=True)
    actor_user_id = db.Column(db.Integer, db.ForeignKey("users.id"), index=True)
    action = db.Column(db.String(80), nullable=False, index=True)
    details_json = db.Column(db.JSON, nullable=False, default=dict)

    workspace = db.relationship("TeamWorkspace")
    actor = db.relationship("User")


class ApiKey(TimestampMixin, db.Model):
    __tablename__ = "api_keys"
    __table_args__ = (
        UniqueConstraint("key_prefix", name="uq_api_key_prefix"),
    )

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)
    name = db.Column(db.String(120), nullable=False, default="Default API Key")
    key_prefix = db.Column(db.String(20), nullable=False, index=True)
    key_hash = db.Column(db.String(128), nullable=False, unique=True)
    is_active = db.Column(db.Boolean, nullable=False, default=True, index=True)
    rate_limit_per_minute = db.Column(db.Integer, nullable=False, default=60)
    last_used_at = db.Column(db.DateTime(timezone=True))
    expires_at = db.Column(db.DateTime(timezone=True))

    user = db.relationship("User")


class ApiUsageLog(TimestampMixin, db.Model):
    __tablename__ = "api_usage_logs"

    id = db.Column(db.Integer, primary_key=True)
    api_key_id = db.Column(db.Integer, db.ForeignKey("api_keys.id"), index=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), index=True)
    endpoint = db.Column(db.String(255), nullable=False, default="", index=True)
    method = db.Column(db.String(8), nullable=False, default="GET")
    status_code = db.Column(db.Integer, nullable=False, default=200, index=True)
    response_ms = db.Column(db.Integer, nullable=False, default=0)
    ip_address = db.Column(db.String(80), nullable=False, default="")
    details_json = db.Column(db.JSON, nullable=False, default=dict)

    api_key = db.relationship("ApiKey")
    user = db.relationship("User")


class WebhookEndpoint(TimestampMixin, db.Model):
    __tablename__ = "webhook_endpoints"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)
    name = db.Column(db.String(120), nullable=False, default="Webhook")
    url = db.Column(db.String(500), nullable=False, default="")
    secret = db.Column(db.String(120), nullable=False, default="")
    event_types_json = db.Column(db.JSON, nullable=False, default=list)
    is_active = db.Column(db.Boolean, nullable=False, default=True, index=True)
    last_status_code = db.Column(db.Integer)
    last_called_at = db.Column(db.DateTime(timezone=True))
    failure_count = db.Column(db.Integer, nullable=False, default=0)

    user = db.relationship("User")


class WebhookDelivery(TimestampMixin, db.Model):
    __tablename__ = "webhook_deliveries"

    id = db.Column(db.Integer, primary_key=True)
    endpoint_id = db.Column(db.Integer, db.ForeignKey("webhook_endpoints.id"), nullable=False, index=True)
    event_name = db.Column(db.String(80), nullable=False, index=True)
    payload_json = db.Column(db.JSON, nullable=False, default=dict)
    status_code = db.Column(db.Integer, nullable=False, default=0)
    response_body = db.Column(db.Text, nullable=False, default="")
    success = db.Column(db.Boolean, nullable=False, default=False, index=True)

    endpoint = db.relationship("WebhookEndpoint")


class PrivacySetting(TimestampMixin, db.Model):
    __tablename__ = "privacy_settings"
    __table_args__ = (
        UniqueConstraint("user_id", name="uq_privacy_setting_user"),
    )

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)
    auto_delete_hours = db.Column(db.Integer, nullable=False, default=24)
    private_mode_enabled = db.Column(db.Boolean, nullable=False, default=False)
    allow_share_links = db.Column(db.Boolean, nullable=False, default=True)
    keep_download_history = db.Column(db.Boolean, nullable=False, default=True)

    user = db.relationship("User")


class FileAccessLog(TimestampMixin, db.Model):
    __tablename__ = "file_access_logs"

    id = db.Column(db.Integer, primary_key=True)
    owner_user_id = db.Column(db.Integer, db.ForeignKey("users.id"), index=True)
    actor_user_id = db.Column(db.Integer, db.ForeignKey("users.id"), index=True)
    file_id = db.Column(db.Integer, db.ForeignKey("managed_files.id"), index=True)
    action = db.Column(db.String(40), nullable=False, default="download", index=True)
    ip_address = db.Column(db.String(80), nullable=False, default="")
    user_agent = db.Column(db.String(255), nullable=False, default="")
    details_json = db.Column(db.JSON, nullable=False, default=dict)

    owner = db.relationship("User", foreign_keys=[owner_user_id])
    actor = db.relationship("User", foreign_keys=[actor_user_id])
    file = db.relationship("ManagedFile")


class ShareAccessLog(TimestampMixin, db.Model):
    __tablename__ = "share_access_logs"

    id = db.Column(db.Integer, primary_key=True)
    share_link_id = db.Column(db.Integer, db.ForeignKey("share_links.id"), nullable=False, index=True)
    event = db.Column(db.String(40), nullable=False, default="view", index=True)
    status = db.Column(db.String(20), nullable=False, default="ok", index=True)
    ip_address = db.Column(db.String(80), nullable=False, default="")
    user_agent = db.Column(db.String(255), nullable=False, default="")
    details_json = db.Column(db.JSON, nullable=False, default=dict)

    share_link = db.relationship("ShareLink")

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
    password_hash = db.Column(db.String(255), nullable=False)
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
    reset_tokens = db.relationship(
        "PasswordResetToken", back_populates="user", lazy="dynamic"
    )
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


class PasswordResetToken(TimestampMixin, db.Model):
    __tablename__ = "password_reset_tokens"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)
    token = db.Column(db.String(120), unique=True, nullable=False, index=True)
    expires_at = db.Column(db.DateTime(timezone=True), nullable=False)
    used_at = db.Column(db.DateTime(timezone=True))

    user = db.relationship("User", back_populates="reset_tokens")


class EmailOTPChallenge(TimestampMixin, db.Model):
    __tablename__ = "email_otp_challenges"

    id = db.Column(db.Integer, primary_key=True)
    purpose = db.Column(db.String(20), nullable=False, index=True)
    email = db.Column(db.String(255), nullable=False, index=True)
    token = db.Column(db.String(160), unique=True, nullable=False, index=True)
    otp_hash = db.Column(db.String(255), nullable=False)
    payload_json = db.Column(db.JSON, default=dict, nullable=False)
    expires_at = db.Column(db.DateTime(timezone=True), nullable=False)
    used_at = db.Column(db.DateTime(timezone=True))
    attempt_count = db.Column(db.Integer, default=0, nullable=False)
    max_attempts = db.Column(db.Integer, default=5, nullable=False)


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

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone

from flask import current_app, has_app_context
from sqlalchemy import func

from app.extensions import db
from app.models import (
    Payment,
    SubscriptionEvent,
    ToolCatalog,
    User,
    UserSubscription,
    WalletTransaction,
    utcnow,
)


def _env_int(name: str, default: int) -> int:
    raw_value = (os.environ.get(name, "") or "").strip()
    if not raw_value:
        return default
    try:
        return int(raw_value)
    except ValueError:
        return default


def _config_int(name: str, default: int) -> int:
    if has_app_context():
        value = current_app.config.get(name)
        if isinstance(value, int):
            return value
        try:
            if value not in (None, ""):
                return int(value)
        except (TypeError, ValueError):
            pass
    return _env_int(name, default)


def _config_str(name: str, default: str) -> str:
    if has_app_context():
        value = current_app.config.get(name)
        if value is not None:
            cleaned = str(value).strip()
            if cleaned:
                return cleaned
    return (os.environ.get(name, default) or default).strip()


class SubscriptionService:
    CUSTOM_PLAN_KEY = "pro_custom"
    PLAN_ORDER = [
        "pro_monthly",
        "pro_3_months",
        "pro_6_months",
        "pro_yearly",
        "pro_2_years",
    ]

    DEFAULT_CUSTOM_DAILY_RATE_PAISE = 100
    DEFAULT_CUSTOM_MIN_DAYS = 5
    DEFAULT_CUSTOM_MAX_DAYS = 365
    DEFAULT_EXPIRING_SOON_DAYS = 7
    DEFAULT_PRICE_PROFILE = "default"

    PLAN_DEFINITIONS = {
        "pro_monthly": {
            "name": "1 Month",
            "label": "1M",
            "duration_days": 30,
            "tagline": "Good for trying premium",
            "cta_label": "Start 1 Month Plan",
            "description": "Perfect for short projects and quick upgrades.",
            "benefits": [
                "Try full premium features",
                "Ideal for one-off tasks",
                "Quick activation",
            ],
            "badge": "",
            "highlight": False,
        },
        "pro_3_months": {
            "name": "3 Months",
            "label": "3M",
            "duration_days": 90,
            "tagline": "Most popular for regular users",
            "cta_label": "Choose Most Popular",
            "description": "Balanced value for students and daily workflows.",
            "benefits": [
                "Better daily pricing",
                "Fewer renewals",
                "Popular user choice",
            ],
            "badge": "Most Popular",
            "highlight": True,
        },
        "pro_6_months": {
            "name": "6 Months",
            "label": "6M",
            "duration_days": 180,
            "tagline": "Better long-term savings",
            "cta_label": "Get 6 Month Access",
            "description": "Great for continuous academic and office usage.",
            "benefits": [
                "Strong mid-term savings",
                "Premium for longer projects",
                "Less renewal friction",
            ],
            "badge": "",
            "highlight": False,
        },
        "pro_yearly": {
            "name": "1 Year",
            "label": "1Y",
            "duration_days": 365,
            "tagline": "Best for committed users",
            "cta_label": "Unlock 1 Year Premium",
            "description": "Best annual value with uninterrupted premium access.",
            "benefits": [
                "Best annual savings",
                "Priority-ready workflow",
                "Worry-free for a full year",
            ],
            "badge": "Best Value",
            "highlight": True,
        },
        "pro_2_years": {
            "name": "2 Years",
            "label": "2Y",
            "duration_days": 730,
            "tagline": "Lowest effective price",
            "cta_label": "Get Best Value Plan",
            "description": "Maximum long-term value for power users.",
            "benefits": [
                "Lowest per-day rate",
                "Maximum continuity",
                "Best for heavy usage",
            ],
            "badge": "",
            "highlight": False,
        },
    }

    PLAN_PRICE_PROFILES = {
        "default": {
            "pro_monthly": 2500,
            "pro_3_months": 6500,
            "pro_6_months": 12000,
            "pro_yearly": 27500,
            "pro_2_years": 45000,
        },
        "option_b": {
            "pro_monthly": 2900,
            "pro_3_months": 7900,
            "pro_6_months": 14900,
            "pro_yearly": 29900,
            "pro_2_years": 49900,
        },
    }

    PREMIUM_BENEFITS = [
        "No ads",
        "Faster processing",
        "Higher file upload limits",
        "Access to AI tools",
        "Priority support",
        "Cloud history and saved tasks",
    ]

    CUSTOM_QUICK_CHIPS = [7, 15, 30, 90]

    @classmethod
    def custom_daily_rate_paise(cls) -> int:
        return max(1, _config_int("CUSTOM_DAILY_RATE_PAISE", cls.DEFAULT_CUSTOM_DAILY_RATE_PAISE))

    @classmethod
    def custom_days_range(cls) -> tuple[int, int]:
        min_days = max(1, _config_int("CUSTOM_PLAN_MIN_DAYS", cls.DEFAULT_CUSTOM_MIN_DAYS))
        max_days = max(min_days, _config_int("CUSTOM_PLAN_MAX_DAYS", cls.DEFAULT_CUSTOM_MAX_DAYS))
        return min_days, max_days

    @classmethod
    def expiring_soon_days(cls) -> int:
        return max(1, _config_int("EXPIRING_SOON_DAYS", cls.DEFAULT_EXPIRING_SOON_DAYS))

    @classmethod
    def active_price_profile_key(cls) -> str:
        candidate = _config_str("SUBSCRIPTION_PRICE_PROFILE", cls.DEFAULT_PRICE_PROFILE).lower()
        return candidate if candidate in cls.PLAN_PRICE_PROFILES else cls.DEFAULT_PRICE_PROFILE

    @classmethod
    def active_plan_prices(cls) -> dict[str, int]:
        return cls.PLAN_PRICE_PROFILES.get(
            cls.active_price_profile_key(),
            cls.PLAN_PRICE_PROFILES[cls.DEFAULT_PRICE_PROFILE],
        )

    @classmethod
    def plans(cls) -> dict[str, dict]:
        prices = cls.active_plan_prices()
        merged: dict[str, dict] = {}
        for plan_key in cls.PLAN_ORDER:
            if plan_key not in cls.PLAN_DEFINITIONS:
                continue
            meta = cls.PLAN_DEFINITIONS[plan_key]
            merged[plan_key] = {
                "plan_key": plan_key,
                "name": meta["name"],
                "label": meta.get("label") or meta["name"],
                "price_paise": int(prices.get(plan_key, 0)),
                "duration_days": int(meta["duration_days"]),
                "tagline": meta.get("tagline", ""),
                "cta_label": meta.get("cta_label", "Activate plan"),
                "description": meta.get("description", ""),
                "benefits": list(meta.get("benefits") or []),
                "badge": meta.get("badge", ""),
                "highlight": bool(meta.get("highlight")),
            }
        return merged

    @classmethod
    def plan_catalog(cls) -> list[dict]:
        plans = cls.plans()
        return [plans[key] for key in cls.PLAN_ORDER if key in plans]

    @classmethod
    def plan_view_models(cls, active_plan_key: str | None = None) -> list[dict]:
        plans = cls.plan_catalog()
        monthly = next((plan for plan in plans if plan["plan_key"] == "pro_monthly"), None)
        monthly_per_day = (
            (int(monthly["price_paise"]) / int(monthly["duration_days"]))
            if monthly and int(monthly.get("duration_days") or 0) > 0
            else 0
        )

        view_models: list[dict] = []
        for plan in plans:
            duration_days = int(plan.get("duration_days") or 0)
            price_paise = int(plan.get("price_paise") or 0)
            per_day_paise = (price_paise / duration_days) if duration_days else price_paise
            per_day_rupees = per_day_paise / 100
            savings_percent = 0
            if monthly_per_day and plan["plan_key"] != "pro_monthly":
                savings_percent = max(
                    int(round(((monthly_per_day - per_day_paise) / monthly_per_day) * 100)),
                    0,
                )
            is_active_plan = bool(active_plan_key and active_plan_key == plan["plan_key"])
            button_label = plan.get("cta_label") or "Activate plan"
            if is_active_plan:
                button_label = f"Renew {plan['name']}"
            badge = (plan.get("badge") or "").strip()
            badge_key = badge.lower().replace(" ", "_")
            view_models.append(
                {
                    **plan,
                    "per_day_paise": per_day_paise,
                    "per_day_rupees": per_day_rupees,
                    "savings_percent": savings_percent,
                    "is_active_plan": is_active_plan,
                    "button_label": button_label,
                    "badge": badge,
                    "badge_key": badge_key,
                    "is_recommended": badge_key == "most_popular",
                    "is_best_value": badge_key == "best_value",
                    "is_highlighted": bool(plan.get("highlight")),
                    "duration_label": cls.duration_label(duration_days),
                }
            )
        return view_models

    @staticmethod
    def duration_label(duration_days: int | None) -> str:
        days = max(0, int(duration_days or 0))
        if days <= 0:
            return "-"
        if days % 365 == 0:
            years = days // 365
            return f"{years} year" if years == 1 else f"{years} years"
        if days % 30 == 0:
            months = days // 30
            return f"{months} month" if months == 1 else f"{months} months"
        return f"{days} day" if days == 1 else f"{days} days"

    @classmethod
    def _parse_custom_days(cls, custom_days: str | int | None) -> int:
        min_days, max_days = cls.custom_days_range()
        if custom_days is None:
            raise ValueError("Please enter number of days.")
        try:
            days = int(str(custom_days).strip())
        except Exception as exc:
            raise ValueError("Days must be a valid number.") from exc
        if days < min_days or days > max_days:
            raise ValueError(f"Days must be between {min_days} and {max_days}.")
        return days

    @classmethod
    def custom_price_paise(cls, days: int) -> int:
        return int(days * cls.custom_daily_rate_paise())

    @classmethod
    def resolve_plan_purchase(cls, plan_key: str, custom_days: str | int | None = None) -> dict:
        normalized_key = (plan_key or "").strip()
        plans = cls.plans()
        if normalized_key in plans:
            plan = plans[normalized_key]
            return {
                "plan_key": normalized_key,
                "name": plan["name"],
                "label": plan["label"],
                "price_paise": int(plan["price_paise"]),
                "duration_days": int(plan["duration_days"]),
                "tagline": plan.get("tagline", ""),
                "is_custom": False,
                "custom_days": None,
            }
        if normalized_key == cls.CUSTOM_PLAN_KEY:
            days = cls._parse_custom_days(custom_days)
            price_paise = cls.custom_price_paise(days)
            return {
                "plan_key": cls.CUSTOM_PLAN_KEY,
                "name": f"Pro {days} Days",
                "label": "Custom",
                "price_paise": price_paise,
                "duration_days": days,
                "tagline": f"Custom premium access for {days} days.",
                "is_custom": True,
                "custom_days": days,
            }
        raise ValueError("Invalid subscription plan.")

    @staticmethod
    def _as_utc_aware(value: datetime) -> datetime:
        if value.tzinfo is None or value.tzinfo.utcoffset(value) is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)

    @classmethod
    def _status_for_expiry(cls, expires_at: datetime | None, now: datetime | None = None) -> dict:
        now_utc = cls._as_utc_aware(now or datetime.now(timezone.utc))
        if not expires_at:
            return {
                "status_key": "expired",
                "status_label": "Expired",
                "status_tone": "expired",
                "days_remaining": 0,
            }
        expiry_utc = cls._as_utc_aware(expires_at)
        if expiry_utc <= now_utc:
            return {
                "status_key": "expired",
                "status_label": "Expired",
                "status_tone": "expired",
                "days_remaining": 0,
            }
        days_remaining = max((expiry_utc.date() - now_utc.date()).days, 0)
        if days_remaining <= cls.expiring_soon_days():
            return {
                "status_key": "expiring_soon",
                "status_label": "Expiring Soon",
                "status_tone": "expiring",
                "days_remaining": days_remaining,
            }
        return {
            "status_key": "active",
            "status_label": "Active",
            "status_tone": "active",
            "days_remaining": days_remaining,
        }

    @classmethod
    def subscription_status_summary(
        cls,
        subscription: UserSubscription,
        now: datetime | None = None,
    ) -> dict:
        now_utc = cls._as_utc_aware(now or datetime.now(timezone.utc))
        started_at = cls._as_utc_aware(subscription.started_at) if subscription.started_at else None
        expires_at = cls._as_utc_aware(subscription.expires_at) if subscription.expires_at else None
        status_meta = cls._status_for_expiry(expires_at, now_utc)

        if status_meta["status_key"] == "expired":
            renewal_hint = "Renew now to reactivate premium features."
        elif status_meta["status_key"] == "expiring_soon":
            renewal_hint = "Renew now. New duration is added after current expiry."
        else:
            renewal_hint = "Renew anytime. New duration is added after current expiry."

        return {
            "plan_name": subscription.plan_name,
            "plan_key": subscription.plan_key,
            "started_at": started_at,
            "expires_at": expires_at,
            "days_remaining": status_meta["days_remaining"],
            "status_key": status_meta["status_key"],
            "status_label": status_meta["status_label"],
            "status_tone": status_meta["status_tone"],
            "renewal_hint": renewal_hint,
        }

    @staticmethod
    def latest_subscription_for_user(user_id: int) -> UserSubscription | None:
        return (
            UserSubscription.query.filter_by(user_id=user_id)
            .order_by(UserSubscription.expires_at.desc(), UserSubscription.updated_at.desc())
            .first()
        )

    @classmethod
    def active_subscription_for_user(cls, user_id: int) -> UserSubscription | None:
        now = cls._as_utc_aware(datetime.now(timezone.utc))
        subscription = (
            UserSubscription.query.filter(
                UserSubscription.user_id == user_id,
                UserSubscription.status.in_(["active", "expiring_soon"]),
            )
            .order_by(UserSubscription.expires_at.desc(), UserSubscription.updated_at.desc())
            .first()
        )
        if not subscription:
            subscription = cls.latest_subscription_for_user(user_id)
            if not subscription:
                return None

        status_meta = cls._status_for_expiry(subscription.expires_at, now)
        should_commit = False
        if subscription.status != status_meta["status_key"]:
            subscription.status = status_meta["status_key"]
            should_commit = True
        if status_meta["status_key"] == "expired" and not subscription.cancelled_at:
            subscription.cancelled_at = now
            should_commit = True
        if should_commit:
            db.session.commit()
        if status_meta["status_key"] == "expired":
            return None
        return subscription

    @staticmethod
    def is_user_premium(user: User) -> bool:
        if user.is_admin:
            return True
        return SubscriptionService.active_subscription_for_user(user.id) is not None

    @staticmethod
    def require_tool_access(user: User, tool: ToolCatalog) -> None:
        if not tool.is_subscription_only:
            return
        if not SubscriptionService.is_user_premium(user):
            raise ValueError("This tool is Pro-only. Subscribe from dashboard to continue.")

    @staticmethod
    def subscribe(user: User, plan_key: str) -> UserSubscription:
        raise ValueError("Direct wallet subscription is disabled. Please use Razorpay checkout.")

    @staticmethod
    def _record_subscription_event(
        *,
        user_id: int,
        event_type: str,
        source: str,
        delta_days: int,
        previous_expiry: datetime | None,
        new_expiry: datetime | None,
        payment_ref: str = "",
        actor_id: int | None = None,
        notes: str = "",
        metadata: dict | None = None,
    ) -> None:
        event = SubscriptionEvent(
            user_id=user_id,
            event_type=(event_type or "").strip() or "updated",
            source=(source or "").strip() or "system",
            delta_days=int(delta_days or 0),
            previous_expiry=previous_expiry,
            new_expiry=new_expiry,
            payment_ref=(payment_ref or "").strip(),
            actor_id=actor_id,
            notes=(notes or "").strip()[:255],
            metadata_json=metadata or {},
        )
        db.session.add(event)

    @classmethod
    def activate_after_gateway_payment(
        cls,
        user: User,
        plan_key: str,
        payment_id: str,
        order_id: str,
        custom_days: str | int | None = None,
        gateway_payload: dict | None = None,
    ) -> UserSubscription:
        plan = cls.resolve_plan_purchase(plan_key, custom_days=custom_days)
        payment_id = (payment_id or "").strip()
        order_id = (order_id or "").strip()
        if not payment_id or not order_id:
            raise ValueError("Missing payment reference.")

        transaction_reference = f"RZP-PAY-{payment_id}"
        payment_record = Payment.query.filter_by(razorpay_order_id=order_id).first()
        existing_transaction = WalletTransaction.query.filter_by(reference=transaction_reference).first()
        if existing_transaction:
            if payment_record and payment_record.status != "success":
                callback_fields = (gateway_payload or {}).get("callback_fields") or {}
                payment_record.status = "success"
                payment_record.razorpay_payment_id = payment_id
                payment_record.razorpay_signature = (
                    callback_fields.get("razorpay_signature") or payment_record.razorpay_signature
                )
                payment_record.reference = transaction_reference
                payment_record.paid_at = payment_record.paid_at or utcnow()
                payment_record.failed_at = None
                payment_record.error_message = ""
                db.session.commit()
            existing_subscription = cls.latest_subscription_for_user(user.id)
            if existing_subscription:
                return existing_subscription
            raise ValueError("Payment already processed.")

        active = cls.active_subscription_for_user(user.id)
        now = datetime.now(timezone.utc)
        payload = gateway_payload or {}
        custom_days_value = plan.get("custom_days")
        callback_fields = payload.get("callback_fields") or {}
        purchased_days = int(plan["duration_days"])

        previous_expiry = None
        if active:
            previous_expiry = cls._as_utc_aware(active.expires_at)
            renewal_anchor = previous_expiry if previous_expiry > now else now
            new_expiry = renewal_anchor + timedelta(days=purchased_days)
            renewals = int((active.metadata_json or {}).get("renewals", 0)) + 1
            active.plan_key = plan["plan_key"]
            active.plan_name = plan["name"]
            active.status = cls._status_for_expiry(new_expiry, now)["status_key"]
            active.expires_at = new_expiry
            active.cancelled_at = None
            active.price_paise = int(plan["price_paise"])
            if not active.started_at:
                active.started_at = now
            active.metadata_json = {
                **(active.metadata_json or {}),
                "renewals": renewals,
                "last_plan_key": plan["plan_key"],
                "last_payment_id": payment_id,
                "last_order_id": order_id,
                "last_custom_days": custom_days_value,
                "last_purchase_at": now.isoformat(),
                "renewal_anchor": renewal_anchor.isoformat(),
                "policy": "extend_term_on_purchase",
                "gateway": "razorpay",
                "gateway_payload": payload,
            }
            subscription = active
            event_type = "renewed"
        else:
            new_expiry = now + timedelta(days=purchased_days)
            subscription = UserSubscription(
                user_id=user.id,
                plan_key=plan["plan_key"],
                plan_name=plan["name"],
                status=cls._status_for_expiry(new_expiry, now)["status_key"],
                price_paise=int(plan["price_paise"]),
                started_at=now,
                expires_at=new_expiry,
                metadata_json={
                    "renewals": 0,
                    "policy": "extend_term_on_purchase",
                    "gateway": "razorpay",
                    "last_payment_id": payment_id,
                    "last_order_id": order_id,
                    "last_custom_days": custom_days_value,
                    "gateway_payload": payload,
                },
            )
            db.session.add(subscription)
            event_type = "activated"

        transaction = WalletTransaction(
            user_id=user.id,
            transaction_type="subscription",
            amount_paise=-int(plan["price_paise"]),
            balance_after_paise=user.wallet_balance_paise,
            reference=transaction_reference,
            note=f"Razorpay subscription purchase: {plan['name']} ({order_id})",
        )
        db.session.add(transaction)

        if not payment_record:
            payment_record = Payment(
                user_id=user.id,
                gateway="razorpay",
                status="success",
                amount_paise=int(plan["price_paise"]),
                currency="INR",
                plan_key=plan["plan_key"],
                plan_name=plan["name"],
                duration_days=purchased_days,
                razorpay_order_id=order_id,
            )
            db.session.add(payment_record)

        payment_record.user_id = user.id
        payment_record.status = "success"
        payment_record.amount_paise = int(plan["price_paise"])
        payment_record.currency = (
            ((payload.get("order") or {}).get("currency") or payment_record.currency or "INR")
            .strip()
            .upper()
        )
        payment_record.plan_key = plan["plan_key"]
        payment_record.plan_name = plan["name"]
        payment_record.duration_days = purchased_days
        payment_record.razorpay_payment_id = payment_id
        payment_record.razorpay_signature = (
            callback_fields.get("razorpay_signature") or payment_record.razorpay_signature
        )
        payment_record.reference = transaction_reference
        payment_record.paid_at = utcnow()
        payment_record.failed_at = None
        payment_record.error_message = ""
        payment_record.notes_json = {
            **(payment_record.notes_json or {}),
            **((payload.get("order") or {}).get("notes") or {}),
            "gateway_payload": payload,
            "policy": "extend_term_on_purchase",
        }

        cls._record_subscription_event(
            user_id=user.id,
            event_type=event_type,
            source="razorpay",
            delta_days=purchased_days,
            previous_expiry=previous_expiry,
            new_expiry=new_expiry,
            payment_ref=transaction_reference,
            notes=f"Razorpay {event_type} for {plan['name']}",
            metadata={
                "plan_key": plan["plan_key"],
                "plan_name": plan["name"],
                "order_id": order_id,
                "payment_id": payment_id,
                "custom_days": custom_days_value,
            },
        )

        db.session.commit()
        return subscription

    @classmethod
    def payment_status_meta(cls, status: str | None) -> dict:
        normalized = (status or "pending").strip().lower()
        mapping = {
            "success": {"status": "success", "label": "Success", "tone": "success"},
            "failed": {"status": "failed", "label": "Failed", "tone": "danger"},
            "pending": {"status": "pending", "label": "Pending", "tone": "warning"},
        }
        return mapping.get(normalized, mapping["pending"])

    @classmethod
    def serialize_payment(cls, payment: Payment) -> dict:
        status_meta = cls.payment_status_meta(payment.status)
        timestamp = payment.paid_at or payment.created_at
        return {
            "id": payment.id,
            "timestamp": cls._as_utc_aware(timestamp) if timestamp else None,
            "plan_name": (payment.plan_name or "Premium Plan").strip() or "Premium Plan",
            "plan_key": (payment.plan_key or "").strip(),
            "amount_paise": int(payment.amount_paise or 0),
            "currency": (payment.currency or "INR").upper(),
            "duration_days": int(payment.duration_days or 0),
            "duration_label": cls.duration_label(payment.duration_days),
            "status": status_meta["status"],
            "status_label": status_meta["label"],
            "status_tone": status_meta["tone"],
            "order_id": (payment.razorpay_order_id or "").strip(),
            "payment_id": (payment.razorpay_payment_id or "").strip(),
            "reference": (
                payment.reference
                or payment.razorpay_payment_id
                or payment.razorpay_order_id
                or ""
            ).strip(),
        }

    @classmethod
    def list_user_transactions(cls, user_id: int, limit: int = 8) -> list[dict]:
        rows = (
            Payment.query.filter_by(user_id=user_id)
            .order_by(Payment.created_at.desc(), Payment.id.desc())
            .limit(max(1, int(limit or 1)))
            .all()
        )
        return [cls.serialize_payment(row) for row in rows]

    @classmethod
    def paginated_user_transactions(cls, user_id: int, page: int = 1, per_page: int = 20):
        query = Payment.query.filter_by(user_id=user_id).order_by(Payment.created_at.desc(), Payment.id.desc())
        pagination = query.paginate(
            page=max(1, int(page or 1)),
            per_page=max(1, int(per_page or 20)),
            error_out=False,
        )
        items = [cls.serialize_payment(row) for row in pagination.items]
        return pagination, items

    @classmethod
    def admin_grant_subscription(
        cls,
        *,
        user: User,
        plan_key: str,
        actor: User,
        custom_days: str | int | None = None,
        notes: str = "",
        source: str = "admin_manual",
    ) -> UserSubscription:
        plan = cls.resolve_plan_purchase(plan_key, custom_days=custom_days)
        now = datetime.now(timezone.utc)
        active = cls.active_subscription_for_user(user.id)
        previous_expiry = cls._as_utc_aware(active.expires_at) if active else None
        anchor = previous_expiry if previous_expiry and previous_expiry > now else now
        new_expiry = anchor + timedelta(days=int(plan["duration_days"]))

        if active:
            active.plan_key = plan["plan_key"]
            active.plan_name = plan["name"]
            active.price_paise = int(plan["price_paise"])
            active.status = cls._status_for_expiry(new_expiry, now)["status_key"]
            active.expires_at = new_expiry
            active.cancelled_at = None
            if not active.started_at:
                active.started_at = now
            active.metadata_json = {
                **(active.metadata_json or {}),
                "source": source,
                "admin_actor_id": actor.id,
                "last_admin_action": "grant_or_renew",
                "last_admin_note": (notes or "").strip()[:255],
                "policy": "extend_term_on_purchase",
            }
            subscription = active
            event_type = "admin_grant_extend"
        else:
            subscription = UserSubscription(
                user_id=user.id,
                plan_key=plan["plan_key"],
                plan_name=plan["name"],
                status=cls._status_for_expiry(new_expiry, now)["status_key"],
                price_paise=int(plan["price_paise"]),
                started_at=now,
                expires_at=new_expiry,
                metadata_json={
                    "source": source,
                    "admin_actor_id": actor.id,
                    "last_admin_action": "grant",
                    "last_admin_note": (notes or "").strip()[:255],
                    "policy": "extend_term_on_purchase",
                },
            )
            db.session.add(subscription)
            event_type = "admin_grant"

        cls._record_subscription_event(
            user_id=user.id,
            event_type=event_type,
            source=source,
            delta_days=int(plan["duration_days"]),
            previous_expiry=previous_expiry,
            new_expiry=new_expiry,
            actor_id=actor.id,
            notes=notes or f"Admin granted {plan['name']}",
            metadata={
                "plan_key": plan["plan_key"],
                "plan_name": plan["name"],
                "custom_days": plan.get("custom_days"),
            },
        )
        db.session.commit()
        return subscription

    @classmethod
    def admin_extend_days(
        cls,
        *,
        user: User,
        extra_days: int,
        actor: User,
        notes: str = "",
        source: str = "admin_manual",
    ) -> UserSubscription:
        days = int(extra_days or 0)
        if days <= 0:
            raise ValueError("Extension days must be greater than zero.")

        now = datetime.now(timezone.utc)
        active = cls.active_subscription_for_user(user.id)
        previous_expiry = cls._as_utc_aware(active.expires_at) if active else None
        anchor = previous_expiry if previous_expiry and previous_expiry > now else now
        new_expiry = anchor + timedelta(days=days)

        if active:
            active.status = cls._status_for_expiry(new_expiry, now)["status_key"]
            active.expires_at = new_expiry
            active.cancelled_at = None
            active.metadata_json = {
                **(active.metadata_json or {}),
                "source": source,
                "admin_actor_id": actor.id,
                "last_admin_action": "extend_days",
                "last_admin_note": (notes or "").strip()[:255],
            }
            subscription = active
        else:
            subscription = UserSubscription(
                user_id=user.id,
                plan_key="admin_extension",
                plan_name="Admin Extension",
                status=cls._status_for_expiry(new_expiry, now)["status_key"],
                price_paise=0,
                started_at=now,
                expires_at=new_expiry,
                metadata_json={
                    "source": source,
                    "admin_actor_id": actor.id,
                    "last_admin_action": "extend_days",
                    "last_admin_note": (notes or "").strip()[:255],
                },
            )
            db.session.add(subscription)

        cls._record_subscription_event(
            user_id=user.id,
            event_type="admin_extend",
            source=source,
            delta_days=days,
            previous_expiry=previous_expiry,
            new_expiry=new_expiry,
            actor_id=actor.id,
            notes=notes or f"Admin extended subscription by {days} days",
            metadata={"extra_days": days},
        )
        db.session.commit()
        return subscription

    @classmethod
    def admin_revoke_subscription(
        cls,
        *,
        user: User,
        actor: User,
        notes: str = "",
        source: str = "admin_manual",
    ) -> UserSubscription:
        subscription = cls.active_subscription_for_user(user.id)
        if not subscription:
            raise ValueError("User does not have an active premium subscription.")

        now = datetime.now(timezone.utc)
        previous_expiry = cls._as_utc_aware(subscription.expires_at) if subscription.expires_at else None
        removed_days = 0
        if previous_expiry and previous_expiry > now:
            removed_days = max((previous_expiry.date() - now.date()).days, 0)
            subscription.expires_at = now

        subscription.status = "expired"
        subscription.cancelled_at = now
        subscription.metadata_json = {
            **(subscription.metadata_json or {}),
            "source": source,
            "admin_actor_id": actor.id,
            "last_admin_action": "revoke",
            "last_admin_note": (notes or "").strip()[:255],
        }

        cls._record_subscription_event(
            user_id=user.id,
            event_type="admin_revoke",
            source=source,
            delta_days=-removed_days,
            previous_expiry=previous_expiry,
            new_expiry=subscription.expires_at,
            actor_id=actor.id,
            notes=notes or "Admin revoked premium access",
            metadata={"removed_days": removed_days},
        )
        db.session.commit()
        return subscription

    @classmethod
    def premium_analytics_summary(cls) -> dict:
        now = datetime.now(timezone.utc)
        subscriptions = UserSubscription.query.order_by(UserSubscription.expires_at.desc()).all()
        seen_users: set[int] = set()
        active_count = 0
        expiring_count = 0
        expired_count = 0

        for sub in subscriptions:
            if sub.user_id in seen_users:
                continue
            seen_users.add(sub.user_id)
            status_meta = cls._status_for_expiry(sub.expires_at, now)
            if status_meta["status_key"] == "active":
                active_count += 1
            elif status_meta["status_key"] == "expiring_soon":
                expiring_count += 1
            else:
                expired_count += 1

        sales_rows = (
            db.session.query(
                Payment.plan_key,
                Payment.plan_name,
                func.count(Payment.id),
                func.coalesce(func.sum(Payment.amount_paise), 0),
            )
            .filter(Payment.status == "success")
            .group_by(Payment.plan_key, Payment.plan_name)
            .order_by(func.count(Payment.id).desc())
            .all()
        )

        plan_sales = [
            {
                "plan_key": (row[0] or "").strip(),
                "plan_name": (row[1] or row[0] or "Unknown").strip(),
                "purchases": int(row[2] or 0),
                "revenue_paise": int(row[3] or 0),
            }
            for row in sales_rows
        ]

        revenue_total = (
            db.session.query(func.coalesce(func.sum(Payment.amount_paise), 0))
            .filter(Payment.status == "success")
            .scalar()
            or 0
        )

        recent_events = (
            SubscriptionEvent.query.order_by(SubscriptionEvent.created_at.desc()).limit(12).all()
        )

        return {
            "total_premium_users": active_count + expiring_count,
            "active_count": active_count,
            "expiring_count": expiring_count,
            "expired_count": expired_count,
            "revenue_paise": int(revenue_total),
            "plan_sales": plan_sales,
            "recent_events": recent_events,
        }

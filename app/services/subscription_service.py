from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.extensions import db
from app.models import Payment, ToolCatalog, User, UserSubscription, WalletTransaction, utcnow


class SubscriptionService:
    CUSTOM_PLAN_KEY = "pro_custom"
    CUSTOM_DAILY_RATE_PAISE = 100
    CUSTOM_MIN_DAYS = 1
    CUSTOM_MAX_DAYS = 3650

    PLANS = {
        "pro_monthly": {
            "name": "1M Plan",
            "label": "1M",
            "price_paise": 2500,
            "duration_days": 30,
            "tagline": "30 days premium access.",
        },
        "pro_3_months": {
            "name": "3M Plan",
            "label": "3M",
            "price_paise": 6500,
            "duration_days": 90,
            "tagline": "90 days premium access.",
        },
        "pro_6_months": {
            "name": "6M Plan",
            "label": "6M",
            "price_paise": 12000,
            "duration_days": 180,
            "tagline": "180 days premium access.",
        },
        "pro_yearly": {
            "name": "1Y Plan",
            "label": "1Y",
            "price_paise": 27500,
            "duration_days": 365,
            "tagline": "365 days premium access.",
        },
        "pro_2_years": {
            "name": "2Y Plan",
            "label": "2Y",
            "price_paise": 45000,
            "duration_days": 730,
            "tagline": "730 days premium access.",
        },
    }

    @classmethod
    def plan_catalog(cls) -> list[dict]:
        return [{"plan_key": key, **details} for key, details in cls.PLANS.items()]

    @classmethod
    def _parse_custom_days(cls, custom_days: str | int | None) -> int:
        if custom_days is None:
            raise ValueError("Please enter number of days.")
        try:
            days = int(str(custom_days).strip())
        except Exception as exc:
            raise ValueError("Days must be a valid number.") from exc
        if days < cls.CUSTOM_MIN_DAYS or days > cls.CUSTOM_MAX_DAYS:
            raise ValueError(
                f"Days must be between {cls.CUSTOM_MIN_DAYS} and {cls.CUSTOM_MAX_DAYS}."
            )
        return days

    @classmethod
    def custom_price_paise(cls, days: int) -> int:
        return int(days * cls.CUSTOM_DAILY_RATE_PAISE)

    @classmethod
    def resolve_plan_purchase(cls, plan_key: str, custom_days: str | int | None = None) -> dict:
        normalized_key = (plan_key or "").strip()
        if normalized_key in cls.PLANS:
            plan = cls.PLANS[normalized_key]
            return {
                "plan_key": normalized_key,
                "name": plan["name"],
                "price_paise": int(plan["price_paise"]),
                "duration_days": int(plan["duration_days"]),
                "tagline": plan["tagline"],
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

    @staticmethod
    def active_subscription_for_user(user_id: int) -> UserSubscription | None:
        now = SubscriptionService._as_utc_aware(datetime.now(timezone.utc))
        subscription = (
            UserSubscription.query.filter_by(user_id=user_id, status="active")
            .order_by(UserSubscription.expires_at.desc())
            .first()
        )
        if not subscription:
            return None
        expires_at = SubscriptionService._as_utc_aware(subscription.expires_at)
        plan = SubscriptionService.PLANS.get(subscription.plan_key)
        if plan:
            expected_expiry = SubscriptionService._as_utc_aware(
                subscription.started_at
            ) + timedelta(days=plan["duration_days"])
            should_commit = False
            if expires_at > expected_expiry:
                expires_at = expected_expiry
                subscription.expires_at = expected_expiry
                should_commit = True
            if subscription.price_paise != plan["price_paise"]:
                subscription.price_paise = plan["price_paise"]
                should_commit = True
            if should_commit:
                db.session.commit()
        if expires_at <= now:
            subscription.status = "expired"
            db.session.commit()
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
    def activate_after_gateway_payment(
        user: User,
        plan_key: str,
        payment_id: str,
        order_id: str,
        custom_days: str | int | None = None,
        gateway_payload: dict | None = None,
    ) -> UserSubscription:
        plan = SubscriptionService.resolve_plan_purchase(plan_key, custom_days=custom_days)
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
            existing_subscription = (
                UserSubscription.query.filter_by(user_id=user.id)
                .order_by(UserSubscription.updated_at.desc())
                .first()
            )
            if existing_subscription:
                return existing_subscription
            raise ValueError("Payment already processed.")

        active = SubscriptionService.active_subscription_for_user(user.id)
        now = datetime.now(timezone.utc)
        expiry = now + timedelta(days=int(plan["duration_days"]))
        payload = gateway_payload or {}
        custom_days_value = plan.get("custom_days")
        callback_fields = payload.get("callback_fields") or {}

        if active:
            renewals = int((active.metadata_json or {}).get("renewals", 0)) + 1
            active.plan_key = plan["plan_key"]
            active.plan_name = plan["name"]
            active.status = "active"
            active.started_at = now
            active.expires_at = expiry
            active.price_paise = int(plan["price_paise"])
            active.metadata_json = {
                **(active.metadata_json or {}),
                "renewals": renewals,
                "last_plan_key": plan["plan_key"],
                "last_payment_id": payment_id,
                "last_order_id": order_id,
                "last_custom_days": custom_days_value,
                "last_purchase_at": now.isoformat(),
                "policy": "reset_term_on_purchase",
                "gateway": "razorpay",
                "gateway_payload": payload,
            }
            subscription = active
        else:
            subscription = UserSubscription(
                user_id=user.id,
                plan_key=plan["plan_key"],
                plan_name=plan["name"],
                status="active",
                price_paise=int(plan["price_paise"]),
                started_at=now,
                expires_at=expiry,
                metadata_json={
                    "renewals": 0,
                    "policy": "reset_term_on_purchase",
                    "gateway": "razorpay",
                    "last_payment_id": payment_id,
                    "last_order_id": order_id,
                    "last_custom_days": custom_days_value,
                    "gateway_payload": payload,
                },
            )
            db.session.add(subscription)

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
                duration_days=int(plan["duration_days"]),
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
        payment_record.duration_days = int(plan["duration_days"])
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
            "policy": "reset_term_on_purchase",
        }

        db.session.commit()
        return subscription

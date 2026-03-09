from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from flask import current_app

from app.extensions import db
from app.models import Payment, User, utcnow
from app.services.subscription_service import SubscriptionService


class PaymentGatewayService:
    @staticmethod
    def is_live_mode() -> bool:
        return (current_app.config.get("PAYMENT_MODE") or "demo").strip().lower() == "live"

    @staticmethod
    def is_demo_mode() -> bool:
        return not PaymentGatewayService.is_live_mode()

    @staticmethod
    def _credentials() -> tuple[str, str]:
        key_id = (current_app.config.get("RAZORPAY_KEY_ID") or "").strip()
        key_secret = (current_app.config.get("RAZORPAY_KEY_SECRET") or "").strip()
        if not key_id or not key_secret:
            raise ValueError("Razorpay keys are not configured. Please set RAZORPAY_KEY_ID and RAZORPAY_KEY_SECRET.")
        return key_id, key_secret

    @staticmethod
    def _auth_header() -> str:
        key_id, key_secret = PaymentGatewayService._credentials()
        token = base64.b64encode(f"{key_id}:{key_secret}".encode("utf-8")).decode("utf-8")
        return f"Basic {token}"

    @staticmethod
    def _request_json(method: str, path: str, payload: dict | None = None) -> dict:
        api_base = current_app.config["RAZORPAY_API_BASE"]
        url = f"{api_base}{path}"
        body = json.dumps(payload or {}).encode("utf-8") if payload is not None else None
        request = Request(
            url=url,
            data=body,
            method=method.upper(),
            headers={
                "Authorization": PaymentGatewayService._auth_header(),
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
        )
        try:
            with urlopen(request, timeout=25) as response:
                raw = response.read().decode("utf-8")
        except HTTPError as exc:
            details = ""
            try:
                payload = json.loads(exc.read().decode("utf-8"))
                details = payload.get("error", {}).get("description") or payload.get("error", {}).get("reason") or ""
            except Exception:
                details = ""
            message = details or f"Razorpay API request failed with status {exc.code}."
            raise ValueError(message) from exc
        except URLError as exc:
            raise ValueError("Unable to reach Razorpay. Please try again.") from exc
        try:
            return json.loads(raw)
        except Exception as exc:
            raise ValueError("Invalid response from Razorpay API.") from exc

    @staticmethod
    def create_subscription_order(
        user: User,
        plan_key: str,
        custom_days: str | int | None = None,
    ) -> dict:
        plan = SubscriptionService.resolve_plan_purchase(plan_key, custom_days=custom_days)
        resolved_plan_key = plan["plan_key"]
        amount_paise = int(plan["price_paise"])
        if amount_paise <= 0:
            raise ValueError("Invalid plan amount.")
        currency = (current_app.config.get("RAZORPAY_CURRENCY") or "INR").upper()
        if plan.get("is_custom"):
            receipt_plan = f"custom{plan['custom_days']}"
        else:
            receipt_plan = resolved_plan_key
        receipt = f"sub_{user.id}_{receipt_plan}_{int(time.time())}"
        payload = {
            "amount": amount_paise,
            "currency": currency,
            "receipt": receipt,
            "notes": {
                "user_id": str(user.id),
                "plan_key": resolved_plan_key,
                "plan_name": plan["name"],
                "duration_days": str(plan["duration_days"]),
                "custom_days": str(plan["custom_days"] or ""),
            },
        }
        if PaymentGatewayService.is_demo_mode():
            order = {
                "id": f"demo_order_{secrets.token_hex(10)}",
                "amount": amount_paise,
                "currency": currency,
                "receipt": receipt,
                "status": "created",
                "notes": payload["notes"],
                "is_demo": True,
            }
        else:
            order = PaymentGatewayService._request_json("POST", "/v1/orders", payload)
        order_amount = int(order.get("amount") or 0)
        if order_amount != amount_paise:
            raise ValueError("Razorpay amount mismatch. Please retry.")
        order_id = (order.get("id") or "").strip()
        if order_id:
            payment = Payment.query.filter_by(razorpay_order_id=order_id).first()
            if not payment:
                payment = Payment(
                    user_id=user.id,
                    gateway="demo" if PaymentGatewayService.is_demo_mode() else "razorpay",
                    status="pending",
                    amount_paise=amount_paise,
                    currency=currency,
                    plan_key=plan["plan_key"],
                    plan_name=plan["name"],
                    duration_days=int(plan["duration_days"]),
                    razorpay_order_id=order_id,
                    notes_json=order.get("notes") or payload["notes"],
                )
                db.session.add(payment)
            else:
                payment.user_id = user.id
                payment.status = "pending"
                payment.gateway = "demo" if PaymentGatewayService.is_demo_mode() else "razorpay"
                payment.amount_paise = amount_paise
                payment.currency = currency
                payment.plan_key = plan["plan_key"]
                payment.plan_name = plan["name"]
                payment.duration_days = int(plan["duration_days"])
                payment.notes_json = order.get("notes") or payload["notes"]
                payment.error_message = ""
                payment.failed_at = None
            db.session.commit()
        return order

    @staticmethod
    def verify_signature(order_id: str, payment_id: str, signature: str) -> bool:
        if PaymentGatewayService.is_demo_mode():
            if not order_id or not payment_id:
                raise ValueError("Invalid demo payment reference.")
            return True
        _, key_secret = PaymentGatewayService._credentials()
        payload = f"{order_id}|{payment_id}".encode("utf-8")
        digest = hmac.new(key_secret.encode("utf-8"), payload, hashlib.sha256).hexdigest()
        if not hmac.compare_digest(digest, (signature or "").strip()):
            raise ValueError("Payment signature verification failed.")
        return True

    @staticmethod
    def verify_webhook_signature(raw_body: bytes, signature: str) -> bool:
        secret = (current_app.config.get("RAZORPAY_WEBHOOK_SECRET") or "").strip()
        if not secret:
            raise ValueError("Razorpay webhook secret is not configured.")
        expected = hmac.new(secret.encode("utf-8"), raw_body, hashlib.sha256).hexdigest()
        if not hmac.compare_digest(expected, (signature or "").strip()):
            raise ValueError("Webhook signature verification failed.")
        return True

    @staticmethod
    def fetch_order(order_id: str) -> dict:
        if not order_id:
            raise ValueError("Missing order id.")
        if PaymentGatewayService.is_demo_mode():
            payment = Payment.query.filter_by(razorpay_order_id=order_id).first()
            if not payment:
                raise ValueError("Demo order not found.")
            return {
                "id": payment.razorpay_order_id,
                "amount": payment.amount_paise,
                "currency": payment.currency,
                "status": payment.status,
                "notes": payment.notes_json or {},
                "is_demo": True,
            }
        return PaymentGatewayService._request_json("GET", f"/v1/orders/{order_id}")

    @staticmethod
    def fetch_order_payments(order_id: str) -> list[dict]:
        order_id = (order_id or "").strip()
        if not order_id:
            raise ValueError("Missing order id.")
        if PaymentGatewayService.is_demo_mode():
            payment = Payment.query.filter_by(razorpay_order_id=order_id).first()
            if not payment:
                return []
            demo_status = "captured" if (payment.status or "").strip().lower() == "success" else "created"
            return [
                {
                    "id": payment.razorpay_payment_id or "",
                    "order_id": payment.razorpay_order_id,
                    "status": demo_status,
                    "amount": payment.amount_paise,
                    "currency": payment.currency,
                }
            ]
        payload = PaymentGatewayService._request_json("GET", f"/v1/orders/{order_id}/payments")
        items = payload.get("items") if isinstance(payload, dict) else None
        if not isinstance(items, list):
            return []
        return [item for item in items if isinstance(item, dict)]

    @staticmethod
    def payment_row_for_order(order_id: str, *, user_id: int | None = None) -> Payment | None:
        order_id = (order_id or "").strip()
        if not order_id:
            return None
        query = Payment.query.filter_by(razorpay_order_id=order_id)
        if user_id is not None:
            query = query.filter_by(user_id=int(user_id))
        return query.first()

    @staticmethod
    def validate_order_vs_payment_row(
        order: dict,
        payment_row: Payment | None,
        *,
        expected_user_id: int | None = None,
    ) -> None:
        if not payment_row:
            raise ValueError("Payment order is not registered in system.")

        notes = (order or {}).get("notes") or {}
        note_user_id = int(notes.get("user_id", "0") or 0)
        if note_user_id and note_user_id != int(payment_row.user_id):
            raise ValueError("Order-user mapping mismatch.")
        if expected_user_id and int(payment_row.user_id) != int(expected_user_id):
            raise ValueError("Order does not belong to current user.")

        order_amount = int((order or {}).get("amount") or 0)
        if order_amount and int(payment_row.amount_paise or 0) != order_amount:
            raise ValueError("Order amount mismatch with registered payment record.")

        order_currency = ((order or {}).get("currency") or "").strip().upper()
        row_currency = (payment_row.currency or "").strip().upper()
        if order_currency and row_currency and order_currency != row_currency:
            raise ValueError("Order currency mismatch with registered payment record.")

        note_plan_key = (notes.get("plan_key") or "").strip()
        row_plan_key = (payment_row.plan_key or "").strip()
        if note_plan_key and row_plan_key and note_plan_key != row_plan_key:
            raise ValueError("Order plan mismatch with registered payment record.")

    @staticmethod
    def mark_payment_failed(
        order_id: str,
        *,
        payment_id: str = "",
        error_message: str = "",
    ) -> None:
        order_id = (order_id or "").strip()
        if not order_id:
            return
        payment = Payment.query.filter_by(razorpay_order_id=order_id).first()
        if not payment:
            return
        if payment.status == "success":
            return
        payment.status = "failed"
        payment.failed_at = utcnow()
        if payment_id:
            payment.razorpay_payment_id = payment_id
        if error_message:
            payment.error_message = error_message[:500]
        db.session.commit()

    @staticmethod
    def confirm_demo_payment(user: User, order_id: str):
        if PaymentGatewayService.is_live_mode():
            raise ValueError("Demo payment confirmation is disabled in live mode.")
        order_id = (order_id or "").strip()
        payment = Payment.query.filter_by(razorpay_order_id=order_id).first()
        if not payment:
            raise ValueError("Demo order not found.")
        if payment.user_id != user.id:
            raise ValueError("This order does not belong to the current user.")
        if payment.status == "success":
            existing_subscription = SubscriptionService.latest_subscription_for_user(user.id)
            if existing_subscription:
                return existing_subscription
            raise ValueError("Payment already processed.")

        payment_id = payment.razorpay_payment_id or f"demo_pay_{secrets.token_hex(10)}"
        return SubscriptionService.activate_after_gateway_payment(
            user=user,
            plan_key=payment.plan_key,
            payment_id=payment_id,
            order_id=order_id,
            custom_days=(payment.notes_json or {}).get("custom_days") or None,
            gateway_payload={
                "order": {
                    "id": order_id,
                    "amount": payment.amount_paise,
                    "currency": payment.currency,
                    "notes": payment.notes_json or {},
                    "is_demo": True,
                },
                "callback_fields": {
                    "razorpay_payment_id": payment_id,
                    "razorpay_order_id": order_id,
                    "razorpay_signature": "demo_signature",
                },
            },
        )

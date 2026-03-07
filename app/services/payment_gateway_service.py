from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from flask import current_app

from app.extensions import db
from app.models import Payment, User, utcnow
from app.services.subscription_service import SubscriptionService


class PaymentGatewayService:
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
                    gateway="razorpay",
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
        _, key_secret = PaymentGatewayService._credentials()
        payload = f"{order_id}|{payment_id}".encode("utf-8")
        digest = hmac.new(key_secret.encode("utf-8"), payload, hashlib.sha256).hexdigest()
        if not hmac.compare_digest(digest, (signature or "").strip()):
            raise ValueError("Payment signature verification failed.")
        return True

    @staticmethod
    def fetch_order(order_id: str) -> dict:
        if not order_id:
            raise ValueError("Missing order id.")
        return PaymentGatewayService._request_json("GET", f"/v1/orders/{order_id}")

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

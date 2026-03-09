from __future__ import annotations

import hashlib
import hmac
import json
import secrets
from datetime import timedelta
from urllib.error import URLError
from urllib.request import Request, urlopen

from flask import current_app

from app.extensions import db
from app.models import ApiKey, ApiUsageLog, WebhookDelivery, WebhookEndpoint, utcnow


class APIAutomationService:
    @staticmethod
    def _hash_key(raw_key: str) -> str:
        return hashlib.sha256((raw_key or "").encode("utf-8")).hexdigest()

    @staticmethod
    def create_api_key(
        *,
        user_id: int,
        name: str,
        rate_limit_per_minute: int = 0,
        expires_days: int | None = None,
    ) -> tuple[ApiKey, str]:
        raw_key = f"pdfm_{secrets.token_urlsafe(32)}"
        effective_rate_limit = int(
            rate_limit_per_minute
            or current_app.config.get("API_DEFAULT_RATE_LIMIT_PER_MINUTE")
            or 60
        )
        row = ApiKey(
            user_id=user_id,
            name=(name or "Default API Key").strip()[:120],
            key_prefix=raw_key[:20],
            key_hash=APIAutomationService._hash_key(raw_key),
            is_active=True,
            rate_limit_per_minute=max(10, min(2000, effective_rate_limit)),
            expires_at=(utcnow() + timedelta(days=expires_days)) if expires_days else None,
        )
        db.session.add(row)
        db.session.commit()
        return row, raw_key

    @staticmethod
    def revoke_api_key(*, key_id: int, user_id: int) -> None:
        row = ApiKey.query.filter_by(id=key_id, user_id=user_id).first()
        if not row:
            raise ValueError("API key not found.")
        row.is_active = False
        db.session.commit()

    @staticmethod
    def list_api_keys(user_id: int, limit: int = 50) -> list[ApiKey]:
        return (
            ApiKey.query.filter_by(user_id=user_id)
            .order_by(ApiKey.created_at.desc())
            .limit(max(1, limit))
            .all()
        )

    @staticmethod
    def _extract_api_key(raw_authorization: str, raw_header_key: str) -> str:
        header_key = (raw_header_key or "").strip()
        if header_key:
            return header_key
        auth = (raw_authorization or "").strip()
        if auth.lower().startswith("bearer "):
            return auth.split(" ", 1)[1].strip()
        return ""

    @staticmethod
    def authenticate_request(raw_authorization: str, raw_header_key: str) -> ApiKey:
        token = APIAutomationService._extract_api_key(raw_authorization, raw_header_key)
        if not token:
            raise ValueError("API key is missing.")

        row = ApiKey.query.filter_by(key_hash=APIAutomationService._hash_key(token)).first()
        if not row or not row.is_active:
            raise ValueError("Invalid API key.")
        if row.expires_at and row.expires_at < utcnow():
            raise ValueError("API key has expired.")

        one_minute_ago = utcnow() - timedelta(minutes=1)
        usage_count = ApiUsageLog.query.filter(
            ApiUsageLog.api_key_id == row.id,
            ApiUsageLog.created_at >= one_minute_ago,
        ).count()
        if usage_count >= int(row.rate_limit_per_minute or 60):
            raise ValueError("API rate limit exceeded. Retry shortly.")

        row.last_used_at = utcnow()
        db.session.commit()
        return row

    @staticmethod
    def log_usage(
        *,
        api_key: ApiKey | None,
        user_id: int | None,
        endpoint: str,
        method: str,
        status_code: int,
        response_ms: int,
        ip_address: str = "",
        details: dict | None = None,
    ) -> None:
        row = ApiUsageLog(
            api_key_id=api_key.id if api_key else None,
            user_id=user_id,
            endpoint=(endpoint or "")[:255],
            method=(method or "GET")[:8],
            status_code=int(status_code),
            response_ms=max(0, int(response_ms or 0)),
            ip_address=(ip_address or "")[:80],
            details_json=details or {},
        )
        db.session.add(row)
        db.session.commit()

    @staticmethod
    def create_webhook_endpoint(
        *,
        user_id: int,
        name: str,
        url: str,
        event_types: list[str],
        secret: str,
    ) -> WebhookEndpoint:
        endpoint = WebhookEndpoint(
            user_id=user_id,
            name=(name or "Webhook").strip()[:120],
            url=(url or "").strip()[:500],
            secret=(secret or secrets.token_urlsafe(24)).strip()[:120],
            event_types_json=event_types or ["job.completed"],
            is_active=True,
            failure_count=0,
        )
        if not endpoint.url.startswith(("http://", "https://")):
            raise ValueError("Webhook URL must start with http:// or https://")
        db.session.add(endpoint)
        db.session.commit()
        return endpoint

    @staticmethod
    def list_webhooks(user_id: int, limit: int = 50) -> list[WebhookEndpoint]:
        return (
            WebhookEndpoint.query.filter_by(user_id=user_id)
            .order_by(WebhookEndpoint.created_at.desc())
            .limit(max(1, limit))
            .all()
        )

    @staticmethod
    def disable_webhook(*, webhook_id: int, user_id: int) -> None:
        endpoint = WebhookEndpoint.query.filter_by(id=webhook_id, user_id=user_id).first()
        if not endpoint:
            raise ValueError("Webhook not found.")
        endpoint.is_active = False
        db.session.commit()

    @staticmethod
    def dispatch_user_event(*, user_id: int, event_name: str, payload: dict) -> None:
        endpoints = WebhookEndpoint.query.filter_by(user_id=user_id, is_active=True).all()
        if not endpoints:
            return

        body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        for endpoint in endpoints:
            subscribed = endpoint.event_types_json or []
            if event_name not in subscribed and "*" not in subscribed:
                continue
            signature = hmac.new(
                (endpoint.secret or "").encode("utf-8"),
                body,
                hashlib.sha256,
            ).hexdigest()
            request = Request(
                endpoint.url,
                data=body,
                method="POST",
                headers={
                    "Content-Type": "application/json",
                    "X-PDFMaster-Event": event_name,
                    "X-PDFMaster-Signature": signature,
                },
            )
            status_code = 0
            response_body = ""
            success = False
            try:
                with urlopen(request, timeout=10) as response:  # noqa: S310
                    status_code = int(getattr(response, "status", 200) or 200)
                    response_body = response.read(1024).decode("utf-8", errors="ignore")
                    success = 200 <= status_code < 300
            except URLError as exc:
                response_body = str(exc.reason)
            except Exception as exc:
                response_body = str(exc)

            endpoint.last_called_at = utcnow()
            endpoint.last_status_code = status_code
            endpoint.failure_count = 0 if success else int(endpoint.failure_count or 0) + 1
            if endpoint.failure_count >= int(current_app.config.get("WEBHOOK_MAX_FAILURES") or 10):
                endpoint.is_active = False

            delivery = WebhookDelivery(
                endpoint_id=endpoint.id,
                event_name=event_name,
                payload_json=payload,
                status_code=status_code,
                response_body=response_body[:4000],
                success=success,
            )
            db.session.add(delivery)
            db.session.commit()

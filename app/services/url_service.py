from __future__ import annotations

from urllib.parse import quote_plus, urlsplit

from flask import current_app, request, url_for


class UrlService:
    LOOPBACK_HOSTS = {"localhost", "127.0.0.1", "::1"}

    @staticmethod
    def normalize_base_url(raw_url: str | None) -> str:
        value = (raw_url or "").strip().rstrip("/")
        if not value:
            return ""
        parsed = urlsplit(value)
        if not parsed.scheme or not parsed.netloc:
            return ""
        return f"{parsed.scheme}://{parsed.netloc}".rstrip("/")

    @classmethod
    def _request_hostname(cls) -> str:
        raw_host = (request.host or "").strip().lower()
        if not raw_host:
            return ""
        if raw_host.startswith("[") and "]" in raw_host:
            return raw_host.split("]", 1)[0].lstrip("[")
        return raw_host.split(":", 1)[0]

    @classmethod
    def is_loopback_request(cls) -> bool:
        return cls._request_hostname() in cls.LOOPBACK_HOSTS

    @classmethod
    def request_base_url(cls) -> str:
        return cls.normalize_base_url(request.url_root)

    @classmethod
    def public_base_url(cls) -> str:
        request_base = cls.request_base_url()
        configured_base = cls.normalize_base_url(current_app.config.get("PUBLIC_BASE_URL", ""))
        if cls.is_loopback_request():
            return request_base
        return configured_base or request_base

    @classmethod
    def build_external_url(cls, endpoint: str, **values) -> str:
        path = url_for(endpoint, **values)
        return f"{cls.public_base_url()}{path}"

    @classmethod
    def resolve_app_url(
        cls,
        endpoint: str,
        *,
        config_key: str = "",
        external: bool = False,
        **values,
    ) -> str:
        configured_url = (current_app.config.get(config_key, "") or "").strip() if config_key else ""
        if configured_url:
            if configured_url.startswith(("http://", "https://")):
                if cls.is_loopback_request():
                    return url_for(endpoint, **values)
                return configured_url
            if configured_url.startswith("/"):
                return configured_url
        if external:
            return cls.build_external_url(endpoint, **values)
        return url_for(endpoint, **values)

    @staticmethod
    def gmail_compose_url(*, to_email: str, subject: str = "", body: str = "") -> str:
        recipient = (to_email or "").strip()
        if not recipient:
            return "https://mail.google.com/mail/"
        return (
            "https://mail.google.com/mail/?view=cm&fs=1"
            f"&to={quote_plus(recipient)}"
            f"&su={quote_plus(subject or '')}"
            f"&body={quote_plus(body or '')}"
        )

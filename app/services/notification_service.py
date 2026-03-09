from __future__ import annotations

import smtplib
from email.message import EmailMessage

from flask import current_app


class NotificationService:
    @staticmethod
    def is_email_enabled() -> bool:
        host = (current_app.config.get("SMTP_HOST") or "").strip()
        from_email = (current_app.config.get("SMTP_FROM_EMAIL") or "").strip()
        return bool(host and from_email)

    @staticmethod
    def send_email(
        *,
        to_email: str,
        subject: str,
        body_text: str,
        body_html: str = "",
    ) -> tuple[bool, str]:
        recipient = (to_email or "").strip()
        if not recipient:
            return False, "Recipient email is required."

        if not NotificationService.is_email_enabled():
            current_app.logger.warning(
                "Email delivery skipped because SMTP is not configured. to=%s subject=%s",
                recipient,
                subject,
            )
            return False, "SMTP is not configured."

        host = (current_app.config.get("SMTP_HOST") or "").strip()
        port = int(current_app.config.get("SMTP_PORT") or 587)
        username = (current_app.config.get("SMTP_USERNAME") or "").strip()
        password = (current_app.config.get("SMTP_PASSWORD") or "").strip()
        from_email = (current_app.config.get("SMTP_FROM_EMAIL") or "").strip()
        use_tls = bool(current_app.config.get("SMTP_USE_TLS"))
        use_ssl = bool(current_app.config.get("SMTP_USE_SSL"))

        message = EmailMessage()
        message["Subject"] = subject
        message["From"] = from_email
        message["To"] = recipient
        message.set_content((body_text or "").strip() or subject)
        if body_html:
            message.add_alternative(body_html, subtype="html")

        try:
            if use_ssl:
                with smtplib.SMTP_SSL(host, port, timeout=12) as client:
                    if username:
                        client.login(username, password)
                    client.send_message(message)
            else:
                with smtplib.SMTP(host, port, timeout=12) as client:
                    client.ehlo()
                    if use_tls:
                        client.starttls()
                        client.ehlo()
                    if username:
                        client.login(username, password)
                    client.send_message(message)
            return True, "sent"
        except Exception as exc:
            current_app.logger.exception("Email delivery failed. to=%s", recipient)
            return False, str(exc)

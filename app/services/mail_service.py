from __future__ import annotations

import smtplib
import socket
import ssl
from collections.abc import Mapping
from email.message import EmailMessage
from email.utils import formataddr

from flask import current_app


class OTPRequestError(RuntimeError):
    DEFAULT_MESSAGE = "Unable to send OTP right now. Please try again in a moment."

    def __init__(self, message: str | None = None) -> None:
        super().__init__(message or self.DEFAULT_MESSAGE)


class MailDeliveryError(OTPRequestError):
    pass


class MailConfigurationError(OTPRequestError):
    pass


class MailService:
    @staticmethod
    def mask_email(email: str) -> str:
        normalized = (email or "").strip()
        if "@" not in normalized:
            return normalized or "<empty>"
        local_part, domain = normalized.split("@", 1)
        if len(local_part) <= 2:
            masked_local = f"{local_part[:1]}*"
        else:
            masked_local = f"{local_part[:2]}***{local_part[-1:]}"
        return f"{masked_local}@{domain}"

    @staticmethod
    def inspect_config(config: Mapping[str, object]) -> tuple[dict[str, object], list[str]]:
        server = str(config.get("MAIL_SERVER", "") or "").strip()
        username = str(config.get("MAIL_USERNAME", "") or "").strip()
        password = str(config.get("MAIL_PASSWORD", "") or "").strip()
        default_sender = str(config.get("MAIL_DEFAULT_SENDER", "") or "").strip()
        port = config.get("MAIL_PORT", 0)
        timeout = config.get("MAIL_TIMEOUT_SECONDS", 10)
        use_tls = bool(config.get("MAIL_USE_TLS", True))
        use_ssl = bool(config.get("MAIL_USE_SSL", False))

        issues: list[str] = []
        if not server:
            issues.append("MAIL_SERVER is missing")
        if not isinstance(port, int) or port <= 0:
            issues.append("MAIL_PORT must be a positive integer")
        if not username:
            issues.append("MAIL_USERNAME is missing")
        if not password:
            issues.append("MAIL_PASSWORD is missing")
        if not default_sender:
            issues.append("MAIL_DEFAULT_SENDER is missing")
        if not isinstance(timeout, int) or timeout <= 0:
            issues.append("MAIL_TIMEOUT_SECONDS must be a positive integer")
        if use_tls and use_ssl:
            issues.append("MAIL_USE_TLS and MAIL_USE_SSL cannot both be true")

        settings = {
            "server": server,
            "port": port if isinstance(port, int) else 0,
            "username": username,
            "password": password,
            "default_sender": default_sender,
            "sender_name": str(config.get("MAIL_SENDER_NAME", "") or "").strip(),
            "timeout": timeout if isinstance(timeout, int) else 0,
            "use_tls": use_tls,
            "use_ssl": use_ssl,
        }
        return settings, issues

    @staticmethod
    def _validated_settings(context: str) -> dict[str, object]:
        settings, issues = MailService.inspect_config(current_app.config)
        if issues:
            current_app.logger.error(
                "Mail configuration validation failed during %s: %s",
                context,
                "; ".join(issues),
            )
            raise MailConfigurationError()
        return settings

    @staticmethod
    def build_message(
        *,
        recipient: str,
        subject: str,
        text_body: str,
        html_body: str,
        sender_email: str,
        sender_name: str = "",
    ) -> EmailMessage:
        message = EmailMessage()
        message["Subject"] = subject
        message["To"] = recipient
        message["From"] = formataddr((sender_name, sender_email)) if sender_name else sender_email
        message.set_content(text_body)
        message.add_alternative(html_body, subtype="html")
        return message

    @staticmethod
    def send_email(
        *,
        recipient: str,
        subject: str,
        text_body: str,
        html_body: str,
        context: str,
    ) -> None:
        settings = MailService._validated_settings(context)
        masked_recipient = MailService.mask_email(recipient)
        try:
            message = MailService.build_message(
                recipient=recipient,
                subject=subject,
                text_body=text_body,
                html_body=html_body,
                sender_email=str(settings["default_sender"]),
                sender_name=str(settings["sender_name"]),
            )
        except Exception as exc:
            current_app.logger.exception(
                "Mail message build failed during %s for %s",
                context,
                masked_recipient,
            )
            raise OTPRequestError() from exc

        MailService._deliver_message(
            message=message,
            settings=settings,
            context=context,
            masked_recipient=masked_recipient,
        )

    @staticmethod
    def _deliver_message(
        *,
        message: EmailMessage,
        settings: dict[str, object],
        context: str,
        masked_recipient: str,
    ) -> None:
        smtp_client: smtplib.SMTP | smtplib.SMTP_SSL | None = None
        phase = "smtp.connect"
        timeout_seconds = int(settings["timeout"])

        try:
            if settings["use_ssl"]:
                smtp_client = smtplib.SMTP_SSL(
                    str(settings["server"]),
                    int(settings["port"]),
                    timeout=timeout_seconds,
                    context=ssl.create_default_context(),
                )
            else:
                # Flask-Mail 0.10.0 does not expose SMTP timeouts, so OTP delivery uses
                # explicit smtplib transport here to keep Render requests bounded.
                smtp_client = smtplib.SMTP(
                    str(settings["server"]),
                    int(settings["port"]),
                    timeout=timeout_seconds,
                )

            phase = "smtp.ehlo"
            smtp_client.ehlo_or_helo_if_needed()

            if settings["use_tls"]:
                phase = "smtp.starttls"
                smtp_client.starttls(context=ssl.create_default_context())
                phase = "smtp.ehlo_tls"
                smtp_client.ehlo_or_helo_if_needed()

            phase = "smtp.login"
            smtp_client.login(str(settings["username"]), str(settings["password"]))

            phase = "smtp.sendmail"
            smtp_client.send_message(message)
            current_app.logger.info(
                "Mail delivery succeeded during %s for %s",
                context,
                masked_recipient,
            )
        except (socket.timeout, TimeoutError) as exc:
            current_app.logger.error(
                "Mail delivery timed out during %s phase=%s recipient=%s timeout=%ss",
                context,
                phase,
                masked_recipient,
                timeout_seconds,
                exc_info=True,
            )
            raise MailDeliveryError() from exc
        except smtplib.SMTPAuthenticationError as exc:
            current_app.logger.exception(
                "SMTP authentication failed during %s phase=%s recipient=%s",
                context,
                phase,
                masked_recipient,
            )
            raise MailConfigurationError() from exc
        except smtplib.SMTPException as exc:
            current_app.logger.exception(
                "SMTP failure during %s phase=%s recipient=%s",
                context,
                phase,
                masked_recipient,
            )
            raise MailDeliveryError() from exc
        except OSError as exc:
            current_app.logger.exception(
                "SMTP network failure during %s phase=%s recipient=%s",
                context,
                phase,
                masked_recipient,
            )
            raise MailDeliveryError() from exc
        finally:
            if smtp_client is not None:
                try:
                    smtp_client.quit()
                except (smtplib.SMTPServerDisconnected, OSError):
                    current_app.logger.debug(
                        "SMTP connection already closed during %s for %s",
                        context,
                        masked_recipient,
                    )
                except Exception:
                    current_app.logger.warning(
                        "SMTP connection cleanup failed during %s for %s",
                        context,
                        masked_recipient,
                        exc_info=True,
                    )

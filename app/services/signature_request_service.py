from __future__ import annotations

import secrets
import string
from pathlib import Path

from flask import current_app

from app.extensions import db
from app.models import (
    SignatureEvent,
    SignatureField,
    SignatureRequest,
    SignatureSigner,
    utcnow,
)
from app.services.notification_service import NotificationService
from app.services.pdf_service import PDFService
from app.services.storage_service import StorageService


class SignatureRequestService:
    SIGNER_FIELD_TYPES = {"signature", "initials", "name", "date", "text"}

    @staticmethod
    def _generate_verification_code() -> str:
        alphabet = string.digits
        return "".join(secrets.choice(alphabet) for _ in range(6))

    @staticmethod
    def _ensure_not_expired(signature_request: SignatureRequest) -> None:
        if signature_request.expires_at and signature_request.expires_at < utcnow():
            signature_request.status = "expired"
            db.session.commit()
            raise ValueError("This signature request has expired.")

    @staticmethod
    def _record_event(
        signature_request: SignatureRequest,
        event_type: str,
        *,
        signer_id: int | None = None,
        ip_address: str = "",
        user_agent: str = "",
        details: dict | None = None,
    ) -> None:
        event = SignatureEvent(
            request_id=signature_request.id,
            signer_id=signer_id,
            event_type=event_type,
            ip_address=(ip_address or "")[:80],
            user_agent=(user_agent or "")[:255],
            details_json=details or {},
        )
        db.session.add(event)
        db.session.commit()

    @staticmethod
    def list_requests_for_user(user_id: int, limit: int = 50) -> list[SignatureRequest]:
        return (
            SignatureRequest.query.filter_by(requester_id=user_id)
            .order_by(SignatureRequest.created_at.desc())
            .limit(max(1, limit))
            .all()
        )

    @staticmethod
    def get_request_for_user(request_id: int, user_id: int) -> SignatureRequest:
        row = SignatureRequest.query.filter_by(id=request_id, requester_id=user_id).first()
        if not row:
            raise ValueError("Signature request not found.")
        return row

    @staticmethod
    def create_request(
        *,
        requester_id: int,
        file_id: int,
        title: str,
        message: str,
        expires_at,
        signers: list[dict],
        fields: list[dict],
        signer_order_enforced: bool,
    ) -> SignatureRequest:
        if not signers:
            raise ValueError("At least one signer is required.")

        signature_request = SignatureRequest(
            requester_id=requester_id,
            file_id=file_id,
            title=(title or "Signature Request").strip()[:255],
            message=(message or "").strip(),
            status="draft",
            signer_order_enforced=bool(signer_order_enforced),
            current_order=1,
            expires_at=expires_at,
            metadata_json={
                "signer_count": len(signers),
                "field_count": len(fields or []),
            },
        )
        db.session.add(signature_request)
        db.session.flush()

        sorted_signers = sorted(signers, key=lambda row: int(row.get("order") or 1))
        signer_rows: list[SignatureSigner] = []
        for signer in sorted_signers:
            email = (signer.get("email") or "").strip().lower()
            if not email:
                raise ValueError("Signer email is required.")
            signer_row = SignatureSigner(
                request_id=signature_request.id,
                user_id=signer.get("user_id"),
                name=(signer.get("name") or email.split("@", 1)[0]).strip()[:140],
                email=email,
                signer_order=max(1, int(signer.get("order") or 1)),
                status="sent",
                verification_code=SignatureRequestService._generate_verification_code(),
                metadata_json={},
            )
            db.session.add(signer_row)
            signer_rows.append(signer_row)

        db.session.flush()
        signer_lookup = {signer.id: signer for signer in signer_rows}

        for field in fields or []:
            field_type = (field.get("field_type") or "signature").strip().lower()
            if field_type not in SignatureRequestService.SIGNER_FIELD_TYPES:
                field_type = "text"
            signer_id = field.get("signer_id")
            if signer_id and signer_id not in signer_lookup:
                signer_id = None
            row = SignatureField(
                request_id=signature_request.id,
                signer_id=signer_id,
                page=max(1, int(field.get("page") or 1)),
                field_type=field_type,
                label=(field.get("label") or field_type.title()).strip()[:120],
                x=float(field.get("x") or 72),
                y=float(field.get("y") or 72),
                width=max(40.0, float(field.get("width") or 160.0)),
                height=max(18.0, float(field.get("height") or 28.0)),
                required=bool(field.get("required", True)),
                value="",
            )
            db.session.add(row)

        db.session.commit()
        SignatureRequestService._record_event(signature_request, "draft_created")
        return signature_request

    @staticmethod
    def send_request(
        *,
        request_id: int,
        requester_id: int,
        access_url_builder,
    ) -> SignatureRequest:
        signature_request = SignatureRequestService.get_request_for_user(request_id, requester_id)
        SignatureRequestService._ensure_not_expired(signature_request)

        signers = (
            SignatureSigner.query.filter_by(request_id=signature_request.id)
            .order_by(SignatureSigner.signer_order.asc(), SignatureSigner.id.asc())
            .all()
        )
        if not signers:
            raise ValueError("No signers found for this request.")

        signature_request.status = "sent"
        signature_request.sent_at = utcnow()
        db.session.commit()

        for signer in signers:
            access_link = access_url_builder(signature_request.access_token, signer.email)
            body = (
                f"Hello {signer.name},\n\n"
                f"You have a signature request: {signature_request.title}\n"
                f"Verification code: {signer.verification_code}\n"
                f"Open link: {access_link}\n\n"
                f"Message from sender:\n{signature_request.message or '(no message)'}\n"
            )
            delivered, reason = NotificationService.send_email(
                to_email=signer.email,
                subject=f"Signature request: {signature_request.title}",
                body_text=body,
            )
            signer.metadata_json = {
                **(signer.metadata_json or {}),
                "last_delivery": "sent" if delivered else "failed",
                "delivery_note": reason,
            }
            db.session.commit()

        SignatureRequestService._record_event(
            signature_request,
            "request_sent",
            details={"signer_count": len(signers)},
        )
        return signature_request

    @staticmethod
    def send_reminder(
        *,
        request_id: int,
        requester_id: int,
        signer_id: int,
        access_url_builder,
    ) -> None:
        signature_request = SignatureRequestService.get_request_for_user(request_id, requester_id)
        signer = SignatureSigner.query.filter_by(id=signer_id, request_id=signature_request.id).first()
        if not signer:
            raise ValueError("Signer not found.")

        access_link = access_url_builder(signature_request.access_token, signer.email)
        delivered, reason = NotificationService.send_email(
            to_email=signer.email,
            subject=f"Reminder: {signature_request.title}",
            body_text=(
                f"Reminder for signature request: {signature_request.title}\n"
                f"Verification code: {signer.verification_code}\n"
                f"Open: {access_link}\n"
            ),
        )
        signer.reminder_count = int(signer.reminder_count or 0) + 1
        signer.metadata_json = {
            **(signer.metadata_json or {}),
            "last_reminder": "sent" if delivered else "failed",
            "reminder_note": reason,
        }
        db.session.commit()
        SignatureRequestService._record_event(
            signature_request,
            "reminder_sent",
            signer_id=signer.id,
            details={"delivered": delivered, "reason": reason},
        )

    @staticmethod
    def get_signer_access(*, token: str, email: str, verification_code: str) -> tuple[SignatureRequest, SignatureSigner]:
        signature_request = SignatureRequest.query.filter_by(access_token=token).first()
        if not signature_request:
            raise ValueError("Invalid signature link.")
        SignatureRequestService._ensure_not_expired(signature_request)

        signer = SignatureSigner.query.filter_by(
            request_id=signature_request.id,
            email=(email or "").strip().lower(),
        ).first()
        if not signer:
            raise ValueError("Signer record not found for this email.")
        code = (verification_code or "").strip()
        if code != signer.verification_code:
            raise ValueError("Invalid signer verification code.")

        if signer.status == "sent":
            signer.status = "viewed"
            signer.viewed_at = utcnow()
            if signature_request.status == "sent":
                signature_request.status = "viewed"
            db.session.commit()
            SignatureRequestService._record_event(
                signature_request,
                "signer_viewed",
                signer_id=signer.id,
            )

        return signature_request, signer

    @staticmethod
    def _ensure_signer_order(signature_request: SignatureRequest, signer: SignatureSigner) -> None:
        if not signature_request.signer_order_enforced:
            return
        expected_order = int(signature_request.current_order or 1)
        if int(signer.signer_order or 1) != expected_order:
            raise ValueError(f"Signing is locked. Current signer order is {expected_order}.")

    @staticmethod
    def submit_signer_fields(
        *,
        token: str,
        email: str,
        verification_code: str,
        values: dict,
        ip_address: str = "",
        user_agent: str = "",
    ) -> SignatureRequest:
        signature_request, signer = SignatureRequestService.get_signer_access(
            token=token,
            email=email,
            verification_code=verification_code,
        )
        SignatureRequestService._ensure_signer_order(signature_request, signer)

        signer_fields = SignatureField.query.filter_by(
            request_id=signature_request.id,
            signer_id=signer.id,
        ).all()

        for field in signer_fields:
            key = f"field_{field.id}"
            incoming = str(values.get(key, "") or "").strip()
            if field.required and not incoming:
                if field.field_type == "date":
                    incoming = utcnow().date().isoformat()
                elif field.field_type in {"name", "signature", "initials"}:
                    incoming = signer.name
            field.value = incoming

        signer.status = "signed"
        signer.signed_at = utcnow()

        pending = (
            SignatureSigner.query.filter(
                SignatureSigner.request_id == signature_request.id,
                SignatureSigner.status != "signed",
            )
            .order_by(SignatureSigner.signer_order.asc(), SignatureSigner.id.asc())
            .first()
        )
        if pending:
            signature_request.current_order = int(pending.signer_order or signature_request.current_order)
        else:
            signature_request.current_order = int(signature_request.current_order or 1)

        db.session.commit()
        SignatureRequestService._record_event(
            signature_request,
            "signer_signed",
            signer_id=signer.id,
            ip_address=ip_address,
            user_agent=user_agent,
        )

        SignatureRequestService._finalize_if_complete(signature_request)
        return signature_request

    @staticmethod
    def _finalize_if_complete(signature_request: SignatureRequest) -> None:
        pending_count = SignatureSigner.query.filter(
            SignatureSigner.request_id == signature_request.id,
            SignatureSigner.status != "signed",
        ).count()
        if pending_count > 0:
            return

        input_path = StorageService.absolute_path(signature_request.file)
        output_dir = (
            Path(current_app.config["OUTPUT_ROOT"])
            / str(signature_request.requester_id)
            / "signature_requests"
            / f"request_{signature_request.id}"
        )
        output_dir.mkdir(parents=True, exist_ok=True)
        output_path = output_dir / f"signed_request_{signature_request.id}.pdf"

        actions = []
        fields = SignatureField.query.filter_by(request_id=signature_request.id).all()
        for field in fields:
            if field.field_type in {"text", "name", "date", "signature", "initials"}:
                text_value = (field.value or "").strip()
                if not text_value:
                    text_value = field.label or field.field_type.title()
                actions.append(
                    {
                        "type": "text",
                        "page": max(1, int(field.page or 1)),
                        "text": text_value,
                        "x": float(field.x or 72),
                        "y": float(field.y or 72) + max(12.0, float(field.height or 24) * 0.8),
                        "font_size": 11,
                        "color": "#0E533D",
                    }
                )

        if actions:
            PDFService.apply_editor_actions(
                input_path=str(input_path),
                output_path=str(output_path),
                actions=actions,
            )
        else:
            output_path.write_bytes(input_path.read_bytes())

        output_file = StorageService.register_existing_file(
            absolute_path=output_path,
            user_id=signature_request.requester_id,
            kind="output",
            original_name=f"signed_{signature_request.file.original_name}",
            label="Signed request output",
        )

        signature_request.signed_file_id = output_file.id
        signature_request.status = "signed"
        signature_request.completed_at = utcnow()
        db.session.commit()
        SignatureRequestService._record_event(
            signature_request,
            "request_completed",
            details={"signed_file_id": output_file.id},
        )

    @staticmethod
    def admin_overview(limit: int = 100) -> list[SignatureRequest]:
        return (
            SignatureRequest.query.order_by(SignatureRequest.created_at.desc())
            .limit(max(1, limit))
            .all()
        )

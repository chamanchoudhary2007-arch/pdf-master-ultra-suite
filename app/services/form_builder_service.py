from __future__ import annotations

import re
import time
from pathlib import Path

from flask import current_app

from app.extensions import db
from app.models import FormSubmission, FormTemplate, FormTemplateField
from app.services.pdf_service import PDFService
from app.services.storage_service import StorageService


class FormBuilderService:
    ALLOWED_FIELD_TYPES = {"text", "checkbox", "radio", "dropdown", "date", "signature"}

    @staticmethod
    def auto_detect_fields(source_file_id: int, owner_id: int) -> list[dict]:
        # Fallback to heuristic detection from PDF text lines that look like labels.
        file_record = FormBuilderService._source_file_for_owner(source_file_id, owner_id)
        source_path = StorageService.absolute_path(file_record)

        try:
            from pypdf import PdfReader
        except Exception:
            return []

        reader = PdfReader(str(source_path))
        suggestions: list[dict] = []
        y = 720
        for page_index, page in enumerate(reader.pages[:3], start=1):
            text = page.extract_text() or ""
            lines = [line.strip() for line in text.splitlines() if line.strip()]
            for line in lines:
                if len(suggestions) >= 20:
                    break
                if line.endswith(":") or re.search(r"\b(name|date|address|email|phone|amount|sign)\b", line, flags=re.IGNORECASE):
                    suggestions.append(
                        {
                            "page": page_index,
                            "field_type": "text",
                            "name": re.sub(r"[^a-z0-9]+", "_", line.lower()).strip("_")[:60] or f"field_{len(suggestions)+1}",
                            "label": line[:120],
                            "placeholder": "",
                            "x": 72,
                            "y": y,
                            "width": 220,
                            "height": 24,
                            "required": False,
                            "options": [],
                        }
                    )
                    y -= 36
                    if y < 120:
                        y = 720
            if len(suggestions) >= 20:
                break
        return suggestions

    @staticmethod
    def _source_file_for_owner(source_file_id: int, owner_id: int):
        from app.models import ManagedFile

        row = ManagedFile.query.filter_by(id=source_file_id, user_id=owner_id, is_deleted=False).first()
        if not row:
            raise ValueError("Source file not found for template.")
        return row

    @staticmethod
    def list_templates(owner_id: int, limit: int = 50) -> list[FormTemplate]:
        return (
            FormTemplate.query.filter_by(owner_id=owner_id)
            .order_by(FormTemplate.created_at.desc())
            .limit(max(1, limit))
            .all()
        )

    @staticmethod
    def get_template_for_owner(template_id: int, owner_id: int) -> FormTemplate:
        row = FormTemplate.query.filter_by(id=template_id, owner_id=owner_id).first()
        if not row:
            raise ValueError("Form template not found.")
        return row

    @staticmethod
    def get_template_by_token(token: str) -> FormTemplate:
        row = FormTemplate.query.filter_by(share_token=(token or "").strip(), is_active=True).first()
        if not row:
            raise ValueError("Shared form template not found.")
        return row

    @staticmethod
    def create_template(
        *,
        owner_id: int,
        source_file_id: int,
        name: str,
        description: str,
        fields: list[dict],
    ) -> FormTemplate:
        FormBuilderService._source_file_for_owner(source_file_id, owner_id)

        template = FormTemplate(
            owner_id=owner_id,
            source_file_id=source_file_id,
            name=(name or "Untitled Form").strip()[:180],
            description=(description or "").strip(),
            is_active=True,
            metadata_json={"field_count": len(fields or [])},
        )
        db.session.add(template)
        db.session.flush()

        FormBuilderService._replace_fields(template.id, fields)
        db.session.commit()
        return template

    @staticmethod
    def update_template(
        *,
        template_id: int,
        owner_id: int,
        name: str,
        description: str,
        fields: list[dict],
    ) -> FormTemplate:
        template = FormBuilderService.get_template_for_owner(template_id, owner_id)
        template.name = (name or template.name).strip()[:180]
        template.description = (description or "").strip()
        template.metadata_json = {
            **(template.metadata_json or {}),
            "field_count": len(fields or []),
        }
        FormBuilderService._replace_fields(template.id, fields)
        db.session.commit()
        return template

    @staticmethod
    def _replace_fields(template_id: int, fields: list[dict]) -> None:
        FormTemplateField.query.filter_by(template_id=template_id).delete(synchronize_session=False)

        for field in fields or []:
            field_type = (field.get("field_type") or "text").strip().lower()
            if field_type not in FormBuilderService.ALLOWED_FIELD_TYPES:
                field_type = "text"
            options = field.get("options")
            if isinstance(options, str):
                options = [opt.strip() for opt in options.split(",") if opt.strip()]
            row = FormTemplateField(
                template_id=template_id,
                page=max(1, int(field.get("page") or 1)),
                field_type=field_type,
                name=(field.get("name") or "").strip()[:120],
                label=(field.get("label") or "").strip()[:120],
                placeholder=(field.get("placeholder") or "").strip()[:255],
                options_json=list(options or []),
                x=float(field.get("x") or 72),
                y=float(field.get("y") or 72),
                width=max(40.0, float(field.get("width") or 180)),
                height=max(16.0, float(field.get("height") or 24)),
                required=bool(field.get("required", False)),
                default_value=(field.get("default_value") or "").strip()[:255],
            )
            db.session.add(row)

    @staticmethod
    def submit_form(
        *,
        template_token: str,
        values: dict,
        submitted_by_user_id: int | None = None,
    ) -> FormSubmission:
        template = FormBuilderService.get_template_by_token(template_token)
        source_path = StorageService.absolute_path(template.source_file)

        fields = (
            FormTemplateField.query.filter_by(template_id=template.id)
            .order_by(FormTemplateField.page.asc(), FormTemplateField.id.asc())
            .all()
        )
        actions = []
        stored_values: dict[str, str] = {}
        for field in fields:
            key = field.name or f"field_{field.id}"
            incoming = str(values.get(key, "") or "").strip()
            if not incoming:
                incoming = field.default_value or ""
            if field.required and not incoming:
                if field.field_type == "date":
                    from datetime import date

                    incoming = date.today().isoformat()
                elif field.field_type == "checkbox":
                    incoming = "false"
                else:
                    raise ValueError(f"{field.label or key} is required.")

            if field.field_type == "checkbox":
                truthy = incoming.lower() in {"1", "true", "yes", "on", "checked"}
                text_value = "X" if truthy else ""
            else:
                text_value = incoming

            if text_value:
                actions.append(
                    {
                        "type": "text",
                        "page": int(field.page),
                        "text": text_value,
                        "x": float(field.x),
                        "y": float(field.y) + max(11.0, float(field.height) * 0.75),
                        "font_size": 10,
                        "color": "#0A4D3A",
                    }
                )
            stored_values[key] = incoming

        output_dir = (
            Path(current_app.config["OUTPUT_ROOT"])
            / str(template.owner_id)
            / "forms"
            / f"template_{template.id}"
        )
        output_dir.mkdir(parents=True, exist_ok=True)
        output_path = output_dir / f"filled_form_{template.id}_{int(time.time())}.pdf"

        if actions:
            PDFService.apply_editor_actions(
                input_path=str(source_path),
                output_path=str(output_path),
                actions=actions,
            )
        else:
            output_path.write_bytes(source_path.read_bytes())

        output_file = StorageService.register_existing_file(
            absolute_path=output_path,
            user_id=template.owner_id,
            kind="output",
            original_name=f"filled_{template.name}.pdf",
            label="Filled form output",
        )

        submission = FormSubmission(
            template_id=template.id,
            submitted_by_user_id=submitted_by_user_id,
            output_file_id=output_file.id,
            status="submitted",
            values_json=stored_values,
        )
        db.session.add(submission)
        db.session.commit()
        return submission

    @staticmethod
    def list_submissions_for_owner(owner_id: int, limit: int = 50) -> list[FormSubmission]:
        return (
            FormSubmission.query.join(FormTemplate, FormTemplate.id == FormSubmission.template_id)
            .filter(FormTemplate.owner_id == owner_id)
            .order_by(FormSubmission.created_at.desc())
            .limit(max(1, limit))
            .all()
        )

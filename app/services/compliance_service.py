from __future__ import annotations

import json
from pathlib import Path

from flask import current_app

from app.extensions import db
from app.models import ComplianceReport, ManagedFile
from app.services.pdf_service import PDFService
from app.services.storage_service import StorageService


class ComplianceService:
    @staticmethod
    def _collect_issues(pdf_path: Path) -> tuple[list[str], dict]:
        try:
            from pypdf import PdfReader
        except Exception as exc:
            raise ValueError("pypdf is required for compliance checks.") from exc

        reader = PdfReader(str(pdf_path))
        metadata = reader.metadata or {}
        issues: list[str] = []

        if reader.is_encrypted:
            issues.append("Document is encrypted. Archive workflows typically require unencrypted PDF.")

        title = (metadata.get("/Title") or "").strip() if metadata else ""
        author = (metadata.get("/Author") or "").strip() if metadata else ""
        if not title:
            issues.append("Missing title metadata.")
        if not author:
            issues.append("Missing author metadata.")

        page_sizes = set()
        for page in reader.pages:
            width = round(float(page.mediabox.width), 1)
            height = round(float(page.mediabox.height), 1)
            page_sizes.add((width, height))
        if len(page_sizes) > 2:
            issues.append("Mixed page sizes detected. Consider normalizing for long-term archival.")

        has_xmp = bool(getattr(reader, "xmp_metadata", None))
        if not has_xmp:
            issues.append("XMP metadata is missing.")

        report = {
            "page_count": len(reader.pages),
            "metadata": {
                "title": title,
                "author": author,
                "subject": (metadata.get("/Subject") or "").strip() if metadata else "",
                "keywords": (metadata.get("/Keywords") or "").strip() if metadata else "",
                "xmp_present": has_xmp,
            },
            "page_sizes": [{"width": w, "height": h} for w, h in sorted(page_sizes)],
        }
        return issues, report

    @staticmethod
    def run_report(
        *,
        user_id: int,
        file_id: int,
        mode: str,
        metadata_overrides: dict | None = None,
    ) -> ComplianceReport:
        file_record = ManagedFile.query.filter_by(id=file_id, user_id=user_id, is_deleted=False).first()
        if not file_record:
            raise ValueError("File not found.")
        source_path = StorageService.absolute_path(file_record)
        if source_path.suffix.lower() != ".pdf":
            raise ValueError("Compliance tools support PDF files only.")

        issues, report = ComplianceService._collect_issues(source_path)

        report_dir = (
            Path(current_app.config["OUTPUT_ROOT"])
            / str(user_id)
            / "compliance"
            / f"file_{file_record.id}"
        )
        report_dir.mkdir(parents=True, exist_ok=True)

        mode_key = (mode or "check").strip().lower()
        output_file = None

        if mode_key in {"pdfa", "archive", "print_safe"}:
            processed_path = report_dir / f"{source_path.stem}_{mode_key}.pdf"
            if mode_key == "print_safe":
                PDFService.flatten_pdf(str(source_path), str(processed_path), grayscale=False)
            else:
                repaired = report_dir / f"{source_path.stem}_repaired.pdf"
                PDFService.repair_pdf(str(source_path), str(repaired))
                metadata = {
                    "title": source_path.stem,
                    "author": "PDFMaster Ultra Suite",
                    "subject": "Archive Ready PDF",
                    "keywords": "pdfa,archive,long-term",
                }
                if metadata_overrides:
                    metadata.update({k: v for k, v in metadata_overrides.items() if str(v).strip()})
                PDFService.update_metadata(str(repaired), str(processed_path), metadata)
            output_file = StorageService.register_existing_file(
                absolute_path=processed_path,
                user_id=user_id,
                kind="output",
                original_name=processed_path.name,
                label="Compliance processed PDF",
            )

        report_json = {
            "mode": mode_key,
            "issues": issues,
            "issue_count": len(issues),
            "checks": report,
            "notes": (
                "This is a practical compliance scan and not a certified legal opinion."
            ),
        }

        report_txt_path = report_dir / "compliance_report.txt"
        report_txt_path.write_text(
            "PDF Compliance Report\n\n"
            f"File: {file_record.original_name}\n"
            f"Mode: {mode_key}\n"
            f"Issue count: {len(issues)}\n\n"
            + ("\n".join(f"- {item}" for item in issues) if issues else "No major issues detected."),
            encoding="utf-8",
        )
        report_json_path = report_dir / "compliance_report.json"
        report_json_path.write_text(json.dumps(report_json, indent=2), encoding="utf-8")

        bundle_path = report_dir / "compliance_bundle.zip"
        from zipfile import ZIP_DEFLATED, ZipFile

        with ZipFile(bundle_path, "w", compression=ZIP_DEFLATED) as archive:
            archive.write(report_txt_path, arcname=report_txt_path.name)
            archive.write(report_json_path, arcname=report_json_path.name)
            if output_file:
                archive.write(StorageService.absolute_path(output_file), arcname=output_file.original_name)

        archive_file = StorageService.register_existing_file(
            absolute_path=bundle_path,
            user_id=user_id,
            kind="output",
            original_name=f"compliance_{file_record.id}.zip",
            label="Compliance report bundle",
        )

        row = ComplianceReport(
            user_id=user_id,
            file_id=file_record.id,
            output_file_id=archive_file.id,
            report_type=mode_key,
            status="completed",
            issue_count=len(issues),
            report_json=report_json,
        )
        db.session.add(row)
        db.session.commit()
        return row

    @staticmethod
    def list_reports(user_id: int, limit: int = 50) -> list[ComplianceReport]:
        return (
            ComplianceReport.query.filter_by(user_id=user_id)
            .order_by(ComplianceReport.created_at.desc())
            .limit(max(1, limit))
            .all()
        )

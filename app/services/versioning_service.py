from __future__ import annotations

import hashlib
import json
from pathlib import Path

from flask import current_app

from app.extensions import db
from app.models import (
    DocumentComparison,
    DocumentVersion,
    DocumentVersionGroup,
    ManagedFile,
)
from app.services.ai_service import AIDocumentService
from app.services.pdf_service import PDFService
from app.services.storage_service import StorageService


class VersioningService:
    @staticmethod
    def list_groups(user_id: int, limit: int = 50) -> list[DocumentVersionGroup]:
        return (
            DocumentVersionGroup.query.filter_by(user_id=user_id)
            .order_by(DocumentVersionGroup.updated_at.desc())
            .limit(max(1, limit))
            .all()
        )

    @staticmethod
    def create_group(*, user_id: int, name: str, description: str = "") -> DocumentVersionGroup:
        group = DocumentVersionGroup(
            user_id=user_id,
            name=(name or "Document Set").strip()[:180],
            description=(description or "").strip(),
            metadata_json={},
        )
        db.session.add(group)
        db.session.commit()
        return group

    @staticmethod
    def add_version(
        *,
        group_id: int,
        user_id: int,
        file_id: int,
        version_label: str,
        notes: str = "",
    ) -> DocumentVersion:
        group = DocumentVersionGroup.query.filter_by(id=group_id, user_id=user_id).first()
        if not group:
            raise ValueError("Version group not found.")
        file_record = ManagedFile.query.filter_by(id=file_id, user_id=user_id, is_deleted=False).first()
        if not file_record:
            raise ValueError("File not found.")

        version = DocumentVersion(
            group_id=group.id,
            file_id=file_record.id,
            version_label=(version_label or f"v{group.versions.count() + 1}").strip()[:120],
            notes=(notes or "").strip(),
        )
        db.session.add(version)
        db.session.commit()
        return version

    @staticmethod
    def list_comparisons(user_id: int, limit: int = 50) -> list[DocumentComparison]:
        return (
            DocumentComparison.query.filter_by(user_id=user_id)
            .order_by(DocumentComparison.created_at.desc())
            .limit(max(1, limit))
            .all()
        )

    @staticmethod
    def _sha256(path: Path) -> str:
        hasher = hashlib.sha256()
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                hasher.update(chunk)
        return hasher.hexdigest()

    @staticmethod
    def compare_versions(
        *,
        user_id: int,
        version_a_id: int,
        version_b_id: int,
    ) -> DocumentComparison:
        version_a = DocumentVersion.query.get(version_a_id)
        version_b = DocumentVersion.query.get(version_b_id)
        if not version_a or not version_b:
            raise ValueError("Select valid versions to compare.")

        if version_a.group.user_id != user_id or version_b.group.user_id != user_id:
            raise ValueError("You do not have access to these versions.")

        file_a_path = StorageService.absolute_path(version_a.file)
        file_b_path = StorageService.absolute_path(version_b.file)
        try:
            from pypdf import PdfReader

            page_count_a = len(PdfReader(str(file_a_path)).pages)
            page_count_b = len(PdfReader(str(file_b_path)).pages)
        except Exception:
            page_count_a = 0
            page_count_b = 0

        report_dir = (
            Path(current_app.config["OUTPUT_ROOT"])
            / str(user_id)
            / "version_compare"
            / f"{version_a.id}_vs_{version_b.id}"
        )
        report_dir.mkdir(parents=True, exist_ok=True)

        diff_path = report_dir / "text_diff.txt"
        PDFService.compare_pdfs(str(file_a_path), str(file_b_path), str(diff_path))
        diff_text = diff_path.read_text(encoding="utf-8", errors="ignore")
        summary = AIDocumentService.summarize_text(diff_text, max_sentences=6) if diff_text else "No textual differences found."

        report_json = {
            "version_a": {
                "id": version_a.id,
                "label": version_a.version_label,
                "file": version_a.file.original_name,
                "size_bytes": file_a_path.stat().st_size,
                "sha256": VersioningService._sha256(file_a_path),
            },
            "version_b": {
                "id": version_b.id,
                "label": version_b.version_label,
                "file": version_b.file.original_name,
                "size_bytes": file_b_path.stat().st_size,
                "sha256": VersioningService._sha256(file_b_path),
            },
            "page_level": {
                "a_pages": int(page_count_a),
                "b_pages": int(page_count_b),
            },
            "summary": summary,
        }

        report_json_path = report_dir / "compare_report.json"
        report_json_path.write_text(json.dumps(report_json, indent=2), encoding="utf-8")
        report_txt_path = report_dir / "compare_summary.txt"
        report_txt_path.write_text(
            "Document Compare Summary\n\n"
            f"A: {version_a.version_label} ({version_a.file.original_name})\n"
            f"B: {version_b.version_label} ({version_b.file.original_name})\n\n"
            f"{summary}\n\n"
            "See text_diff.txt for full unified diff.",
            encoding="utf-8",
        )

        bundle_path = report_dir / "compare_bundle.zip"
        from zipfile import ZIP_DEFLATED, ZipFile

        with ZipFile(bundle_path, "w", compression=ZIP_DEFLATED) as archive:
            archive.write(diff_path, arcname=diff_path.name)
            archive.write(report_json_path, arcname=report_json_path.name)
            archive.write(report_txt_path, arcname=report_txt_path.name)

        report_file = StorageService.register_existing_file(
            absolute_path=bundle_path,
            user_id=user_id,
            kind="output",
            original_name=f"compare_{version_a.id}_{version_b.id}.zip",
            label="Document compare report",
        )

        row = DocumentComparison(
            user_id=user_id,
            version_a_id=version_a.id,
            version_b_id=version_b.id,
            report_file_id=report_file.id,
            summary=summary,
            diff_json=report_json,
        )
        db.session.add(row)
        db.session.commit()
        return row

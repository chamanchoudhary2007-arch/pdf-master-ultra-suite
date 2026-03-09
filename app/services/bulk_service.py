from __future__ import annotations

import shutil
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Lock
from zipfile import ZIP_DEFLATED, ZipFile

from flask import current_app

from app.extensions import db
from app.models import BulkBatch, BulkBatchItem, ManagedFile, utcnow
from app.services.conversion_service import ConversionService
from app.services.ocr_service import OCRService
from app.services.pdf_service import PDFService
from app.services.storage_service import StorageService


class BulkService:
    SUPPORTED_TOOLS = {
        "compress",
        "watermark",
        "convert",
        "rename",
        "merge_by_folder",
        "ocr_batch",
    }

    _executor: ThreadPoolExecutor | None = None
    _lock = Lock()

    @classmethod
    def _get_executor(cls) -> ThreadPoolExecutor:
        with cls._lock:
            if cls._executor is None:
                cls._executor = ThreadPoolExecutor(
                    max_workers=2,
                    thread_name_prefix="pdfmaster-bulk",
                )
            return cls._executor

    @staticmethod
    def _output_root(user_id: int, batch_id: int) -> Path:
        root = Path(current_app.config["OUTPUT_ROOT"]) / str(user_id) / f"bulk_{batch_id}"
        root.mkdir(parents=True, exist_ok=True)
        return root

    @staticmethod
    def submit_batch(
        *,
        user_id: int,
        tool_key: str,
        files: list[ManagedFile],
        options: dict | None = None,
        name: str = "",
    ) -> BulkBatch:
        normalized_tool = (tool_key or "").strip().lower()
        if normalized_tool not in BulkService.SUPPORTED_TOOLS:
            raise ValueError("Unsupported bulk action selected.")
        if not files:
            raise ValueError("Upload at least one file for bulk processing.")

        batch = BulkBatch(
            user_id=user_id,
            name=(name or f"Bulk {normalized_tool}").strip()[:160],
            tool_key=normalized_tool,
            status="pending",
            total_files=len(files),
            processed_files=0,
            failed_files=0,
            options_json=options or {},
            result_json={},
        )
        db.session.add(batch)
        db.session.flush()

        for index, file_record in enumerate(files, start=1):
            item = BulkBatchItem(
                batch_id=batch.id,
                input_file_id=file_record.id,
                status="pending",
                sequence_index=index,
                log_message="Queued",
            )
            db.session.add(item)

        db.session.commit()
        app = current_app._get_current_object()
        BulkService._get_executor().submit(BulkService._run_batch, app, batch.id)
        return batch

    @staticmethod
    def list_batches_for_user(user_id: int, limit: int = 30) -> list[BulkBatch]:
        return (
            BulkBatch.query.filter_by(user_id=user_id)
            .order_by(BulkBatch.created_at.desc())
            .limit(max(1, limit))
            .all()
        )

    @staticmethod
    def get_batch_for_user(batch_id: int, user_id: int) -> BulkBatch:
        row = BulkBatch.query.filter_by(id=batch_id, user_id=user_id).first()
        if not row:
            raise ValueError("Bulk batch not found.")
        return row

    @staticmethod
    def _run_batch(app, batch_id: int) -> None:
        with app.app_context():
            batch = db.session.get(BulkBatch, batch_id)
            if not batch:
                return

            batch.status = "processing"
            batch.started_at = utcnow()
            db.session.commit()

            options = batch.options_json or {}
            user_id = int(batch.user_id)
            output_root = BulkService._output_root(user_id, batch.id)
            item_outputs: list[ManagedFile] = []

            try:
                items = (
                    BulkBatchItem.query.filter_by(batch_id=batch.id)
                    .order_by(BulkBatchItem.sequence_index.asc())
                    .all()
                )
                if batch.tool_key == "merge_by_folder":
                    merged_output = BulkService._process_merge_by_folder(
                        batch=batch,
                        items=items,
                        output_root=output_root,
                    )
                    if merged_output:
                        item_outputs.append(merged_output)
                else:
                    for item in items:
                        out = BulkService._process_item(
                            batch=batch,
                            item=item,
                            options=options,
                            output_root=output_root,
                        )
                        if out:
                            item_outputs.append(out)

                archive = BulkService._bundle_outputs(
                    user_id=user_id,
                    batch=batch,
                    output_root=output_root,
                    outputs=item_outputs,
                )
                if archive:
                    batch.output_archive_file_id = archive.id

                batch.status = "completed" if batch.failed_files == 0 else "completed"
                batch.finished_at = utcnow()
                batch.result_json = {
                    "processed": int(batch.processed_files),
                    "failed": int(batch.failed_files),
                    "tool": batch.tool_key,
                }
                db.session.commit()
            except Exception as exc:
                current_app.logger.exception("Bulk batch failed. batch_id=%s", batch.id)
                batch.status = "failed"
                batch.finished_at = utcnow()
                batch.result_json = {"error": str(exc)}
                db.session.commit()

    @staticmethod
    def _process_merge_by_folder(
        *,
        batch: BulkBatch,
        items: list[BulkBatchItem],
        output_root: Path,
    ) -> ManagedFile | None:
        pdf_paths: list[str] = []
        for item in items:
            item.status = "processing"
            item.started_at = utcnow()
            db.session.commit()
            if not item.input_file:
                item.status = "failed"
                item.error_message = "Input file missing"
                item.finished_at = utcnow()
                batch.failed_files += 1
                db.session.commit()
                continue
            source = StorageService.absolute_path(item.input_file)
            if source.suffix.lower() != ".pdf":
                item.status = "failed"
                item.error_message = "Only PDF files are allowed for merge-by-folder"
                item.finished_at = utcnow()
                batch.failed_files += 1
                db.session.commit()
                continue
            pdf_paths.append(str(source))

        if len(pdf_paths) < 2:
            raise ValueError("Merge-by-folder requires at least two PDF files.")

        output_path = output_root / "merged_batch.pdf"
        PDFService.merge_pdfs(pdf_paths, str(output_path))
        output_file = StorageService.register_existing_file(
            absolute_path=output_path,
            user_id=batch.user_id,
            kind="output",
            original_name="bulk_merged.pdf",
            label="Bulk merge output",
        )

        for item in items:
            if item.status == "failed":
                continue
            item.status = "completed"
            item.log_message = "Merged into final output"
            item.output_file_id = output_file.id
            item.finished_at = utcnow()
            batch.processed_files += 1
        db.session.commit()
        return output_file

    @staticmethod
    def _process_item(
        *,
        batch: BulkBatch,
        item: BulkBatchItem,
        options: dict,
        output_root: Path,
    ) -> ManagedFile | None:
        item.status = "processing"
        item.started_at = utcnow()
        db.session.commit()

        if not item.input_file:
            item.status = "failed"
            item.error_message = "Input file missing"
            item.finished_at = utcnow()
            batch.failed_files += 1
            db.session.commit()
            return None

        source = StorageService.absolute_path(item.input_file)
        item_dir = output_root / f"item_{item.sequence_index:03d}"
        item_dir.mkdir(parents=True, exist_ok=True)

        try:
            output_name = BulkService._execute_tool(
                tool_key=batch.tool_key,
                source_path=source,
                item_dir=item_dir,
                options=options,
            )
            output_file = StorageService.register_existing_file(
                absolute_path=output_name,
                user_id=batch.user_id,
                kind="output",
                original_name=Path(output_name).name,
                label=f"Bulk {batch.tool_key} output",
            )
            item.output_file_id = output_file.id
            item.status = "completed"
            item.log_message = "Processed successfully"
            item.finished_at = utcnow()
            batch.processed_files += 1
            db.session.commit()
            return output_file
        except Exception as exc:
            item.status = "failed"
            item.error_message = str(exc)
            item.log_message = "Processing failed"
            item.finished_at = utcnow()
            batch.failed_files += 1
            db.session.commit()
            return None

    @staticmethod
    def _execute_tool(*, tool_key: str, source_path: Path, item_dir: Path, options: dict) -> Path:
        stem = source_path.stem
        suffix = source_path.suffix.lower()

        if tool_key == "compress":
            if suffix != ".pdf":
                raise ValueError("Compress supports PDF files only.")
            output = item_dir / f"{stem}_compressed.pdf"
            level = (options.get("compress_level") or "balanced").strip().lower()
            PDFService.compress_pdf(str(source_path), str(output), level=level)
            return output

        if tool_key == "watermark":
            if suffix != ".pdf":
                raise ValueError("Watermark supports PDF files only.")
            output = item_dir / f"{stem}_watermark.pdf"
            text = (options.get("watermark_text") or "CONFIDENTIAL").strip() or "CONFIDENTIAL"
            position = (options.get("watermark_position") or "diagonal").strip().lower()
            PDFService.add_text_watermark(
                str(source_path),
                str(output),
                text=text,
                position=position,
                opacity=0.22,
            )
            return output

        if tool_key == "convert":
            target = (options.get("target_format") or "txt").strip().lower()
            if suffix == ".pdf" and target in {"docx", "pptx", "xlsx", "txt", "html", "rtf"}:
                if target == "docx":
                    output = item_dir / f"{stem}.docx"
                    ConversionService.pdf_to_word(str(source_path), str(output))
                    return output
                if target == "pptx":
                    output = item_dir / f"{stem}.pptx"
                    ConversionService.pdf_to_powerpoint(str(source_path), str(output), str(item_dir / "slides"))
                    return output
                if target == "xlsx":
                    output = item_dir / f"{stem}.xlsx"
                    ConversionService.pdf_to_excel(str(source_path), str(output))
                    return output
                if target == "html":
                    output = item_dir / f"{stem}.html"
                    ConversionService.pdf_to_html(str(source_path), str(output))
                    return output
                if target == "rtf":
                    output = item_dir / f"{stem}.rtf"
                    ConversionService.pdf_to_rtf(str(source_path), str(output))
                    return output
                output = item_dir / f"{stem}.txt"
                PDFService.pdf_to_text(str(source_path), str(output))
                return output

            if suffix in {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tif", ".tiff"} and target == "pdf":
                output = item_dir / f"{stem}.pdf"
                PDFService.images_to_pdf([str(source_path)], str(output))
                return output
            raise ValueError("Unsupported conversion target for this file type.")

        if tool_key == "rename":
            prefix = (options.get("rename_prefix") or "renamed").strip()
            output = item_dir / f"{prefix}_{stem}{suffix}"
            shutil.copy2(source_path, output)
            return output

        if tool_key == "ocr_batch":
            lang = (options.get("ocr_lang") or current_app.config.get("OCR_LANG") or "eng").strip()
            output_format = (options.get("ocr_output") or "txt").strip().lower()
            if suffix == ".pdf":
                output_pdf = item_dir / f"{stem}_searchable.pdf"
                output_txt = item_dir / f"{stem}_ocr.txt"
                OCRService.ocr_pdf_to_searchable(
                    input_pdf_path=str(source_path),
                    output_pdf_path=str(output_pdf),
                    output_text_path=str(output_txt),
                    lang=lang,
                )
                if output_format == "searchable_pdf":
                    return output_pdf
                return output_txt
            output_txt = item_dir / f"{stem}_ocr.txt"
            OCRService.ocr_image_paths([str(source_path)], str(output_txt), lang=lang)
            return output_txt

        raise ValueError("Unsupported bulk tool")

    @staticmethod
    def _bundle_outputs(
        *,
        user_id: int,
        batch: BulkBatch,
        output_root: Path,
        outputs: list[ManagedFile],
    ) -> ManagedFile | None:
        unique_paths: list[Path] = []
        seen = set()
        for row in outputs:
            absolute = StorageService.absolute_path(row)
            key = str(absolute.resolve())
            if key in seen or not absolute.exists():
                continue
            seen.add(key)
            unique_paths.append(absolute)

        if not unique_paths:
            return None

        archive_path = output_root / "bulk_results.zip"
        with ZipFile(archive_path, "w", compression=ZIP_DEFLATED) as archive:
            for file_path in unique_paths:
                archive.write(file_path, arcname=file_path.name)

        return StorageService.register_existing_file(
            absolute_path=archive_path,
            user_id=user_id,
            kind="output",
            original_name=f"bulk_batch_{batch.id}_results.zip",
            label="Bulk batch archive",
        )

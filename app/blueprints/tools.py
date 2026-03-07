from __future__ import annotations

import json
import shutil
from datetime import datetime, timezone
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

from flask import Blueprint, Response, current_app, flash, redirect, render_template, request, send_file, url_for
from flask_login import current_user, login_required

from app.models import ManagedFile, ShareLink, ToolCatalog
from app.services import (
    AIDocumentService,
    CatalogService,
    ConversionService,
    EducationService,
    GovernmentService,
    ImageService,
    JobService,
    OCRService,
    PDFService,
    PricingService,
    ScannerService,
    ShareService,
    SignatureService,
    StorageService,
    SubscriptionService,
    TemplateService,
)

tools_bp = Blueprint("tools", __name__)


def _save_uploads(field_name: str, multiple: bool = False, kind: str = "upload") -> list[ManagedFile]:
    uploads = request.files.getlist(field_name) if multiple else [request.files.get(field_name)]
    saved_files = []
    for upload in uploads:
        if upload and upload.filename:
            saved_files.append(StorageService.save_uploaded_file(upload, current_user.id, kind=kind))
    if not saved_files:
        raise ValueError("Please upload at least one file.")
    return saved_files


def _job_dir(job_id: int, user_id: int | None = None) -> Path:
    base = Path(current_app.config["OUTPUT_ROOT"]) / str(user_id or current_user.id) / f"job_{job_id}"
    base.mkdir(parents=True, exist_ok=True)
    return base


def _bundle_outputs(paths: list[str], zip_path: Path) -> str:
    with ZipFile(zip_path, "w", compression=ZIP_DEFLATED) as archive:
        for file_path in paths:
            archive.write(file_path, arcname=Path(file_path).name)
    return str(zip_path)


def _register_output(user_id: int, absolute_path: str | Path, original_name: str, label: str = "") -> ManagedFile:
    return StorageService.register_existing_file(
        absolute_path=absolute_path,
        user_id=user_id,
        kind="output",
        original_name=original_name,
        label=label,
    )


def _extract_pdf_text(input_pdf_path: str, output_dir: Path) -> str:
    text_path = output_dir / "document_text.txt"
    PDFService.pdf_to_text(input_pdf_path, str(text_path))
    extracted = text_path.read_text(encoding="utf-8", errors="ignore").strip()
    if extracted:
        return extracted

    ocr_text_path = output_dir / "document_text_ocr.txt"
    ocr_frame_dir = output_dir / "ocr_frames"
    try:
        image_paths = PDFService.pdf_to_images(
            input_pdf_path,
            str(ocr_frame_dir),
            image_format="png",
        )
        OCRService.ocr_image_paths(image_paths, str(ocr_text_path), lang="eng")
        ocr_text = ocr_text_path.read_text(encoding="utf-8", errors="ignore").strip()
        if ocr_text:
            return ocr_text
    except Exception:
        pass

    raise ValueError(
        "No readable text found in this document. Upload a text-based PDF or run OCR first."
    )


def _extract_ai_input_text(input_name: str, input_path: str, output_dir: Path) -> str:
    extension = Path(input_name or "").suffix.lower()
    if extension in {".txt", ".md"}:
        text = Path(input_path).read_text(encoding="utf-8", errors="ignore").strip()
        if not text:
            raise ValueError("Uploaded text file is empty.")
        return text
    if extension == ".pdf":
        return _extract_pdf_text(input_path, output_dir)
    raise ValueError("Unsupported file type. Upload .pdf, .txt, or .md file.")


def _write_text_file(path: Path, content: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


def _is_checked(field_name: str) -> bool:
    return (request.form.get(field_name, "") or "").lower() in {"1", "true", "on", "yes"}


def _parse_int(
    raw_value: str | None,
    *,
    field_name: str,
    default: int,
    minimum: int | None = None,
    maximum: int | None = None,
) -> int:
    value_str = (raw_value or "").strip()
    if not value_str:
        value = default
    else:
        try:
            value = int(value_str)
        except ValueError as exc:
            raise ValueError(f"Invalid value for {field_name}.") from exc
    if minimum is not None:
        value = max(minimum, value)
    if maximum is not None:
        value = min(maximum, value)
    return value


def _parse_float(
    raw_value: str | None,
    *,
    field_name: str,
    default: float,
    minimum: float | None = None,
    maximum: float | None = None,
) -> float:
    value_str = (raw_value or "").strip()
    if not value_str:
        value = default
    else:
        try:
            value = float(value_str)
        except ValueError as exc:
            raise ValueError(f"Invalid value for {field_name}.") from exc
    if minimum is not None:
        value = max(minimum, value)
    if maximum is not None:
        value = min(maximum, value)
    return value


def _parse_bookmark_payload(raw_value: str) -> list[tuple[str, int]]:
    bookmarks: list[tuple[str, int]] = []
    for index, line in enumerate((raw_value or "").splitlines(), start=1):
        chunk = line.strip()
        if not chunk:
            continue
        if "|" in chunk:
            title, page_str = chunk.split("|", 1)
            try:
                page_number = int(page_str.strip())
            except ValueError as exc:
                raise ValueError(f"Invalid bookmark page number in line {index}.") from exc
            bookmarks.append((title.strip(), page_number))
            continue
        bookmarks.append((chunk, index))
    return bookmarks


def _friendly_tool_error(exc: Exception) -> str:
    message = (str(exc) or "").strip()
    normalized = message.lower()
    if not message:
        return "Processing failed. Please try again with a valid file."
    if "no module named" in normalized:
        return "This tool dependency is missing on the server. Please contact admin."
    if "unsupported file type" in normalized:
        return "Unsupported file type. Please upload an allowed file format."
    if "suspicious upload rejected" in normalized:
        return "Upload rejected for security reasons. Please use a clean original file."
    if "file too large" in normalized:
        return message
    if "invalid pdf" in normalized or "malformed pdf" in normalized or "eof marker" in normalized:
        return "PDF file looks corrupted or invalid. Please upload another PDF."
    if "invalid image" in normalized or "cannot identify image file" in normalized:
        return "Image file is invalid or corrupted. Please upload another image."
    return message if len(message) < 180 else "Processing failed. Please try a different file or settings."


@tools_bp.route("/")
@login_required
def catalog():
    return redirect(url_for("main.dashboard"))


@tools_bp.route("/<tool_key>")
@login_required
def tool_detail(tool_key: str):
    tool = CatalogService.get_tool(tool_key, enabled_only=not current_user.is_admin)
    is_premium_user = SubscriptionService.is_user_premium(current_user)
    premium_blocked = tool.is_subscription_only and not is_premium_user
    active_subscription = SubscriptionService.active_subscription_for_user(current_user.id)
    output_files = (
        ManagedFile.query.filter_by(user_id=current_user.id, storage_kind="output", is_deleted=False)
        .order_by(ManagedFile.created_at.desc())
        .limit(20)
        .all()
    )
    cloud_files = StorageService.list_cloud_files(current_user.id)
    share_links = (
        ShareLink.query.filter_by(user_id=current_user.id, is_active=True)
        .order_by(ShareLink.created_at.desc())
        .limit(20)
        .all()
    )
    return render_template(
        tool.template_name,
        tool=tool,
        output_files=output_files,
        cloud_files=cloud_files,
        share_links=share_links,
        is_premium_user=is_premium_user,
        premium_blocked=premium_blocked,
        active_subscription=active_subscription,
    )


@tools_bp.route("/file-share/create", methods=["POST"])
@login_required
def create_share_link():
    try:
        file_id = _parse_int(
            request.form.get("file_id"),
            field_name="file",
            default=0,
        )
        if file_id <= 0:
            raise ValueError("Please choose a valid file.")
        file_record = ManagedFile.query.filter_by(
            id=file_id,
            user_id=current_user.id,
            is_deleted=False,
        ).first()
        if not file_record:
            raise ValueError("Selected file was not found.")
        expiry_hours = _parse_int(
            request.form.get("expiry_hours"),
            field_name="expiry hours",
            default=24,
            minimum=1,
            maximum=168,
        )
        max_downloads = _parse_int(
            request.form.get("max_downloads"),
            field_name="max downloads",
            default=10,
            minimum=1,
            maximum=500,
        )
        share_link = ShareService.create_share_link(
            user_id=current_user.id,
            file_id=file_record.id,
            password=request.form.get("password", ""),
            expiry_hours=expiry_hours,
            max_downloads=max_downloads,
        )
    except Exception as exc:
        flash(str(exc), "danger")
    else:
        link = url_for("main.access_share_link", token=share_link.token, _external=True)
        flash(f"Share link generated: {link}", "success")
    return redirect(url_for("tools.tool_detail", tool_key="file_share"))


@tools_bp.route("/<tool_key>/run", methods=["POST"])
@login_required
def run_tool(tool_key: str):
    tool = CatalogService.get_tool(tool_key, enabled_only=not current_user.is_admin)
    handlers = {
        "merge_pdf": _handle_merge,
        "split_pdf": _handle_split,
        "rotate_pdf": _handle_rotate,
        "rotate_pages": _handle_rotate,
        "delete_pages": _handle_delete,
        "reorder_pdf": _handle_reorder,
        "organize_pdf": _handle_reorder,
        "watermark_pdf": _handle_watermark,
        "secure_pdf": _handle_security,
        "protect_pdf": _handle_security,
        "unlock_pdf": _handle_security,
        "pdf_to_images": _handle_pdf_to_images,
        "images_to_pdf": _handle_images_to_pdf,
        "pdf_to_jpg": _handle_convert_from_pdf,
        "pdf_to_png": _handle_convert_from_pdf,
        "pdf_to_text": _handle_pdf_to_text,
        "page_numbers": _handle_page_numbers,
        "header_footer": _handle_header_footer,
        "remove_metadata": _handle_remove_metadata,
        "edit_metadata_pdf": _handle_advanced_pdf,
        "document_scanner": _handle_scanner,
        "scan_to_pdf": _handle_scanner,
        "deskew_scan_pdf": _handle_scanner,
        "digital_signature": _handle_signature,
        "sign_pdf": _handle_signature,
        "fill_sign_pdf": _handle_signature,
        "compress_pdf": _handle_compress,
        "pdf_to_word": _handle_convert_from_pdf,
        "pdf_to_docx": _handle_convert_from_pdf,
        "pdf_to_ppt": _handle_convert_from_pdf,
        "pdf_to_pptx": _handle_convert_from_pdf,
        "pdf_to_excel": _handle_convert_from_pdf,
        "pdf_to_xlsx": _handle_convert_from_pdf,
        "pdf_to_html": _handle_convert_from_pdf,
        "pdf_to_rtf": _handle_convert_from_pdf,
        "word_to_pdf": _handle_convert_to_pdf,
        "doc_to_pdf": _handle_convert_to_pdf,
        "docx_to_pdf": _handle_convert_to_pdf,
        "powerpoint_to_pdf": _handle_convert_to_pdf,
        "ppt_to_pdf": _handle_convert_to_pdf,
        "pptx_to_pdf": _handle_convert_to_pdf,
        "excel_to_pdf": _handle_convert_to_pdf,
        "xls_to_pdf": _handle_convert_to_pdf,
        "xlsx_to_pdf": _handle_convert_to_pdf,
        "html_to_pdf": _handle_convert_to_pdf,
        "text_to_pdf": _handle_convert_to_pdf,
        "jpg_to_pdf": _handle_convert_to_pdf,
        "png_to_pdf": _handle_convert_to_pdf,
        "webp_to_pdf": _handle_convert_to_pdf,
        "heic_to_pdf": _handle_convert_to_pdf,
        "svg_to_pdf": _handle_convert_to_pdf,
        "tiff_to_pdf": _handle_convert_to_pdf,
        "image_utilities": _handle_image_utilities,
        "ocr_pdf": _handle_ocr,
        "extract_pages": _handle_advanced_pdf,
        "split_by_pages": _handle_advanced_pdf,
        "split_by_bookmarks": _handle_advanced_pdf,
        "split_in_half": _handle_advanced_pdf,
        "split_by_size": _handle_advanced_pdf,
        "split_by_text": _handle_advanced_pdf,
        "alternate_mix_pdf": _handle_advanced_pdf,
        "n_up_pdf": _handle_advanced_pdf,
        "resize_pdf": _handle_advanced_pdf,
        "deskew_pdf": _handle_advanced_pdf,
        "crop_pdf": _handle_advanced_pdf,
        "flatten_pdf": _handle_advanced_pdf,
        "grayscale_pdf": _handle_advanced_pdf,
        "extract_images_pdf": _handle_advanced_pdf,
        "remove_annotations_pdf": _handle_advanced_pdf,
        "repair_pdf": _handle_advanced_pdf,
        "redact_pdf": _handle_advanced_pdf,
        "compare_pdf": _handle_advanced_pdf,
        "bates_numbering": _handle_advanced_pdf,
        "create_bookmarks": _handle_advanced_pdf,
        "student_mode": _handle_student_mode,
        "pdf_editor": _handle_editor_suite,
        "edit_pdf": _handle_editor_suite,
        "create_forms": _handle_editor_suite,
        "study_pack_pro": _handle_study_pack_pro,
        "teacher_toolkit": _handle_teacher_toolkit,
        "government_office_suite": _handle_government_office_suite,
        "smart_pdf_pipeline": _handle_smart_pdf_pipeline,
        "office_mode": _handle_office_mode,
        "ai_document_tools": _handle_ai_tools,
        "translate_pdf": _handle_ai_tools,
        "document_templates": _handle_document_templates,
    }
    handler = handlers.get(tool_key)
    if not handler:
        flash("This tool is catalogued but not yet wired to a processing function.", "warning")
        return redirect(url_for("tools.tool_detail", tool_key=tool_key))
    try:
        SubscriptionService.require_tool_access(current_user, tool)
        result = handler(tool)
    except Exception as exc:
        current_app.logger.exception("Tool processing failed: tool=%s user=%s", tool_key, current_user.id)
        flash(_friendly_tool_error(exc), "danger")
    else:
        if isinstance(result, Response):
            return result
        job = result
        flash(f"Request queued for {tool.name}. Refresh the dashboard to track progress.", "success")
    return redirect(url_for("tools.tool_detail", tool_key=tool_key))


def _handle_merge(tool: ToolCatalog):
    uploaded_files = _save_uploads("documents", multiple=True)
    if not SubscriptionService.is_user_premium(current_user) and len(uploaded_files) > 3:
        raise ValueError("Merge PDF is free for up to 3 files. Upgrade to Pro for larger merges.")
    input_paths = [str(StorageService.absolute_path(file_record)) for file_record in uploaded_files]
    names = [file_record.original_name for file_record in uploaded_files]

    def work(job_id, progress):
        job_dir = _job_dir(job_id, current_user.id)
        progress(35)
        output_path = job_dir / "merged_document.pdf"
        PDFService.merge_pdfs(input_paths, str(output_path))
        progress(85)
        output_file = _register_output(current_user.id, output_path, "merged_document.pdf", "Merged PDF")
        return {"output_file_id": output_file.id, "output_filename": output_file.original_name}

    return JobService.submit_job(
        current_user,
        tool,
        input_filename=", ".join(names),
        options={"file_count": len(uploaded_files)},
        work_fn=work,
    )


def _handle_split(tool: ToolCatalog):
    input_file = _save_uploads("document")[0]
    input_path = str(StorageService.absolute_path(input_file))
    split_mode = request.form.get("split_mode", "range")
    selection = request.form.get("page_range", "")
    every_n = int(request.form.get("every_n", "1"))

    def work(job_id, progress):
        job_dir = _job_dir(job_id, current_user.id)
        progress(30)
        output_paths = PDFService.split_pdf(
            input_path=input_path,
            output_dir=str(job_dir),
            mode="range" if split_mode == "range" else "every",
            selection=selection,
            every_n=every_n,
        )
        progress(80)
        if len(output_paths) == 1:
            output_file = _register_output(current_user.id, output_paths[0], Path(output_paths[0]).name, "Split PDF")
            return {"output_file_id": output_file.id, "output_filename": output_file.original_name}
        archive_path = job_dir / "split_outputs.zip"
        _bundle_outputs(output_paths, archive_path)
        output_file = _register_output(current_user.id, archive_path, "split_outputs.zip", "Split PDF bundle")
        return {"output_file_id": output_file.id, "output_filename": output_file.original_name}

    return JobService.submit_job(
        current_user,
        tool,
        input_filename=input_file.original_name,
        options={"split_mode": split_mode, "page_range": selection, "every_n": every_n},
        work_fn=work,
    )


def _handle_rotate(tool: ToolCatalog):
    input_file = _save_uploads("document")[0]
    selection = request.form.get("pages", "")
    angle = int(request.form.get("angle", "90"))
    input_path = str(StorageService.absolute_path(input_file))

    def work(job_id, progress):
        job_dir = _job_dir(job_id, current_user.id)
        progress(40)
        output_path = job_dir / "rotated_document.pdf"
        PDFService.rotate_pdf(input_path, str(output_path), selection, angle)
        progress(85)
        output_file = _register_output(current_user.id, output_path, "rotated_document.pdf", "Rotated PDF")
        return {"output_file_id": output_file.id, "output_filename": output_file.original_name}

    return JobService.submit_job(current_user, tool, input_file.original_name, {"pages": selection, "angle": angle}, work)


def _handle_delete(tool: ToolCatalog):
    input_file = _save_uploads("document")[0]
    selection = request.form.get("pages", "")
    input_path = str(StorageService.absolute_path(input_file))

    def work(job_id, progress):
        job_dir = _job_dir(job_id, current_user.id)
        progress(35)
        output_path = job_dir / "trimmed_document.pdf"
        PDFService.delete_pages(input_path, str(output_path), selection)
        progress(85)
        output_file = _register_output(current_user.id, output_path, "trimmed_document.pdf", "Deleted pages PDF")
        return {"output_file_id": output_file.id, "output_filename": output_file.original_name}

    return JobService.submit_job(current_user, tool, input_file.original_name, {"pages": selection}, work)


def _handle_reorder(tool: ToolCatalog):
    input_file = _save_uploads("document")[0]
    order = request.form.get("order", "")
    input_path = str(StorageService.absolute_path(input_file))

    def work(job_id, progress):
        job_dir = _job_dir(job_id, current_user.id)
        progress(35)
        output_path = job_dir / "reordered_document.pdf"
        PDFService.reorder_pages(input_path, str(output_path), order)
        progress(85)
        output_file = _register_output(current_user.id, output_path, "reordered_document.pdf", "Reordered PDF")
        return {"output_file_id": output_file.id, "output_filename": output_file.original_name}

    return JobService.submit_job(current_user, tool, input_file.original_name, {"order": order}, work)


def _handle_watermark(tool: ToolCatalog):
    input_file = _save_uploads("document")[0]
    watermark_type = request.form.get("watermark_type", "text")
    watermark_text = request.form.get("watermark_text", "CONFIDENTIAL")
    opacity = float(request.form.get("opacity", "0.2"))
    position = request.form.get("position", "center")
    input_path = str(StorageService.absolute_path(input_file))
    image_path = ""
    if watermark_type == "image":
        image_file = _save_uploads("watermark_image")[0]
        image_path = str(StorageService.absolute_path(image_file))

    def work(job_id, progress):
        job_dir = _job_dir(job_id, current_user.id)
        progress(30)
        output_path = job_dir / "watermarked_document.pdf"
        if watermark_type == "image":
            PDFService.add_image_watermark(input_path, str(output_path), image_path, opacity=opacity, position=position)
        else:
            PDFService.add_text_watermark(input_path, str(output_path), watermark_text, opacity=opacity, position=position)
        progress(85)
        output_file = _register_output(current_user.id, output_path, "watermarked_document.pdf", "Watermarked PDF")
        return {"output_file_id": output_file.id, "output_filename": output_file.original_name}

    return JobService.submit_job(
        current_user,
        tool,
        input_file.original_name,
        {"watermark_type": watermark_type, "position": position},
        work,
    )


def _handle_security(tool: ToolCatalog):
    input_file = _save_uploads("document")[0]
    forced_action = ""
    if tool.tool_key == "protect_pdf":
        forced_action = "protect"
    elif tool.tool_key == "unlock_pdf":
        forced_action = "unlock"
    action = forced_action or request.form.get("security_action", "protect")
    password = request.form.get("password", "")
    if not password:
        raise ValueError("Password is required for this action.")
    input_path = str(StorageService.absolute_path(input_file))

    def work(job_id, progress):
        job_dir = _job_dir(job_id, current_user.id)
        progress(35)
        output_path = job_dir / ("protected_document.pdf" if action == "protect" else "unlocked_document.pdf")
        if action == "protect":
            PDFService.protect_pdf(input_path, str(output_path), password)
        else:
            PDFService.remove_password(input_path, str(output_path), password)
        progress(85)
        output_file = _register_output(current_user.id, output_path, output_path.name, "Secured PDF")
        return {"output_file_id": output_file.id, "output_filename": output_file.original_name}

    return JobService.submit_job(current_user, tool, input_file.original_name, {"action": action}, work)


def _handle_pdf_to_images(tool: ToolCatalog):
    input_file = _save_uploads("document")[0]
    image_format = request.form.get("image_format", "png")
    input_path = str(StorageService.absolute_path(input_file))

    def work(job_id, progress):
        job_dir = _job_dir(job_id, current_user.id)
        progress(35)
        image_paths = PDFService.pdf_to_images(input_path, str(job_dir), image_format=image_format)
        archive_path = job_dir / "pdf_images.zip"
        _bundle_outputs(image_paths, archive_path)
        progress(85)
        output_file = _register_output(current_user.id, archive_path, "pdf_images.zip", "PDF to images bundle")
        return {"output_file_id": output_file.id, "output_filename": output_file.original_name}

    return JobService.submit_job(current_user, tool, input_file.original_name, {"format": image_format}, work)


def _handle_images_to_pdf(tool: ToolCatalog):
    uploaded_files = _save_uploads("images", multiple=True)
    image_paths = [str(StorageService.absolute_path(file_record)) for file_record in uploaded_files]
    charged_amount = 0
    charge_reference = f"DIRECT-{current_user.id}-images_to_pdf-{int(datetime.now(timezone.utc).timestamp())}"

    if tool.is_payperuse_allowed and not SubscriptionService.is_user_premium(current_user):
        PricingService.charge_tool(current_user, tool, charge_reference)
        charged_amount = tool.price_paise

    try:
        direct_dir = Path(current_app.config["OUTPUT_ROOT"]) / str(current_user.id) / "direct_downloads"
        direct_dir.mkdir(parents=True, exist_ok=True)
        filename = f"images_to_pdf_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}.pdf"
        output_path = direct_dir / filename
        PDFService.images_to_pdf(image_paths, str(output_path))
        output_file = _register_output(current_user.id, output_path, filename, "Images to PDF")
    except Exception:
        if charged_amount > 0:
            PricingService.refund(
                user=current_user,
                amount_paise=charged_amount,
                reference=f"{charge_reference}-REFUND",
                note="Refund for failed direct images to PDF conversion",
            )
        raise

    return send_file(
        StorageService.absolute_path(output_file),
        as_attachment=True,
        download_name=output_file.original_name,
        mimetype=output_file.mime_type,
    )


def _handle_pdf_to_text(tool: ToolCatalog):
    input_file = _save_uploads("document")[0]
    input_path = str(StorageService.absolute_path(input_file))

    def work(job_id, progress):
        job_dir = _job_dir(job_id, current_user.id)
        progress(40)
        output_path = job_dir / "document_text.txt"
        PDFService.pdf_to_text(input_path, str(output_path))
        progress(85)
        output_file = _register_output(current_user.id, output_path, "document_text.txt", "Extracted text")
        return {"output_file_id": output_file.id, "output_filename": output_file.original_name}

    return JobService.submit_job(current_user, tool, input_file.original_name, {}, work)


def _handle_page_numbers(tool: ToolCatalog):
    input_file = _save_uploads("document")[0]
    position = request.form.get("position", "bottom_center")
    font_size = int(request.form.get("font_size", "10"))
    input_path = str(StorageService.absolute_path(input_file))

    def work(job_id, progress):
        job_dir = _job_dir(job_id, current_user.id)
        progress(35)
        output_path = job_dir / "page_numbers.pdf"
        PDFService.add_page_numbers(input_path, str(output_path), position=position, font_size=font_size)
        progress(85)
        output_file = _register_output(current_user.id, output_path, "page_numbers.pdf", "Page numbers PDF")
        return {"output_file_id": output_file.id, "output_filename": output_file.original_name}

    return JobService.submit_job(current_user, tool, input_file.original_name, {"position": position}, work)


def _handle_header_footer(tool: ToolCatalog):
    input_file = _save_uploads("document")[0]
    header = request.form.get("header", "")
    footer = request.form.get("footer", "")
    input_path = str(StorageService.absolute_path(input_file))

    def work(job_id, progress):
        job_dir = _job_dir(job_id, current_user.id)
        progress(35)
        output_path = job_dir / "header_footer.pdf"
        PDFService.add_header_footer(input_path, str(output_path), header=header, footer=footer)
        progress(85)
        output_file = _register_output(current_user.id, output_path, "header_footer.pdf", "Header footer PDF")
        return {"output_file_id": output_file.id, "output_filename": output_file.original_name}

    return JobService.submit_job(current_user, tool, input_file.original_name, {"header": header, "footer": footer}, work)


def _handle_remove_metadata(tool: ToolCatalog):
    input_file = _save_uploads("document")[0]
    input_path = str(StorageService.absolute_path(input_file))

    def work(job_id, progress):
        job_dir = _job_dir(job_id, current_user.id)
        progress(35)
        output_path = job_dir / "metadata_clean.pdf"
        PDFService.remove_metadata(input_path, str(output_path))
        progress(85)
        output_file = _register_output(current_user.id, output_path, "metadata_clean.pdf", "Metadata removed PDF")
        return {"output_file_id": output_file.id, "output_filename": output_file.original_name}

    return JobService.submit_job(current_user, tool, input_file.original_name, {}, work)


def _handle_scanner(tool: ToolCatalog):
    uploaded_files = _save_uploads("scan_images", multiple=True)
    image_paths = [str(StorageService.absolute_path(file_record)) for file_record in uploaded_files]
    brightness = float(request.form.get("brightness", "1.1"))
    contrast = float(request.form.get("contrast", "1.25"))
    black_white = bool(request.form.get("black_white"))
    export_type = request.form.get("export_type", "pdf")
    if tool.tool_key == "scan_to_pdf":
        export_type = "pdf"
    if tool.tool_key == "deskew_scan_pdf":
        export_type = "pdf"
        black_white = bool(request.form.get("black_white"))

    def work(job_id, progress):
        job_dir = _job_dir(job_id, current_user.id)
        progress(25)
        result = ScannerService.batch_scan(
            image_paths,
            str(job_dir),
            brightness=brightness,
            contrast=contrast,
            black_white=black_white,
            export_type=export_type,
        )
        progress(80)
        if export_type == "pdf":
            output_file = _register_output(current_user.id, result["pdf_path"], "scanned_document.pdf", "Scanned document")
        else:
            archive_path = job_dir / "scan_images.zip"
            _bundle_outputs(result["image_paths"], archive_path)
            output_file = _register_output(current_user.id, archive_path, "scan_images.zip", "Scanned image bundle")
        return {"output_file_id": output_file.id, "output_filename": output_file.original_name}

    return JobService.submit_job(
        current_user,
        tool,
        ", ".join(file_record.original_name for file_record in uploaded_files),
        {"export_type": export_type},
        work,
    )


def _handle_signature(tool: ToolCatalog):
    input_file = _save_uploads("document")[0]
    input_path = str(StorageService.absolute_path(input_file))
    signature_data = request.form.get("signature_data", "")
    signature_upload = request.files.get("signature_image")
    if signature_upload and signature_upload.filename:
        signature_file = StorageService.save_uploaded_file(signature_upload, current_user.id, kind="signature")
    elif signature_data:
        signature_file = StorageService.save_signature_data(signature_data, current_user.id)
    else:
        raise ValueError("Provide a drawn or uploaded signature.")

    placements_json = request.form.get("placements_json", "")
    placements = (
        json.loads(placements_json)
        if placements_json
        else [{"page": 1, "x": 320, "y": 60, "width": 160, "height": 64}]
    )
    date_stamp = request.form.get("date_stamp", "")
    if date_stamp:
        for placement in placements:
            placement["date_stamp"] = date_stamp

    def work(job_id, progress):
        job_dir = _job_dir(job_id, current_user.id)
        progress(35)
        output_path = job_dir / "signed_document.pdf"
        SignatureService.apply_signatures(
            input_path=input_path,
            output_path=str(output_path),
            signature_path=str(StorageService.absolute_path(signature_file)),
            placements=placements,
        )
        progress(85)
        output_file = _register_output(current_user.id, output_path, "signed_document.pdf", "Digitally signed PDF")
        return {"output_file_id": output_file.id, "output_filename": output_file.original_name}

    return JobService.submit_job(current_user, tool, input_file.original_name, {"placements": placements}, work)


def _handle_compress(tool: ToolCatalog):
    input_file = _save_uploads("document")[0]
    level = (request.form.get("level", "balanced") or "balanced").strip().lower()
    if level not in {"low", "balanced", "strong"}:
        level = "balanced"
    compress_action = (request.form.get("compress_action", "level") or "level").strip().lower()
    if compress_action not in {"level", "target", "increase"}:
        compress_action = "level"
    target_kb = _parse_int(
        request.form.get("target_kb"),
        field_name="target KB",
        default=100,
        minimum=10,
        maximum=150000,
    )
    input_path = str(StorageService.absolute_path(input_file))

    def work(job_id, progress):
        job_dir = _job_dir(job_id, current_user.id)
        progress(35)
        output_path = job_dir / "compressed_document.pdf"
        if compress_action == "target":
            details = PDFService.compress_pdf_to_target_size(input_path, str(output_path), target_kb=target_kb)
        elif compress_action == "increase":
            details = PDFService.increase_pdf_size(input_path, str(output_path), target_kb=target_kb)
        else:
            details = PDFService.compress_pdf(input_path, str(output_path), level=level)
        progress(85)
        output_file = _register_output(current_user.id, output_path, "compressed_document.pdf", "Compressed PDF")
        return {
            "output_file_id": output_file.id,
            "output_filename": output_file.original_name,
            "result_json": details,
        }

    return JobService.submit_job(
        current_user,
        tool,
        input_file.original_name,
        {"action": compress_action, "level": level, "target_kb": target_kb},
        work,
    )


def _handle_office_conversion(tool: ToolCatalog):
    input_file = _save_uploads("document")[0]
    input_path = str(StorageService.absolute_path(input_file))

    def work(job_id, progress):
        job_dir = _job_dir(job_id, current_user.id)
        progress(30)
        if tool.tool_key == "pdf_to_word":
            output_path = job_dir / "converted_document.docx"
            ConversionService.pdf_to_word(input_path, str(output_path))
        elif tool.tool_key == "pdf_to_ppt":
            output_path = job_dir / "converted_document.pptx"
            ConversionService.pdf_to_powerpoint(input_path, str(output_path), str(job_dir / "slides"))
        else:
            output_path = job_dir / "converted_document.xlsx"
            ConversionService.pdf_to_excel(input_path, str(output_path))
        progress(85)
        output_file = _register_output(current_user.id, output_path, output_path.name, "Office conversion output")
        return {"output_file_id": output_file.id, "output_filename": output_file.original_name}

    return JobService.submit_job(current_user, tool, input_file.original_name, {}, work)


def _handle_convert_from_pdf(tool: ToolCatalog):
    input_file = _save_uploads("document")[0]
    input_path = str(StorageService.absolute_path(input_file))
    tool_key = tool.tool_key

    def work(job_id, progress):
        job_dir = _job_dir(job_id, current_user.id)
        progress(30)

        if tool_key in {"pdf_to_word", "pdf_to_docx"}:
            output_path = job_dir / "converted_document.docx"
            ConversionService.pdf_to_word(input_path, str(output_path))
            label = "PDF to Word output"
        elif tool_key in {"pdf_to_ppt", "pdf_to_pptx"}:
            output_path = job_dir / "converted_document.pptx"
            ConversionService.pdf_to_powerpoint(input_path, str(output_path), str(job_dir / "slides"))
            label = "PDF to PowerPoint output"
        elif tool_key in {"pdf_to_excel", "pdf_to_xlsx"}:
            output_path = job_dir / "converted_document.xlsx"
            ConversionService.pdf_to_excel(input_path, str(output_path))
            label = "PDF to Excel output"
        elif tool_key == "pdf_to_html":
            output_path = job_dir / "converted_document.html"
            ConversionService.pdf_to_html(input_path, str(output_path))
            label = "PDF to HTML output"
        elif tool_key == "pdf_to_rtf":
            output_path = job_dir / "converted_document.rtf"
            ConversionService.pdf_to_rtf(input_path, str(output_path))
            label = "PDF to RTF output"
        elif tool_key in {"pdf_to_jpg", "pdf_to_png"}:
            image_format = "jpg" if tool_key == "pdf_to_jpg" else "png"
            image_paths = PDFService.pdf_to_images(
                input_path,
                str(job_dir / "images"),
                image_format=image_format,
            )
            archive_path = job_dir / f"{tool_key}_bundle.zip"
            _bundle_outputs(image_paths, archive_path)
            output_file = _register_output(
                current_user.id,
                archive_path,
                archive_path.name,
                "PDF to image bundle",
            )
            progress(85)
            return {"output_file_id": output_file.id, "output_filename": output_file.original_name}
        else:
            raise ValueError("Unsupported PDF conversion action.")

        progress(85)
        output_file = _register_output(current_user.id, output_path, output_path.name, label)
        return {"output_file_id": output_file.id, "output_filename": output_file.original_name}

    return JobService.submit_job(
        current_user,
        tool,
        input_file.original_name,
        {"conversion": tool_key},
        work,
    )


def _handle_convert_to_pdf(tool: ToolCatalog):
    tool_key = tool.tool_key
    image_source_keys = {
        "jpg_to_pdf",
        "png_to_pdf",
        "webp_to_pdf",
        "heic_to_pdf",
        "svg_to_pdf",
        "tiff_to_pdf",
    }
    input_name = ""
    image_paths: list[str] = []
    source_path = ""

    if tool_key in image_source_keys:
        uploaded_files = _save_uploads("documents", multiple=True)
        image_paths = [str(StorageService.absolute_path(file_record)) for file_record in uploaded_files]
        input_name = ", ".join(file_record.original_name for file_record in uploaded_files)
    else:
        source_file = _save_uploads("document")[0]
        source_path = str(StorageService.absolute_path(source_file))
        input_name = source_file.original_name

    def work(job_id, progress):
        job_dir = _job_dir(job_id, current_user.id)
        progress(30)
        output_path = job_dir / "converted_to_pdf.pdf"

        if tool_key in {"word_to_pdf", "doc_to_pdf", "docx_to_pdf"}:
            ConversionService.word_to_pdf(source_path, str(output_path))
        elif tool_key in {"powerpoint_to_pdf", "ppt_to_pdf", "pptx_to_pdf"}:
            ConversionService.powerpoint_to_pdf(source_path, str(output_path))
        elif tool_key in {"excel_to_pdf", "xls_to_pdf", "xlsx_to_pdf"}:
            ConversionService.excel_to_pdf(source_path, str(output_path))
        elif tool_key == "html_to_pdf":
            ConversionService.html_to_pdf(source_path, str(output_path))
        elif tool_key == "text_to_pdf":
            ConversionService.text_to_pdf(source_path, str(output_path))
        elif tool_key in image_source_keys:
            PDFService.images_to_pdf(image_paths, str(output_path))
        else:
            raise ValueError("Unsupported conversion to PDF action.")

        progress(85)
        output_file = _register_output(current_user.id, output_path, "converted_to_pdf.pdf", "Converted to PDF")
        return {"output_file_id": output_file.id, "output_filename": output_file.original_name}

    return JobService.submit_job(
        current_user,
        tool,
        input_name,
        {"conversion": tool_key},
        work,
    )


def _handle_advanced_pdf(tool: ToolCatalog):
    tool_key = tool.tool_key
    input_name = ""
    input_path = ""
    second_path = ""
    input_paths: list[str] = []

    if tool_key == "alternate_mix_pdf":
        files = _save_uploads("documents", multiple=True)
        if len(files) < 2:
            raise ValueError("Upload at least two PDFs for alternate mix.")
        input_paths = [str(StorageService.absolute_path(file_record)) for file_record in files]
        input_name = ", ".join(file_record.original_name for file_record in files)
    elif tool_key == "compare_pdf":
        first_file = _save_uploads("document")[0]
        second_file = _save_uploads("compare_document")[0]
        input_path = str(StorageService.absolute_path(first_file))
        second_path = str(StorageService.absolute_path(second_file))
        input_name = f"{first_file.original_name}, {second_file.original_name}"
    else:
        input_file = _save_uploads("document")[0]
        input_path = str(StorageService.absolute_path(input_file))
        input_name = input_file.original_name

    page_range = (request.form.get("page_range", "") or "").strip()
    every_n = _parse_int(
        request.form.get("every_n"),
        field_name="every n pages",
        default=1,
        minimum=1,
        maximum=500,
    )
    max_size_mb = _parse_float(
        request.form.get("max_size_mb"),
        field_name="max size MB",
        default=2.0,
        minimum=0.25,
        maximum=500.0,
    )
    pages_per_sheet = _parse_int(
        request.form.get("pages_per_sheet"),
        field_name="pages per sheet",
        default=2,
        minimum=2,
        maximum=16,
    )
    scale_percent = _parse_float(
        request.form.get("scale_percent"),
        field_name="scale percent",
        default=100.0,
        minimum=10.0,
        maximum=400.0,
    )
    margin_points = _parse_float(
        request.form.get("margin_points"),
        field_name="margin points",
        default=0.0,
        minimum=0.0,
        maximum=400.0,
    )
    text_delimiter = (request.form.get("text_delimiter", "") or "").strip()
    margin_percent = _parse_float(
        request.form.get("margin_percent"),
        field_name="margin percent",
        default=5.0,
        minimum=0.0,
        maximum=40.0,
    )
    image_format = (request.form.get("image_format", "png") or "png").strip().lower()
    if image_format not in {"png", "jpg", "jpeg", "webp"}:
        image_format = "png"

    redact_terms = [
        token.strip()
        for token in (request.form.get("redact_terms", "") or "").replace("\n", ",").split(",")
        if token.strip()
    ]
    bates_prefix = (request.form.get("bates_prefix", "DOC") or "DOC").strip() or "DOC"
    bates_start = _parse_int(
        request.form.get("bates_start"),
        field_name="bates start number",
        default=1,
        minimum=1,
        maximum=9_999_999,
    )
    bates_position = (request.form.get("bates_position", "bottom_right") or "bottom_right").strip()
    if bates_position not in {"top_left", "top_center", "top_right", "bottom_left", "bottom_center", "bottom_right"}:
        bates_position = "bottom_right"
    bookmarks = _parse_bookmark_payload(request.form.get("bookmarks_text", ""))
    metadata_payload = {
        "title": request.form.get("metadata_title", ""),
        "author": request.form.get("metadata_author", ""),
        "subject": request.form.get("metadata_subject", ""),
        "keywords": request.form.get("metadata_keywords", ""),
    }
    grayscale_requested = _is_checked("grayscale")

    def work(job_id, progress):
        job_dir = _job_dir(job_id, current_user.id)
        progress(30)

        if tool_key == "extract_pages":
            output_paths = PDFService.split_pdf(
                input_path=input_path,
                output_dir=str(job_dir),
                mode="range",
                selection=page_range or "all",
            )
            output_path = Path(output_paths[0])
            output_file = _register_output(current_user.id, output_path, "extracted_pages.pdf", "Extracted pages PDF")
        elif tool_key == "split_by_pages":
            output_paths = PDFService.split_pdf(
                input_path=input_path,
                output_dir=str(job_dir),
                mode="every",
                every_n=every_n,
            )
            archive_path = job_dir / "split_by_pages.zip"
            _bundle_outputs(output_paths, archive_path)
            output_file = _register_output(current_user.id, archive_path, archive_path.name, "Split by pages bundle")
        elif tool_key == "split_by_bookmarks":
            output_paths = PDFService.split_pdf_by_bookmarks(input_path, str(job_dir))
            archive_path = job_dir / "split_by_bookmarks.zip"
            _bundle_outputs(output_paths, archive_path)
            output_file = _register_output(
                current_user.id,
                archive_path,
                archive_path.name,
                "Split by bookmarks bundle",
            )
        elif tool_key == "split_in_half":
            output_paths = PDFService.split_pdf_in_half(input_path, str(job_dir))
            archive_path = job_dir / "split_in_half.zip"
            _bundle_outputs(output_paths, archive_path)
            output_file = _register_output(current_user.id, archive_path, archive_path.name, "Split in half bundle")
        elif tool_key == "split_by_size":
            output_paths = PDFService.split_pdf_by_size(input_path, str(job_dir), max_size_mb=max_size_mb)
            archive_path = job_dir / "split_by_size.zip"
            _bundle_outputs(output_paths, archive_path)
            output_file = _register_output(current_user.id, archive_path, archive_path.name, "Split by size bundle")
        elif tool_key == "split_by_text":
            if not text_delimiter:
                raise ValueError("Text delimiter is required for split-by-text.")
            output_paths = PDFService.split_pdf_by_text(input_path, str(job_dir), delimiter=text_delimiter)
            archive_path = job_dir / "split_by_text.zip"
            _bundle_outputs(output_paths, archive_path)
            output_file = _register_output(current_user.id, archive_path, archive_path.name, "Split by text bundle")
        elif tool_key == "alternate_mix_pdf":
            output_path = job_dir / "alternate_mix.pdf"
            PDFService.alternate_mix_pdfs(input_paths, str(output_path))
            output_file = _register_output(current_user.id, output_path, output_path.name, "Alternate mixed PDF")
        elif tool_key == "n_up_pdf":
            output_path = job_dir / "n_up_document.pdf"
            PDFService.n_up_pdf(input_path, str(output_path), pages_per_sheet=pages_per_sheet)
            output_file = _register_output(current_user.id, output_path, output_path.name, "N-up PDF")
        elif tool_key == "resize_pdf":
            output_path = job_dir / "resized_document.pdf"
            PDFService.resize_pdf_pages(
                input_path,
                str(output_path),
                scale_percent=scale_percent,
                margin_points=margin_points,
            )
            output_file = _register_output(current_user.id, output_path, output_path.name, "Resized PDF")
        elif tool_key == "deskew_pdf":
            output_path = job_dir / "deskewed_document.pdf"
            PDFService.deskew_pdf(input_path, str(output_path))
            output_file = _register_output(current_user.id, output_path, output_path.name, "Deskewed PDF")
        elif tool_key == "crop_pdf":
            output_path = job_dir / "cropped_document.pdf"
            PDFService.crop_pdf(input_path, str(output_path), margin_percent=margin_percent)
            output_file = _register_output(current_user.id, output_path, output_path.name, "Cropped PDF")
        elif tool_key in {"flatten_pdf", "grayscale_pdf"}:
            output_path = job_dir / ("grayscale_flattened.pdf" if tool_key == "grayscale_pdf" else "flattened_document.pdf")
            PDFService.flatten_pdf(
                input_path,
                str(output_path),
                grayscale=(tool_key == "grayscale_pdf" or grayscale_requested),
            )
            output_file = _register_output(current_user.id, output_path, output_path.name, "Flattened PDF")
        elif tool_key == "extract_images_pdf":
            image_paths = PDFService.extract_images(input_path, str(job_dir / "images"), output_format=image_format)
            archive_path = job_dir / "extracted_images.zip"
            _bundle_outputs(image_paths, archive_path)
            output_file = _register_output(current_user.id, archive_path, archive_path.name, "Extracted images bundle")
        elif tool_key == "remove_annotations_pdf":
            output_path = job_dir / "annotations_removed.pdf"
            PDFService.remove_annotations(input_path, str(output_path))
            output_file = _register_output(current_user.id, output_path, output_path.name, "Annotations removed PDF")
        elif tool_key == "repair_pdf":
            output_path = job_dir / "repaired_document.pdf"
            PDFService.repair_pdf(input_path, str(output_path))
            output_file = _register_output(current_user.id, output_path, output_path.name, "Repaired PDF")
        elif tool_key == "redact_pdf":
            if not redact_terms:
                raise ValueError("Enter at least one keyword for redaction.")
            output_path = job_dir / "redacted_document.pdf"
            PDFService.redact_pdf(input_path, str(output_path), redact_terms)
            output_file = _register_output(current_user.id, output_path, output_path.name, "Redacted PDF")
        elif tool_key == "compare_pdf":
            output_path = job_dir / "pdf_comparison.txt"
            PDFService.compare_pdfs(input_path, second_path, str(output_path))
            output_file = _register_output(current_user.id, output_path, output_path.name, "PDF comparison report")
        elif tool_key == "bates_numbering":
            output_path = job_dir / "bates_numbered.pdf"
            PDFService.add_bates_numbers(
                input_path,
                str(output_path),
                prefix=bates_prefix,
                start_number=bates_start,
                position=bates_position,
            )
            output_file = _register_output(current_user.id, output_path, output_path.name, "Bates numbered PDF")
        elif tool_key == "create_bookmarks":
            if not bookmarks:
                raise ValueError("Provide bookmarks in format: Title|Page.")
            output_path = job_dir / "bookmarked_document.pdf"
            PDFService.create_bookmarks(input_path, str(output_path), bookmarks)
            output_file = _register_output(current_user.id, output_path, output_path.name, "Bookmarked PDF")
        elif tool_key == "edit_metadata_pdf":
            if not any((value or "").strip() for value in metadata_payload.values()):
                raise ValueError("Enter at least one metadata value.")
            output_path = job_dir / "metadata_updated.pdf"
            PDFService.update_metadata(input_path, str(output_path), metadata_payload)
            output_file = _register_output(current_user.id, output_path, output_path.name, "Metadata updated PDF")
        else:
            raise ValueError("Unsupported advanced PDF action.")

        progress(85)
        return {"output_file_id": output_file.id, "output_filename": output_file.original_name}

    return JobService.submit_job(
        current_user,
        tool,
        input_name,
        {
            "action": tool_key,
            "page_range": page_range,
            "every_n": every_n,
            "max_size_mb": max_size_mb,
            "pages_per_sheet": pages_per_sheet,
            "scale_percent": scale_percent,
            "margin_points": margin_points,
        },
        work,
    )


def _handle_editor_suite(tool: ToolCatalog):
    input_file = _save_uploads("document")[0]
    input_path = str(StorageService.absolute_path(input_file))
    actions_payload = (request.form.get("editor_actions", "") or "").strip()
    editor_image_paths: dict[str, str] = {}
    editor_image = request.files.get("editor_image")
    if editor_image and editor_image.filename:
        image_record = StorageService.save_uploaded_file(editor_image, current_user.id, kind="upload")
        editor_image_paths["uploaded"] = str(StorageService.absolute_path(image_record))

    def _parse_form_fields(raw_value: str) -> list[dict]:
        parsed: list[dict] = []
        for index, line in enumerate((raw_value or "").splitlines(), start=1):
            chunk = line.strip()
            if not chunk:
                continue
            parts = [part.strip() for part in chunk.split("|")]
            if len(parts) < 5:
                raise ValueError(
                    f"Invalid form field entry on line {index}. Use Label|x|y|width|height|page(optional)."
                )
            label = parts[0]
            try:
                x = float(parts[1])
                y = float(parts[2])
                width = float(parts[3])
                height = float(parts[4])
                page = int(parts[5]) if len(parts) > 5 and parts[5] else 1
            except ValueError as exc:
                raise ValueError(f"Invalid numeric value in form field line {index}.") from exc
            parsed.append(
                {
                    "label": label,
                    "x": x,
                    "y": y,
                    "width": width,
                    "height": height,
                    "page": page,
                }
            )
        return parsed

    if tool.tool_key == "create_forms":
        fields_input = (request.form.get("form_fields", "") or "").strip()
        if not fields_input:
            raise ValueError("Provide at least one field definition.")
        form_fields = _parse_form_fields(fields_input)

        def work(job_id, progress):
            job_dir = _job_dir(job_id, current_user.id)
            progress(35)
            output_path = job_dir / "form_layout.pdf"
            PDFService.create_form_layout(input_path, str(output_path), form_fields)
            progress(85)
            output_file = _register_output(current_user.id, output_path, output_path.name, "Form layout PDF")
            return {"output_file_id": output_file.id, "output_filename": output_file.original_name}

        return JobService.submit_job(
            current_user,
            tool,
            input_file.original_name,
            {"form_fields": len(form_fields)},
            work,
        )

    actions: list[dict] = []
    if actions_payload:
        try:
            loaded_actions = json.loads(actions_payload)
        except Exception as exc:
            raise ValueError("Invalid editor actions payload.") from exc
        if not isinstance(loaded_actions, list):
            raise ValueError("Editor actions should be an array.")
        actions = [action for action in loaded_actions if isinstance(action, dict)]
    else:
        text = (request.form.get("text", "") or "").strip()
        if text:
            actions.append(
                {
                    "type": "text",
                    "text": text,
                    "page": _parse_int(request.form.get("page"), field_name="page", default=1, minimum=1, maximum=5000),
                    "x": _parse_float(request.form.get("x"), field_name="x", default=72, minimum=0, maximum=4000),
                    "y": _parse_float(request.form.get("y"), field_name="y", default=100, minimum=0, maximum=6000),
                    "font_size": _parse_float(
                        request.form.get("font_size"),
                        field_name="font size",
                        default=14,
                        minimum=6,
                        maximum=120,
                    ),
                    "color": request.form.get("color", "#18342b"),
                }
            )
        if request.form.get("add_box"):
            actions.append(
                {
                    "type": "rect",
                    "page": _parse_int(request.form.get("page"), field_name="page", default=1, minimum=1, maximum=5000),
                    "x": _parse_float(request.form.get("x"), field_name="x", default=72, minimum=0, maximum=4000),
                    "y": _parse_float(request.form.get("y"), field_name="y", default=130, minimum=0, maximum=6000),
                    "width": _parse_float(request.form.get("box_width"), field_name="box width", default=180, minimum=10, maximum=4000),
                    "height": _parse_float(request.form.get("box_height"), field_name="box height", default=50, minimum=10, maximum=4000),
                    "stroke_width": 1.6,
                    "color": request.form.get("color", "#18342b"),
                }
            )
        if request.form.get("add_line"):
            start_x = _parse_float(request.form.get("x"), field_name="x", default=72, minimum=0, maximum=4000)
            start_y = _parse_float(request.form.get("y"), field_name="y", default=200, minimum=0, maximum=6000)
            actions.append(
                {
                    "type": "line",
                    "page": _parse_int(request.form.get("page"), field_name="page", default=1, minimum=1, maximum=5000),
                    "x1": start_x,
                    "y1": start_y,
                    "x2": start_x + _parse_float(
                        request.form.get("line_length"),
                        field_name="line length",
                        default=200,
                        minimum=10,
                        maximum=5000,
                    ),
                    "y2": start_y,
                    "width": 1.8,
                    "color": request.form.get("color", "#18342b"),
                }
            )
        if editor_image_paths:
            actions.append(
                {
                    "type": "image",
                    "image_key": "uploaded",
                    "page": _parse_int(request.form.get("page"), field_name="page", default=1, minimum=1, maximum=5000),
                    "x": _parse_float(request.form.get("x"), field_name="x", default=72, minimum=0, maximum=4000),
                    "y": _parse_float(request.form.get("y"), field_name="y", default=72, minimum=0, maximum=6000),
                    "width": _parse_float(request.form.get("image_width"), field_name="image width", default=160, minimum=10, maximum=5000),
                    "height": _parse_float(request.form.get("image_height"), field_name="image height", default=70, minimum=10, maximum=5000),
                }
            )

    if not actions:
        raise ValueError("Add at least one edit action.")

    def work(job_id, progress):
        job_dir = _job_dir(job_id, current_user.id)
        progress(35)
        output_path = job_dir / "edited_document.pdf"
        PDFService.apply_editor_actions(
            input_path,
            str(output_path),
            actions=actions,
            image_paths=editor_image_paths,
        )
        progress(85)
        output_file = _register_output(current_user.id, output_path, output_path.name, "PDF editor output")
        return {"output_file_id": output_file.id, "output_filename": output_file.original_name}

    return JobService.submit_job(
        current_user,
        tool,
        input_file.original_name,
        {"editor_actions": len(actions)},
        work,
    )


def _handle_image_utilities(tool: ToolCatalog):
    action_defaults = {
        "compress_image": "compress_quality",
        "compress_image_exact": "compress_target",
        "convert_image": "convert",
        "crop_image": "crop_custom",
        "image_to_jpg": "convert",
        "image_to_jpeg": "convert",
        "webp_to_jpg": "convert",
        "jpeg_to_png": "convert",
        "png_to_jpeg": "convert",
        "heic_to_jpg": "convert",
        "image_to_pdf": "image_to_pdf",
        "jpg_to_pdf_under_50kb": "jpg_to_pdf_target",
        "jpg_to_pdf_under_100kb": "jpg_to_pdf_target",
        "jpg_to_pdf_under_200kb": "jpg_to_pdf_target",
        "jpg_to_pdf_under_300kb": "jpg_to_pdf_target",
        "jpg_to_pdf_under_500kb": "jpg_to_pdf_target",
        "jpg_to_text": "image_to_text",
        "png_to_text": "image_to_text",
        "ocr_image": "image_to_text",
        "reduce_image_kb": "compress_target",
        "increase_image_kb": "increase_target",
        "resize_image_pixel": "resize_pixel",
        "resize_image_cm": "resize_unit",
        "resize_image_mm": "resize_unit",
        "resize_image_inch": "resize_unit",
        "passport_photo_maker": "resize_preset",
    }

    action = (request.form.get("image_action", "") or "").strip().lower()
    if not action:
        action = action_defaults.get(tool.tool_key, "resize_pixel")

    preset_key = (request.form.get("preset_key", "") or "").strip().lower()
    target_format = (request.form.get("target_format", "png") or "png").strip().lower()
    if target_format == "jpeg":
        target_format = "jpg"
    width = _parse_int(request.form.get("width"), field_name="width", default=1200, minimum=1, maximum=20000)
    height = _parse_int(request.form.get("height"), field_name="height", default=1600, minimum=1, maximum=20000)
    quality = _parse_int(request.form.get("quality"), field_name="quality", default=78, minimum=20, maximum=98)
    dpi = _parse_int(request.form.get("dpi"), field_name="dpi", default=300, minimum=72, maximum=1200)
    target_kb = _parse_int(request.form.get("target_kb"), field_name="target KB", default=100, minimum=5, maximum=20000)
    target_mb = _parse_float(
        request.form.get("target_mb"),
        field_name="target MB",
        default=0.0,
        minimum=0.0,
        maximum=250.0,
    )
    angle = _parse_int(request.form.get("angle"), field_name="angle", default=90, minimum=-360, maximum=360)
    unit = (request.form.get("unit", "px") or "px").strip().lower()
    text_value = (request.form.get("text", "") or "").strip()
    name_value = (request.form.get("person_name", "") or "").strip()
    dob_value = (request.form.get("dob", "") or "").strip()
    x = _parse_int(request.form.get("x"), field_name="x", default=20, minimum=0, maximum=10000)
    y = _parse_int(request.form.get("y"), field_name="y", default=20, minimum=0, maximum=10000)
    crop_x = _parse_int(request.form.get("crop_x"), field_name="crop x", default=0, minimum=0, maximum=10000)
    crop_y = _parse_int(request.form.get("crop_y"), field_name="crop y", default=0, minimum=0, maximum=10000)
    crop_w = _parse_int(request.form.get("crop_w"), field_name="crop width", default=500, minimum=1, maximum=20000)
    crop_h = _parse_int(request.form.get("crop_h"), field_name="crop height", default=500, minimum=1, maximum=20000)
    split_rows = _parse_int(request.form.get("split_rows"), field_name="split rows", default=2, minimum=1, maximum=12)
    split_cols = _parse_int(request.form.get("split_cols"), field_name="split cols", default=2, minimum=1, maximum=12)
    blur_radius = _parse_float(
        request.form.get("blur_radius"),
        field_name="blur radius",
        default=3.0,
        minimum=0.5,
        maximum=80.0,
    )
    pixel_size = _parse_int(
        request.form.get("pixel_size"),
        field_name="pixel size",
        default=10,
        minimum=2,
        maximum=100,
    )
    bw_threshold = _parse_int(
        request.form.get("bw_threshold"),
        field_name="black-white threshold",
        default=145,
        minimum=0,
        maximum=255,
    )
    motion_radius = _parse_int(
        request.form.get("motion_radius"),
        field_name="motion radius",
        default=11,
        minimum=3,
        maximum=101,
    )
    pixel_art_factor = _parse_int(
        request.form.get("pixel_art_factor"),
        field_name="pixel art factor",
        default=12,
        minimum=2,
        maximum=80,
    )
    line_mode = (request.form.get("join_direction", "vertical") or "vertical").strip().lower()
    metadata_title = (request.form.get("metadata_title", "") or "").strip()
    metadata_author = (request.form.get("metadata_author", "") or "").strip()
    logo_width = _parse_int(request.form.get("logo_width"), field_name="logo width", default=120, minimum=10, maximum=5000)
    upscale_factor = _parse_float(
        request.form.get("upscale_factor"),
        field_name="upscale factor",
        default=2.0,
        minimum=1.1,
        maximum=6.0,
    )
    source_value = _parse_float(
        request.form.get("size_value"),
        field_name="size value",
        default=1.0,
        minimum=0.0001,
        maximum=100000.0,
    )
    source_unit = (request.form.get("size_from", "mb") or "mb").strip().lower()
    target_unit = (request.form.get("size_to", "kb") or "kb").strip().lower()
    if source_unit not in {"kb", "mb"}:
        source_unit = "mb"
    if target_unit not in {"kb", "mb"}:
        target_unit = "kb"

    single_image_actions = {
        "resize_pixel",
        "resize_unit",
        "resize_preset",
        "compress_quality",
        "compress_target",
        "increase_target",
        "convert",
        "image_to_text",
        "rotate",
        "flip",
        "blur_background",
        "remove_background",
        "remove_object",
        "watermark",
        "crop_custom",
        "crop_square",
        "crop_circle",
        "split_image",
        "color_picker",
        "metadata_view",
        "metadata_edit",
        "metadata_remove",
        "blur_image",
        "pixelate_image",
        "motion_blur",
        "grayscale",
        "black_white",
        "beautify",
        "unblur",
        "blur_face",
        "pixelate_face",
        "censor_photo",
        "retouch",
        "add_text",
        "add_name_dob",
        "add_logo",
        "dpi_check",
        "dpi_convert",
        "super_resolution",
        "upscale_ai",
        "pixel_art",
    }
    multi_image_actions = {"image_to_pdf", "join_images"}
    pdf_actions = {"pdf_to_images"}
    signature_actions = {"merge_photo_signature"}
    value_only_actions = {"size_convert"}

    if action in {
        "resize",
        "resize_image_pixel",
        "resize_by_pixel",
        "bulk_resize",
        "resize_signature",
        "resize_image_3_5x4_5",
        "bulk_image_resizer",
    }:
        action = "resize_pixel"
    elif action in {"resize_in_cm", "resize_cm", "resize_image_centimeter", "resize_in_centimeters"}:
        action = "resize_unit"
        unit = "cm"
    elif action in {"resize_in_mm", "resize_mm", "resize_in_millimeters"}:
        action = "resize_unit"
        unit = "mm"
    elif action in {"resize_in_inch", "resize_inches", "resize_in_inches"}:
        action = "resize_unit"
        unit = "inch"
    elif action in {"compress", "compress_image", "jpg_to_kb", "image_compressor"}:
        action = "compress_quality"
    elif action in {"reduce_kb", "reduce_size_kb", "reduce_size_mb", "reduce_image_size_in_kb", "reduce_image_size_kb"}:
        action = "compress_target"
    elif action in {"increase_kb", "increase_size_kb", "increase_image_size_in_kb"}:
        action = "increase_target"
    elif action in {"rotate_image"}:
        action = "rotate"
    elif action in {"flip_image"}:
        action = "flip"
    elif action in {"watermark_images"}:
        action = "watermark"
    elif action in {"freehand_crop", "crop_png", "crop_image"}:
        action = "crop_custom"
    elif action in {"square_crop"}:
        action = "crop_square"
    elif action in {"circle_crop"}:
        action = "crop_circle"
    elif action in {"split_photo"}:
        action = "split_image"
    elif action in {"view_metadata"}:
        action = "metadata_view"
    elif action in {"edit_metadata"}:
        action = "metadata_edit"
    elif action in {"remove_metadata"}:
        action = "metadata_remove"
    elif action in {"blur"}:
        action = "blur_image"
    elif action in {"pixelate"}:
        action = "pixelate_image"
    elif action in {"ai_photo_enhancer"}:
        action = "beautify"
    elif action in {"unblur_face", "unblur_image"}:
        action = "unblur"
    elif action in {"ai_face_generator"}:
        action = "pixel_art"
    elif action in {"blemishes_remover"}:
        action = "retouch"
    elif action in {"add_text_to_image"}:
        action = "add_text"
    elif action in {"add_name_dob"}:
        action = "add_name_dob"
    elif action in {"add_logo_to_image"}:
        action = "add_logo"
    elif action in {"check_dpi"}:
        action = "dpi_check"
    elif action in {"convert_dpi"}:
        action = "dpi_convert"
    elif action in {"super_resolution"}:
        action = "super_resolution"
    elif action in {"upscale_ai"}:
        action = "upscale_ai"
    elif action in {"picture_to_pixel_art"}:
        action = "pixel_art"
    elif action in {"convert_mb_kb", "convert_kb_mb"}:
        action = "size_convert"
    elif action in {"remove_object_photo"}:
        action = "remove_object"
    elif action in {"blur_bg"}:
        action = "blur_background"
    elif action in {"remove_bg"}:
        action = "remove_background"
    elif action in {"merge_photo_signature"}:
        action = "merge_photo_signature"
    elif action in {"join_multiple_images"}:
        action = "join_images"
    elif action in {"images_to_pdf"}:
        action = "image_to_pdf"
    elif action in {"pdf_to_jpg"}:
        action = "pdf_to_images"
        target_format = "jpg"
    elif action in {"image_to_jpg"}:
        action = "convert"
        target_format = "jpg"
    elif action in {"image_to_jpeg"}:
        action = "convert"
        target_format = "jpg"
    elif action in {"jpeg_to_png"}:
        action = "convert"
        target_format = "png"
    elif action in {"png_to_jpeg"}:
        action = "convert"
        target_format = "jpg"
    elif action in {"heic_to_jpg", "webp_to_jpg"}:
        action = "convert"
        target_format = "jpg"
    elif action in {"jpg_to_text", "png_to_text", "ocr_image"}:
        action = "image_to_text"
    elif action in {"jpg_to_pdf_target"}:
        action = "jpg_to_pdf_target"
    elif action in {"passport_photo_maker"}:
        action = "resize_preset"
        if not preset_key:
            preset_key = "passport_photo"
    elif action in {"resize_sign_6x2cm", "signature_50mm_x_20mm"}:
        action = "resize_preset"
        if not preset_key:
            preset_key = "sign_6x2cm"
    elif action in {"size_35x45mm", "photo_35x45"}:
        action = "resize_preset"
        if not preset_key:
            preset_key = "size_35x45mm"
    elif action in {"size_2x2_inch", "photo_2x2"}:
        action = "resize_preset"
        if not preset_key:
            preset_key = "size_2x2_inch"
    elif action in {"size_3x4_inch", "photo_3x4"}:
        action = "resize_preset"
        if not preset_key:
            preset_key = "size_3_4_inch"
    elif action in {"size_4x6_inch", "photo_4x6"}:
        action = "resize_preset"
        if not preset_key:
            preset_key = "size_4_6_inch"
    elif action in {"size_600x600_pixels", "size_600x600"}:
        action = "resize_preset"
        if not preset_key:
            preset_key = "size_600x600"
    elif action in {"instagram_no_crop"}:
        action = "resize_preset"
        if not preset_key:
            preset_key = "instagram_no_crop"
    elif action in {"instagram_grid_maker"}:
        action = "resize_preset"
        if not preset_key:
            preset_key = "instagram_grid"
    elif action in {"whatsapp_dp"}:
        action = "resize_preset"
        if not preset_key:
            preset_key = "whatsapp_dp"
    elif action in {"youtube_banner"}:
        action = "resize_preset"
        if not preset_key:
            preset_key = "youtube_banner"

    if action == "jpg_to_pdf_target":
        action = "image_to_pdf"

    if target_mb > 0:
        target_kb = max(target_kb, int(round(target_mb * 1024)))

    if action in value_only_actions:
        source_name = f"{source_value}{source_unit}"
        image_record = None
        image_records: list[ManagedFile] = []
        pdf_record = None
    else:
        image_record = None
        image_records = []
        pdf_record = None

        if action in single_image_actions or action in signature_actions:
            image_upload = request.files.get("image")
            if image_upload and image_upload.filename:
                image_record = StorageService.save_uploaded_file(image_upload, current_user.id, kind="upload")

        if action in multi_image_actions:
            multi_uploads = [upload for upload in request.files.getlist("images") if upload and upload.filename]
            if multi_uploads:
                image_records = [
                    StorageService.save_uploaded_file(upload, current_user.id, kind="upload")
                    for upload in multi_uploads
                ]

        if action == "image_to_pdf" and not image_records:
            fallback_upload = request.files.get("image")
            if fallback_upload and fallback_upload.filename:
                image_records = [StorageService.save_uploaded_file(fallback_upload, current_user.id, kind="upload")]

        if action in pdf_actions:
            pdf_upload = request.files.get("pdf_document")
            if pdf_upload and pdf_upload.filename:
                pdf_record = StorageService.save_uploaded_file(pdf_upload, current_user.id, kind="upload")

        if action in {"image_to_pdf", "join_images"} and not image_records and image_record:
            image_records = [image_record]

        if action in single_image_actions or action in signature_actions:
            if not image_record:
                raise ValueError("Please upload an image file.")
        if action in multi_image_actions and not image_records:
            raise ValueError("Please upload at least one image file.")
        if action == "join_images" and len(image_records) < 2:
            raise ValueError("Join multiple images requires at least two images.")
        if action in pdf_actions and not pdf_record:
            raise ValueError("Please upload a PDF document.")

        source_name = (
            image_record.original_name if image_record else ", ".join(item.original_name for item in image_records)
        ) or (pdf_record.original_name if pdf_record else action)

    signature_upload = request.files.get("signature_image")
    signature_record = None
    if signature_upload and signature_upload.filename:
        signature_record = StorageService.save_uploaded_file(signature_upload, current_user.id, kind="upload")
    logo_upload = request.files.get("logo_image")
    logo_record = None
    if logo_upload and logo_upload.filename:
        logo_record = StorageService.save_uploaded_file(logo_upload, current_user.id, kind="upload")

    image_path_single = str(StorageService.absolute_path(image_record)) if image_record else ""
    image_paths_multi = [str(StorageService.absolute_path(item)) for item in image_records]
    pdf_path_input = str(StorageService.absolute_path(pdf_record)) if pdf_record else ""
    signature_path = str(StorageService.absolute_path(signature_record)) if signature_record else ""
    logo_path = str(StorageService.absolute_path(logo_record)) if logo_record else ""

    def work(job_id, progress):
        job_dir = _job_dir(job_id, current_user.id)
        progress(28)

        if action == "size_convert":
            converted = ImageService.size_conversion(source_value, source_unit, target_unit)
            output_path = job_dir / "size_conversion.txt"
            output_path.write_text(
                f"{source_value:g} {source_unit.upper()} = {converted:.4f} {target_unit.upper()}",
                encoding="utf-8",
            )
            output_file = _register_output(current_user.id, output_path, output_path.name, "Size conversion")
            progress(85)
            return {"output_file_id": output_file.id, "output_filename": output_file.original_name}

        image_path = image_path_single
        pdf_path = pdf_path_input
        image_paths = image_paths_multi

        if action == "resize_pixel":
            output_path = job_dir / f"resized_{Path(image_path).stem}.png"
            if preset_key:
                ImageService.resize_with_preset(image_path, str(output_path), preset_key)
            else:
                ImageService.resize_pixels(image_path, str(output_path), width=width, height=height)
            output_file = _register_output(current_user.id, output_path, output_path.name, "Resized image")
        elif action == "resize_unit":
            output_path = job_dir / f"resized_{unit}.png"
            ImageService.resize_units(image_path, str(output_path), width=float(width), height=float(height), unit=unit, dpi=dpi)
            output_file = _register_output(current_user.id, output_path, output_path.name, "Resized image")
        elif action == "resize_preset":
            output_path = job_dir / "preset_resized.png"
            key = preset_key or "passport_photo"
            ImageService.resize_with_preset(image_path, str(output_path), key)
            output_file = _register_output(current_user.id, output_path, output_path.name, "Preset resized image")
        elif action == "compress_quality":
            output_path = job_dir / "compressed.jpg"
            details = ImageService.compress_to_quality(image_path, str(output_path), quality=quality)
            output_file = _register_output(current_user.id, output_path, output_path.name, "Compressed image")
            progress(85)
            return {"output_file_id": output_file.id, "output_filename": output_file.original_name, "result_json": details}
        elif action == "compress_target":
            output_path = job_dir / f"compressed_{target_kb}kb.jpg"
            details = ImageService.compress_to_target_kb(image_path, str(output_path), target_kb=target_kb)
            output_file = _register_output(current_user.id, output_path, output_path.name, "Target compressed image")
            progress(85)
            return {"output_file_id": output_file.id, "output_filename": output_file.original_name, "result_json": details}
        elif action == "increase_target":
            output_path = job_dir / f"increased_{target_kb}kb.jpg"
            details = ImageService.increase_to_target_kb(image_path, str(output_path), target_kb=target_kb)
            output_file = _register_output(current_user.id, output_path, output_path.name, "Target increased image")
            progress(85)
            return {"output_file_id": output_file.id, "output_filename": output_file.original_name, "result_json": details}
        elif action == "convert":
            output_path = job_dir / f"converted_image.{target_format}"
            ImageService.convert_image(image_path, str(output_path))
            output_file = _register_output(current_user.id, output_path, output_path.name, "Converted image")
        elif action == "image_to_pdf":
            source_list = image_paths if image_paths else [image_path]
            output_path = job_dir / "images_output.pdf"
            PDFService.images_to_pdf(source_list, str(output_path))
            if target_kb > 0 and "under" in (request.form.get("preset_key", "") or "").lower():
                compressed_pdf = job_dir / "images_output_target.pdf"
                PDFService.compress_pdf_to_target_size(str(output_path), str(compressed_pdf), target_kb=target_kb)
                output_path = compressed_pdf
            output_file = _register_output(current_user.id, output_path, output_path.name, "Images to PDF")
        elif action == "pdf_to_images":
            converted = PDFService.pdf_to_images(pdf_path, str(job_dir / "pages"), image_format=target_format or "jpg")
            archive_path = job_dir / "pdf_to_images.zip"
            _bundle_outputs(converted, archive_path)
            output_file = _register_output(current_user.id, archive_path, archive_path.name, "PDF to images")
        elif action == "image_to_text":
            text_path = job_dir / "image_text.txt"
            OCRService.ocr_image_paths([image_path], str(text_path), lang=current_app.config["OCR_LANG"])
            output_file = _register_output(current_user.id, text_path, text_path.name, "Image OCR text")
        elif action == "rotate":
            output_path = job_dir / "rotated_image.png"
            ImageService.rotate_image(image_path, str(output_path), angle=angle)
            output_file = _register_output(current_user.id, output_path, output_path.name, "Rotated image")
        elif action == "flip":
            output_path = job_dir / "flipped_image.png"
            direction = (request.form.get("flip_direction", "horizontal") or "horizontal").strip().lower()
            ImageService.flip_image(image_path, str(output_path), direction=direction)
            output_file = _register_output(current_user.id, output_path, output_path.name, "Flipped image")
        elif action == "blur_background":
            output_path = job_dir / "blur_background.png"
            ImageService.blur_background(image_path, str(output_path))
            output_file = _register_output(current_user.id, output_path, output_path.name, "Background blurred")
        elif action == "remove_background":
            output_path = job_dir / "remove_background.png"
            ImageService.remove_background(image_path, str(output_path))
            output_file = _register_output(current_user.id, output_path, output_path.name, "Background removed")
        elif action == "remove_object":
            output_path = job_dir / "object_removed.png"
            ImageService.remove_object(image_path, str(output_path), x=crop_x, y=crop_y, width=crop_w, height=crop_h)
            output_file = _register_output(current_user.id, output_path, output_path.name, "Object removed image")
        elif action == "watermark":
            output_path = job_dir / "watermark_image.png"
            ImageService.watermark_text(image_path, str(output_path), text_value or "WATERMARK")
            output_file = _register_output(current_user.id, output_path, output_path.name, "Watermarked image")
        elif action == "crop_custom":
            output_path = job_dir / "cropped_custom.png"
            ImageService.crop_custom(image_path, str(output_path), crop_x, crop_y, crop_w, crop_h)
            output_file = _register_output(current_user.id, output_path, output_path.name, "Cropped image")
        elif action == "crop_square":
            output_path = job_dir / "cropped_square.png"
            ImageService.crop_square(image_path, str(output_path))
            output_file = _register_output(current_user.id, output_path, output_path.name, "Square crop")
        elif action == "crop_circle":
            output_path = job_dir / "cropped_circle.png"
            ImageService.crop_circle(image_path, str(output_path))
            output_file = _register_output(current_user.id, output_path, output_path.name, "Circle crop")
        elif action == "merge_photo_signature":
            if not signature_path:
                raise ValueError("Upload signature image for merge photo & signature.")
            output_path = job_dir / "photo_with_signature.png"
            ImageService.merge_photo_signature(
                image_path,
                signature_path,
                str(output_path),
            )
            output_file = _register_output(current_user.id, output_path, output_path.name, "Photo signature merged")
        elif action == "join_images":
            output_path = job_dir / "joined_images.png"
            source_list = image_paths if image_paths else ([image_path] if image_path else [])
            ImageService.join_images(source_list, str(output_path), direction=line_mode if line_mode in {"vertical", "horizontal"} else "vertical")
            output_file = _register_output(current_user.id, output_path, output_path.name, "Joined images")
        elif action == "split_image":
            split_paths = ImageService.split_image(image_path, str(job_dir / "split_parts"), rows=split_rows, cols=split_cols)
            archive_path = job_dir / "split_image.zip"
            _bundle_outputs(split_paths, archive_path)
            output_file = _register_output(current_user.id, archive_path, archive_path.name, "Split image bundle")
        elif action == "color_picker":
            palette = ImageService.dominant_colors(image_path)
            output_path = job_dir / "dominant_colors.json"
            output_path.write_text(json.dumps({"colors": palette}, indent=2), encoding="utf-8")
            output_file = _register_output(current_user.id, output_path, output_path.name, "Dominant colors")
        elif action == "metadata_view":
            metadata = ImageService.metadata_view(image_path)
            output_path = job_dir / "metadata.json"
            output_path.write_text(json.dumps(metadata, indent=2), encoding="utf-8")
            output_file = _register_output(current_user.id, output_path, output_path.name, "Image metadata")
        elif action == "metadata_edit":
            output_path = job_dir / "metadata_edited.jpg"
            ImageService.metadata_edit(image_path, str(output_path), title=metadata_title, author=metadata_author)
            output_file = _register_output(current_user.id, output_path, output_path.name, "Edited metadata image")
        elif action == "metadata_remove":
            output_path = job_dir / "metadata_removed.jpg"
            ImageService.metadata_remove(image_path, str(output_path))
            output_file = _register_output(current_user.id, output_path, output_path.name, "Metadata removed image")
        elif action == "blur_image":
            output_path = job_dir / "blurred_image.png"
            ImageService.blur_image(image_path, str(output_path), radius=blur_radius)
            output_file = _register_output(current_user.id, output_path, output_path.name, "Blurred image")
        elif action == "pixelate_image":
            output_path = job_dir / "pixelated_image.png"
            ImageService.pixelate_image(image_path, str(output_path), pixel_size=pixel_size)
            output_file = _register_output(current_user.id, output_path, output_path.name, "Pixelated image")
        elif action == "blur_face":
            output_path = job_dir / "face_blur.png"
            ImageService.face_effect(image_path, str(output_path), mode="blur")
            output_file = _register_output(current_user.id, output_path, output_path.name, "Face blurred")
        elif action == "pixelate_face":
            output_path = job_dir / "face_pixelate.png"
            ImageService.face_effect(image_path, str(output_path), mode="pixelate")
            output_file = _register_output(current_user.id, output_path, output_path.name, "Face pixelated")
        elif action == "censor_photo":
            output_path = job_dir / "censored_photo.png"
            ImageService.face_effect(image_path, str(output_path), mode="censor")
            output_file = _register_output(current_user.id, output_path, output_path.name, "Censored photo")
        elif action == "motion_blur":
            output_path = job_dir / "motion_blur.png"
            ImageService.motion_blur_image(image_path, str(output_path), radius=motion_radius)
            output_file = _register_output(current_user.id, output_path, output_path.name, "Motion blur image")
        elif action == "grayscale":
            output_path = job_dir / "grayscale_image.png"
            ImageService.grayscale_image(image_path, str(output_path))
            output_file = _register_output(current_user.id, output_path, output_path.name, "Grayscale image")
        elif action == "black_white":
            output_path = job_dir / "black_white_image.png"
            ImageService.black_white_image(image_path, str(output_path), threshold=bw_threshold)
            output_file = _register_output(current_user.id, output_path, output_path.name, "Black and white image")
        elif action == "beautify":
            output_path = job_dir / "beautified_image.png"
            ImageService.beautify(image_path, str(output_path))
            output_file = _register_output(current_user.id, output_path, output_path.name, "Beautified image")
        elif action == "unblur":
            output_path = job_dir / "unblur_image.png"
            ImageService.unblur(image_path, str(output_path))
            output_file = _register_output(current_user.id, output_path, output_path.name, "Unblur image")
        elif action == "retouch":
            output_path = job_dir / "retouched_image.png"
            ImageService.beautify(image_path, str(output_path))
            output_file = _register_output(current_user.id, output_path, output_path.name, "Retouched image")
        elif action == "add_text":
            output_path = job_dir / "text_added_image.png"
            ImageService.add_text(image_path, str(output_path), text=text_value or "Sample Text", x=x, y=y)
            output_file = _register_output(current_user.id, output_path, output_path.name, "Text overlay image")
        elif action == "add_name_dob":
            output_path = job_dir / "name_dob_image.png"
            payload = " | ".join(part for part in [name_value, dob_value] if part) or "Name | DOB"
            ImageService.add_text(image_path, str(output_path), payload, x=x, y=y)
            output_file = _register_output(current_user.id, output_path, output_path.name, "Name DOB image")
        elif action == "add_logo":
            if not logo_path:
                raise ValueError("Upload logo image to add logo.")
            output_path = job_dir / "logo_added_image.png"
            ImageService.add_logo(
                image_path,
                str(output_path),
                logo_path,
                x=x,
                y=y,
                width=logo_width,
            )
            output_file = _register_output(current_user.id, output_path, output_path.name, "Logo added image")
        elif action == "dpi_check":
            dpi_info = ImageService.check_dpi(image_path)
            output_path = job_dir / "dpi_info.json"
            output_path.write_text(json.dumps(dpi_info, indent=2), encoding="utf-8")
            output_file = _register_output(current_user.id, output_path, output_path.name, "DPI info")
        elif action == "dpi_convert":
            output_path = job_dir / f"dpi_{dpi}.jpg"
            ImageService.convert_dpi(image_path, str(output_path), dpi=dpi)
            output_file = _register_output(current_user.id, output_path, output_path.name, "DPI converted image")
        elif action in {"super_resolution", "upscale_ai"}:
            output_path = job_dir / "upscaled_image.png"
            ImageService.upscale(image_path, str(output_path), scale=upscale_factor)
            output_file = _register_output(current_user.id, output_path, output_path.name, "Upscaled image")
        elif action == "pixel_art":
            output_path = job_dir / "pixel_art.png"
            ImageService.pixel_art(image_path, str(output_path), factor=pixel_art_factor)
            output_file = _register_output(current_user.id, output_path, output_path.name, "Pixel art image")
        else:
            raise ValueError(f"Unsupported image action: {action}")

        progress(85)
        return {"output_file_id": output_file.id, "output_filename": output_file.original_name}

    return JobService.submit_job(current_user, tool, source_name, {"action": action, "preset": preset_key}, work)


def _handle_ocr(tool: ToolCatalog):
    input_file = _save_uploads("document")[0]
    input_path = str(StorageService.absolute_path(input_file))
    ocr_action = request.form.get("ocr_action", "text")

    def work(job_id, progress):
        job_dir = _job_dir(job_id, current_user.id)
        progress(30)
        if ocr_action == "searchable":
            searchable_pdf = job_dir / "searchable_document.pdf"
            output_text = job_dir / "ocr_text.txt"
            OCRService.ocr_pdf_to_searchable(
                input_path,
                str(searchable_pdf),
                str(output_text),
                lang=current_app.config["OCR_LANG"],
            )
            archive_path = job_dir / "ocr_bundle.zip"
            _bundle_outputs([str(searchable_pdf), str(output_text)], archive_path)
            output_file = _register_output(current_user.id, archive_path, "ocr_bundle.zip", "OCR bundle")
        else:
            image_paths = PDFService.pdf_to_images(input_path, str(job_dir / "ocr_images"), image_format="png")
            output_text = job_dir / "ocr_text.txt"
            OCRService.ocr_image_paths(image_paths, str(output_text), lang=current_app.config["OCR_LANG"])
            output_file = _register_output(current_user.id, output_text, "ocr_text.txt", "OCR text output")
        progress(85)
        return {"output_file_id": output_file.id, "output_filename": output_file.original_name}

    return JobService.submit_job(current_user, tool, input_file.original_name, {"ocr_action": ocr_action}, work)


def _handle_student_mode(tool: ToolCatalog):
    input_file = _save_uploads("document")[0]
    action = request.form.get("student_action", "summary")
    highlight_query = request.form.get("highlight_query", "")
    input_path = str(StorageService.absolute_path(input_file))

    def work(job_id, progress):
        job_dir = _job_dir(job_id, current_user.id)
        progress(25)
        if action == "highlight":
            if not highlight_query:
                raise ValueError("Enter text to highlight.")
            output_path = job_dir / "highlighted_notes.pdf"
            PDFService.highlight_text(input_path, str(output_path), highlight_query)
            output_file = _register_output(current_user.id, output_path, "highlighted_notes.pdf", "Highlighted notes")
            return {"output_file_id": output_file.id, "output_filename": output_file.original_name}

        text = _extract_pdf_text(input_path, job_dir)
        if action == "summary":
            payload = AIDocumentService.summarize_text(text)
            output_path = job_dir / "summary.txt"
            output_path.write_text(payload, encoding="utf-8")
        elif action == "notes":
            payload = "\n".join(f"- {item}" for item in AIDocumentService.generate_notes(text))
            output_path = job_dir / "notes.txt"
            output_path.write_text(payload, encoding="utf-8")
        elif action == "flashcards":
            payload = json.dumps(AIDocumentService.generate_flashcards(text), indent=2)
            output_path = job_dir / "flashcards.json"
            output_path.write_text(payload, encoding="utf-8")
        else:
            payload = json.dumps(AIDocumentService.generate_quiz(text), indent=2)
            output_path = job_dir / "quiz.json"
            output_path.write_text(payload, encoding="utf-8")
        progress(85)
        output_file = _register_output(current_user.id, output_path, output_path.name, "Student mode output")
        return {"output_file_id": output_file.id, "output_filename": output_file.original_name}

    return JobService.submit_job(current_user, tool, input_file.original_name, {"student_action": action}, work)


def _handle_study_pack_pro(tool: ToolCatalog):
    input_file = _save_uploads("document")[0]
    flashcard_limit = _parse_int(
        request.form.get("flashcard_limit"),
        field_name="flashcard limit",
        default=12,
        minimum=4,
        maximum=40,
    )
    quiz_limit = _parse_int(
        request.form.get("quiz_limit"),
        field_name="quiz limit",
        default=10,
        minimum=3,
        maximum=30,
    )
    revision_days = _parse_int(
        request.form.get("revision_days"),
        field_name="revision days",
        default=7,
        minimum=1,
        maximum=30,
    )
    input_path = str(StorageService.absolute_path(input_file))

    def work(job_id, progress):
        job_dir = _job_dir(job_id, current_user.id)
        progress(20)
        text = _extract_pdf_text(input_path, job_dir)
        progress(45)
        study_pack = EducationService.build_study_pack(
            text=text,
            flashcard_limit=flashcard_limit,
            quiz_limit=quiz_limit,
            revision_days=revision_days,
        )
        summary_path = _write_text_file(job_dir / "summary.txt", study_pack["summary"])
        notes_path = _write_text_file(job_dir / "notes.txt", "\n".join(study_pack["notes"]))
        formulas_path = _write_text_file(
            job_dir / "formula_sheet.txt",
            "\n".join(study_pack["formula_sheet"]) or "No formula-like lines detected.",
        )
        revision_path = _write_text_file(
            job_dir / "revision_plan.txt",
            "\n".join(study_pack["revision_plan"]),
        )
        keywords_path = job_dir / "keywords.json"
        keywords_path.write_text(
            json.dumps(study_pack["keywords"], indent=2),
            encoding="utf-8",
        )
        flashcards_path = job_dir / "flashcards.json"
        flashcards_path.write_text(
            json.dumps(study_pack["flashcards"], indent=2),
            encoding="utf-8",
        )
        quiz_path = job_dir / "quiz.json"
        quiz_path.write_text(
            json.dumps(study_pack["quiz"], indent=2),
            encoding="utf-8",
        )
        progress(80)
        bundle_path = job_dir / "study_pack_pro_bundle.zip"
        _bundle_outputs(
            [
                str(summary_path),
                str(notes_path),
                str(formulas_path),
                str(revision_path),
                str(keywords_path),
                str(flashcards_path),
                str(quiz_path),
            ],
            bundle_path,
        )
        output_file = _register_output(
            current_user.id,
            bundle_path,
            "study_pack_pro_bundle.zip",
            "Study Pack Pro bundle",
        )
        return {
            "output_file_id": output_file.id,
            "output_filename": output_file.original_name,
            "result_json": {
                "flashcards": len(study_pack["flashcards"]),
                "quiz_questions": len(study_pack["quiz"]),
                "keywords": len(study_pack["keywords"]),
            },
        }

    return JobService.submit_job(
        current_user,
        tool,
        input_file.original_name,
        {
            "flashcard_limit": flashcard_limit,
            "quiz_limit": quiz_limit,
            "revision_days": revision_days,
        },
        work,
    )


def _handle_teacher_toolkit(tool: ToolCatalog):
    input_file = _save_uploads("document")[0]
    objective_count = _parse_int(
        request.form.get("objective_count"),
        field_name="objective questions",
        default=10,
        minimum=5,
        maximum=40,
    )
    subjective_count = _parse_int(
        request.form.get("subjective_count"),
        field_name="subjective questions",
        default=5,
        minimum=2,
        maximum=20,
    )
    total_marks = _parse_int(
        request.form.get("total_marks"),
        field_name="total marks",
        default=100,
        minimum=20,
        maximum=300,
    )
    class_duration = _parse_int(
        request.form.get("class_duration"),
        field_name="class duration",
        default=45,
        minimum=20,
        maximum=180,
    )
    input_path = str(StorageService.absolute_path(input_file))

    def work(job_id, progress):
        job_dir = _job_dir(job_id, current_user.id)
        progress(25)
        text = _extract_pdf_text(input_path, job_dir)
        progress(50)
        toolkit = EducationService.build_teacher_toolkit(
            text=text,
            objective_count=objective_count,
            subjective_count=subjective_count,
            total_marks=total_marks,
            class_duration_minutes=class_duration,
        )
        question_paper_path = _write_text_file(
            job_dir / "question_paper.txt", toolkit["question_paper"]
        )
        answer_key_path = _write_text_file(job_dir / "answer_key.txt", toolkit["answer_key"])
        rubric_path = _write_text_file(job_dir / "rubric.txt", toolkit["rubric"])
        lesson_plan_path = _write_text_file(job_dir / "lesson_plan.txt", toolkit["lesson_plan"])
        progress(82)
        bundle_path = job_dir / "teacher_toolkit_bundle.zip"
        _bundle_outputs(
            [
                str(question_paper_path),
                str(answer_key_path),
                str(rubric_path),
                str(lesson_plan_path),
            ],
            bundle_path,
        )
        output_file = _register_output(
            current_user.id,
            bundle_path,
            "teacher_toolkit_bundle.zip",
            "Teacher toolkit bundle",
        )
        return {
            "output_file_id": output_file.id,
            "output_filename": output_file.original_name,
            "result_json": {
                "objective_questions": objective_count,
                "subjective_questions": subjective_count,
                "total_marks": total_marks,
            },
        }

    return JobService.submit_job(
        current_user,
        tool,
        input_file.original_name,
        {
            "objective_count": objective_count,
            "subjective_count": subjective_count,
            "total_marks": total_marks,
            "class_duration": class_duration,
        },
        work,
    )


def _handle_government_office_suite(tool: ToolCatalog):
    gov_action = request.form.get("gov_action", "office_memo")
    payload = {
        "department": request.form.get("department", ""),
        "reference_no": request.form.get("reference_no", ""),
        "subject": request.form.get("subject", ""),
        "recipient": request.form.get("recipient", ""),
        "signatory": request.form.get("signatory", ""),
        "points": request.form.get("points", ""),
    }

    def work(job_id, progress):
        job_dir = _job_dir(job_id, current_user.id)
        progress(30)
        draft = GovernmentService.build_document(gov_action, payload)
        text_path = _write_text_file(
            job_dir / f"{draft['filename_prefix']}.txt",
            draft["text"],
        )
        progress(60)
        pdf_path = Path(
            GovernmentService.render_pdf(
                title=draft["title"],
                text=draft["text"],
                output_path=job_dir / f"{draft['filename_prefix']}.pdf",
            )
        )
        progress(82)
        bundle_path = job_dir / f"{draft['filename_prefix']}_bundle.zip"
        _bundle_outputs([str(text_path), str(pdf_path)], bundle_path)
        output_file = _register_output(
            current_user.id,
            bundle_path,
            bundle_path.name,
            "Government office document bundle",
        )
        return {
            "output_file_id": output_file.id,
            "output_filename": output_file.original_name,
            "result_json": {"action": gov_action, "title": draft["title"]},
        }

    return JobService.submit_job(
        current_user,
        tool,
        input_filename=payload.get("subject", gov_action) or gov_action,
        options={"gov_action": gov_action, "reference_no": payload.get("reference_no", "")},
        work_fn=work,
    )


def _handle_smart_pdf_pipeline(tool: ToolCatalog):
    input_file = _save_uploads("document")[0]
    rotate_angle = _parse_int(
        request.form.get("rotate_angle"),
        field_name="rotate angle",
        default=0,
        minimum=-270,
        maximum=270,
    )
    watermark_text = (request.form.get("watermark_text") or "").strip()
    watermark_position = request.form.get("watermark_position", "diagonal")
    add_page_numbers = _is_checked("add_page_numbers")
    include_text_extract = _is_checked("include_text_extract")
    compress_level = request.form.get("compress_level", "none").strip().lower()
    protect_password = (request.form.get("protect_password") or "").strip()

    if rotate_angle % 90 != 0:
        raise ValueError("Rotate angle should be a multiple of 90.")
    if compress_level not in {"none", "low", "balanced", "strong"}:
        raise ValueError("Invalid compression level selected.")
    if watermark_position not in {
        "top_left",
        "top_right",
        "bottom_left",
        "bottom_center",
        "center",
        "diagonal",
    }:
        watermark_position = "diagonal"
    if not any(
        [
            rotate_angle,
            watermark_text,
            add_page_numbers,
            include_text_extract,
            compress_level != "none",
            protect_password,
        ]
    ):
        raise ValueError("Choose at least one pipeline step.")

    input_path = str(StorageService.absolute_path(input_file))

    def work(job_id, progress):
        job_dir = _job_dir(job_id, current_user.id)
        current_path = input_path
        steps = []
        attachments: list[str] = []
        progress(20)

        if rotate_angle:
            rotated_path = job_dir / "step_rotated.pdf"
            PDFService.rotate_pdf(current_path, str(rotated_path), "all", rotate_angle)
            current_path = str(rotated_path)
            steps.append(f"Rotated pages by {rotate_angle} degrees")

        if watermark_text:
            watermark_path = job_dir / "step_watermarked.pdf"
            PDFService.add_text_watermark(
                current_path,
                str(watermark_path),
                watermark_text,
                opacity=0.22,
                position=watermark_position,
            )
            current_path = str(watermark_path)
            steps.append("Applied text watermark")

        if add_page_numbers:
            numbered_path = job_dir / "step_numbered.pdf"
            PDFService.add_page_numbers(current_path, str(numbered_path))
            current_path = str(numbered_path)
            steps.append("Added page numbers")

        compression_result = {}
        if compress_level != "none":
            compressed_path = job_dir / "step_compressed.pdf"
            compression_result = PDFService.compress_pdf(
                current_path, str(compressed_path), level=compress_level
            )
            current_path = str(compressed_path)
            steps.append(f"Compressed PDF ({compress_level})")

        if protect_password:
            protected_path = job_dir / "step_protected.pdf"
            PDFService.protect_pdf(current_path, str(protected_path), protect_password)
            current_path = str(protected_path)
            steps.append("Applied password protection")

        if include_text_extract:
            text_path = job_dir / "pipeline_text_extract.txt"
            PDFService.pdf_to_text(current_path, str(text_path))
            attachments.append(str(text_path))
            steps.append("Extracted text")

        progress(78)
        report = {
            "steps": steps,
            "compression": compression_result,
            "final_pdf": Path(current_path).name,
        }
        report_path = job_dir / "pipeline_report.json"
        report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
        bundle_path = job_dir / "smart_pipeline_bundle.zip"
        _bundle_outputs([current_path, str(report_path), *attachments], bundle_path)
        output_file = _register_output(
            current_user.id,
            bundle_path,
            "smart_pipeline_bundle.zip",
            "Smart PDF pipeline bundle",
        )
        return {
            "output_file_id": output_file.id,
            "output_filename": output_file.original_name,
            "result_json": report,
        }

    return JobService.submit_job(
        current_user,
        tool,
        input_filename=input_file.original_name,
        options={
            "rotate_angle": rotate_angle,
            "watermark": bool(watermark_text),
            "page_numbers": add_page_numbers,
            "compress_level": compress_level,
            "text_extract": include_text_extract,
            "protect": bool(protect_password),
        },
        work_fn=work,
    )


def _handle_office_mode(tool: ToolCatalog):
    action = request.form.get("office_action", "contract_extract")
    stamp_text = request.form.get("stamp_text", "APPROVED")
    keywords = [keyword.strip() for keyword in request.form.get("keywords", "").split(",")]
    pages_per_invoice = int(request.form.get("pages_per_invoice", "1"))
    uploaded_documents = _save_uploads("documents", multiple=True) if action == "bulk_rename" else []
    input_file = _save_uploads("document")[0] if action != "bulk_rename" else None
    input_path = str(StorageService.absolute_path(input_file)) if input_file else ""

    def work(job_id, progress):
        job_dir = _job_dir(job_id, current_user.id)
        progress(25)
        if action == "contract_extract":
            output_path = job_dir / "contract_pages.pdf"
            PDFService.extract_pages_by_keywords(input_path, str(output_path), keywords)
            output_file = _register_output(current_user.id, output_path, "contract_pages.pdf", "Contract pages")
        elif action == "invoice_split":
            output_paths = PDFService.split_pdf(input_path, str(job_dir / "split"), mode="every", every_n=pages_per_invoice)
            archive_path = job_dir / "invoice_packets.zip"
            _bundle_outputs(output_paths, archive_path)
            output_file = _register_output(current_user.id, archive_path, "invoice_packets.zip", "Invoice packet bundle")
        elif action == "stamp":
            output_path = job_dir / "stamped_document.pdf"
            PDFService.stamp_pdf(input_path, str(output_path), stamp_text)
            output_file = _register_output(current_user.id, output_path, "stamped_document.pdf", "Stamped PDF")
        else:
            prefix = request.form.get("rename_prefix", "office-file")
            staged_paths = []
            for index, file_record in enumerate(uploaded_documents, start=1):
                source_path = StorageService.absolute_path(file_record)
                target_path = job_dir / f"{prefix}-{index:03d}{Path(file_record.original_name).suffix.lower()}"
                shutil.copy2(source_path, target_path)
                staged_paths.append(str(target_path))
            archive_path = job_dir / "renamed_documents.zip"
            _bundle_outputs(staged_paths, archive_path)
            output_file = _register_output(current_user.id, archive_path, "renamed_documents.zip", "Bulk renamed documents")
        progress(85)
        return {"output_file_id": output_file.id, "output_filename": output_file.original_name}

    input_name = input_file.original_name if input_file else ", ".join(file.original_name for file in uploaded_documents)
    return JobService.submit_job(current_user, tool, input_name, {"office_action": action}, work)


def _handle_ai_tools(tool: ToolCatalog):
    input_file = _save_uploads("document")[0]
    input_name = input_file.original_name
    action = "translate" if tool.tool_key == "translate_pdf" else request.form.get("ai_action", "summary")
    target_language_raw = request.form.get("target_language", "hi")
    target_language = AIDocumentService.normalize_language_code(target_language_raw)
    input_path = str(StorageService.absolute_path(input_file))

    def work(job_id, progress):
        job_dir = _job_dir(job_id, current_user.id)
        progress(25)
        text = _extract_ai_input_text(input_name, input_path, job_dir)
        if action == "summary":
            payload = AIDocumentService.summarize_text(text)
            output_path = job_dir / "ai_summary.txt"
            output_path.write_text(payload, encoding="utf-8")
        elif action == "keywords":
            payload = json.dumps(AIDocumentService.extract_keywords(text), indent=2)
            output_path = job_dir / "keywords.json"
            output_path.write_text(payload, encoding="utf-8")
        elif action == "notes":
            payload = "\n".join(AIDocumentService.generate_notes(text))
            output_path = job_dir / "notes.txt"
            output_path.write_text(payload, encoding="utf-8")
        else:
            translated = AIDocumentService.translate_text(text, target_language)
            output_path = job_dir / f"translated_{target_language}.txt"
            output_path.write_text(translated, encoding="utf-8")
        progress(85)
        output_file = _register_output(current_user.id, output_path, output_path.name, "AI document output")
        return {"output_file_id": output_file.id, "output_filename": output_file.original_name}

    return JobService.submit_job(
        current_user,
        tool,
        input_name,
        {"ai_action": action, "target_language": target_language},
        work,
    )


def _handle_document_templates(tool: ToolCatalog):
    template_key = request.form.get("template_key", "resume")
    payload = {
        key: value
        for key, value in request.form.items()
        if key not in {"csrf_token", "template_key"} and value.strip()
    }

    def work(job_id, progress):
        job_dir = _job_dir(job_id, current_user.id)
        progress(35)
        output_path = job_dir / f"{template_key}.pdf"
        TemplateService.generate_document(template_key, payload, output_path)
        progress(85)
        output_file = _register_output(current_user.id, output_path, f"{template_key}.pdf", "Template document")
        return {"output_file_id": output_file.id, "output_filename": output_file.original_name}

    return JobService.submit_job(current_user, tool, template_key, {"template_key": template_key}, work)

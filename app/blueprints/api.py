from __future__ import annotations

import time
from pathlib import Path

from flask import Blueprint, jsonify, request, send_file

from app.extensions import csrf
from app.models import Job
from app.services import APIAutomationService, JobService, PDFService, StorageService

api_bp = Blueprint("api", __name__, url_prefix="/api/v1")


def _status_for_api_error(message: str) -> int:
    lowered = (message or "").lower()
    if "rate limit" in lowered:
        return 429
    if "missing" in lowered or "invalid" in lowered or "expired" in lowered:
        return 401
    return 400


def _authenticate():
    return APIAutomationService.authenticate_request(
        raw_authorization=request.headers.get("Authorization", ""),
        raw_header_key=request.headers.get("X-API-Key", ""),
    )


@api_bp.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "ok"})


@api_bp.route("/keys/me", methods=["GET"])
def key_info():
    started = time.perf_counter()
    api_key = None
    status_code = 200
    try:
        api_key = _authenticate()
        payload = {
            "key_id": api_key.id,
            "name": api_key.name,
            "key_prefix": api_key.key_prefix,
            "rate_limit_per_minute": api_key.rate_limit_per_minute,
            "is_active": api_key.is_active,
            "expires_at": api_key.expires_at.isoformat() if api_key.expires_at else None,
        }
        return jsonify(payload), status_code
    except Exception as exc:
        status_code = _status_for_api_error(str(exc))
        return jsonify({"error": str(exc)}), status_code
    finally:
        APIAutomationService.log_usage(
            api_key=api_key,
            user_id=api_key.user_id if api_key else None,
            endpoint="/api/v1/keys/me",
            method="GET",
            status_code=status_code,
            response_ms=int((time.perf_counter() - started) * 1000),
            ip_address=request.headers.get("X-Forwarded-For", request.remote_addr or ""),
        )


@api_bp.route("/jobs/compress", methods=["POST"])
@csrf.exempt
def api_job_compress():
    started = time.perf_counter()
    api_key = None
    status_code = 200
    try:
        api_key = _authenticate()
        upload = request.files.get("document")
        if not upload or not upload.filename:
            raise ValueError("Please upload a PDF file in field 'document'.")
        file_record = StorageService.save_uploaded_file(upload, api_key.user_id, kind="upload")
        source_path = StorageService.absolute_path(file_record)
        if source_path.suffix.lower() != ".pdf":
            raise ValueError("Only PDF files are supported for this API endpoint.")

        level = (request.form.get("compress_level") or "balanced").strip().lower()
        job = JobService.create_job(
            user_id=api_key.user_id,
            tool_key="api_compress",
            input_filename=file_record.original_name,
            options={"compress_level": level},
        )
        JobService.update_job(job.id, status="processing", progress=20)

        output_dir = Path(source_path.parent.parent) / "api_jobs" / str(job.id)
        output_dir.mkdir(parents=True, exist_ok=True)
        output_path = output_dir / f"{source_path.stem}_compressed.pdf"
        PDFService.compress_pdf(str(source_path), str(output_path), level=level)

        output_file = StorageService.register_existing_file(
            absolute_path=output_path,
            user_id=api_key.user_id,
            kind="output",
            original_name=output_path.name,
            label="API compressed output",
        )
        JobService.update_job(
            job.id,
            status="completed",
            progress=100,
            output_filename=output_file.original_name,
            output_file_id=output_file.id,
            result_json={"compress_level": level},
            error_message="",
        )
        APIAutomationService.dispatch_user_event(
            user_id=api_key.user_id,
            event_name="job.completed",
            payload={
                "job_id": job.id,
                "tool_key": "api_compress",
                "status": "completed",
                "output_file_id": output_file.id,
            },
        )
        return (
            jsonify(
                {
                    "job_id": job.id,
                    "status": "completed",
                    "output_file_id": output_file.id,
                    "output_filename": output_file.original_name,
                    "job_status_url": f"/api/v1/jobs/{job.id}",
                    "download_url": f"/api/v1/jobs/{job.id}/download",
                }
            ),
            status_code,
        )
    except Exception as exc:
        status_code = _status_for_api_error(str(exc))
        return jsonify({"error": str(exc)}), status_code
    finally:
        APIAutomationService.log_usage(
            api_key=api_key,
            user_id=api_key.user_id if api_key else None,
            endpoint="/api/v1/jobs/compress",
            method="POST",
            status_code=status_code,
            response_ms=int((time.perf_counter() - started) * 1000),
            ip_address=request.headers.get("X-Forwarded-For", request.remote_addr or ""),
        )


@api_bp.route("/jobs/<int:job_id>", methods=["GET"])
def api_job_status(job_id: int):
    started = time.perf_counter()
    api_key = None
    status_code = 200
    try:
        api_key = _authenticate()
        job = Job.query.filter_by(id=job_id, user_id=api_key.user_id).first()
        if not job:
            raise ValueError("Job not found.")
        return (
            jsonify(
                {
                    "job_id": job.id,
                    "tool_key": job.tool_key,
                    "status": job.status,
                    "progress": job.progress,
                    "error_message": job.error_message,
                    "output_file_id": job.output_file_id,
                }
            ),
            status_code,
        )
    except Exception as exc:
        status_code = _status_for_api_error(str(exc))
        return jsonify({"error": str(exc)}), status_code
    finally:
        APIAutomationService.log_usage(
            api_key=api_key,
            user_id=api_key.user_id if api_key else None,
            endpoint=f"/api/v1/jobs/{job_id}",
            method="GET",
            status_code=status_code,
            response_ms=int((time.perf_counter() - started) * 1000),
            ip_address=request.headers.get("X-Forwarded-For", request.remote_addr or ""),
        )


@api_bp.route("/jobs/<int:job_id>/download", methods=["GET"])
def api_job_download(job_id: int):
    started = time.perf_counter()
    api_key = None
    status_code = 200
    try:
        api_key = _authenticate()
        job = Job.query.filter_by(id=job_id, user_id=api_key.user_id).first()
        if not job or not job.output_file_id or not job.output_file:
            raise ValueError("Job output not available.")
        output_file = StorageService.absolute_path(job.output_file)
        return send_file(
            output_file,
            as_attachment=True,
            download_name=job.output_file.original_name,
            mimetype=job.output_file.mime_type,
        )
    except Exception as exc:
        status_code = _status_for_api_error(str(exc))
        return jsonify({"error": str(exc)}), status_code
    finally:
        APIAutomationService.log_usage(
            api_key=api_key,
            user_id=api_key.user_id if api_key else None,
            endpoint=f"/api/v1/jobs/{job_id}/download",
            method="GET",
            status_code=status_code,
            response_ms=int((time.perf_counter() - started) * 1000),
            ip_address=request.headers.get("X-Forwarded-For", request.remote_addr or ""),
        )


@api_bp.route("/webhooks/test", methods=["POST"])
@csrf.exempt
def api_webhook_test():
    started = time.perf_counter()
    api_key = None
    status_code = 200
    try:
        api_key = _authenticate()
        payload = request.get_json(silent=True) or {}
        APIAutomationService.dispatch_user_event(
            user_id=api_key.user_id,
            event_name="job.completed",
            payload={
                "type": "webhook.test",
                "triggered_at": started,
                "payload": payload,
            },
        )
        return jsonify({"status": "sent"}), status_code
    except Exception as exc:
        status_code = _status_for_api_error(str(exc))
        return jsonify({"error": str(exc)}), status_code
    finally:
        APIAutomationService.log_usage(
            api_key=api_key,
            user_id=api_key.user_id if api_key else None,
            endpoint="/api/v1/webhooks/test",
            method="POST",
            status_code=status_code,
            response_ms=int((time.perf_counter() - started) * 1000),
            ip_address=request.headers.get("X-Forwarded-For", request.remote_addr or ""),
        )

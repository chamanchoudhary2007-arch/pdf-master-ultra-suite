from __future__ import annotations

import json
from datetime import timedelta

from flask import (
    Blueprint,
    abort,
    current_app,
    flash,
    redirect,
    render_template,
    request,
    send_file,
    url_for,
)
from flask_login import current_user, login_required

from app.models import (
    ApiUsageLog,
    BulkBatch,
    BulkBatchItem,
    DocumentChatMessage,
    DocumentChatSession,
    DocumentVersion,
    FormSubmission,
    FormTemplate,
    FormTemplateField,
    ManagedFile,
    SignatureField,
    SignatureRequest,
    SignatureSigner,
    TeamProject,
    TeamWorkspace,
    TeamWorkspaceMember,
    utcnow,
)
from app.services import (
    APIAutomationService,
    AdvancedOCRService,
    BulkService,
    CloudIntegrationService,
    ComplianceService,
    DocumentChatService,
    FormBuilderService,
    PrivacyService,
    SignatureRequestService,
    TeamWorkspaceService,
    VersioningService,
)
from app.services.storage_service import StorageService

workspace_bp = Blueprint("workspace", __name__, url_prefix="/workspace")


def _is_checked(name: str) -> bool:
    return (request.form.get(name, "") or "").strip().lower() in {"1", "true", "yes", "on"}


def _parse_int(
    value: str | None,
    default: int,
    minimum: int | None = None,
    maximum: int | None = None,
) -> int:
    try:
        parsed = int((value or "").strip() or default)
    except Exception:
        parsed = default
    if minimum is not None:
        parsed = max(minimum, parsed)
    if maximum is not None:
        parsed = min(maximum, parsed)
    return parsed


def _parse_json(value: str | None, default):
    payload = (value or "").strip()
    if not payload:
        return default
    try:
        return json.loads(payload)
    except Exception as exc:
        raise ValueError("Invalid JSON input.") from exc


@workspace_bp.route("/")
@login_required
def index():
    summary = {
        "chat_sessions": DocumentChatSession.query.filter_by(user_id=current_user.id).count(),
        "bulk_batches": BulkBatch.query.filter_by(user_id=current_user.id).count(),
        "signature_requests": SignatureRequest.query.filter_by(requester_id=current_user.id).count(),
        "form_templates": FormTemplate.query.filter_by(owner_id=current_user.id).count(),
        "workspace_count": TeamWorkspace.query.filter_by(owner_id=current_user.id).count(),
    }
    return render_template("workspace/index.html", summary=summary)


@workspace_bp.route("/chat-with-pdf", methods=["GET", "POST"])
@login_required
def chat_with_pdf():
    active_session_id = _parse_int(request.args.get("session"), 0)

    if request.method == "POST":
        action = (request.form.get("action") or "ask").strip().lower()
        try:
            if action == "upload":
                upload = request.files.get("document")
                if not upload or not upload.filename:
                    raise ValueError("Please upload a PDF document.")
                source_file = StorageService.save_uploaded_file(upload, current_user.id, kind="upload")
                session = DocumentChatService.create_session(
                    user_id=current_user.id,
                    source_file=source_file,
                    title=request.form.get("title", ""),
                )
                flash("Document indexed for chat successfully.", "success")
                return redirect(url_for("workspace.chat_with_pdf", session=session.id))

            if action == "clear":
                session_id = _parse_int(request.form.get("session_id"), 0, minimum=1)
                DocumentChatService.clear_conversation(session_id, current_user.id)
                flash("Conversation cleared.", "success")
                return redirect(url_for("workspace.chat_with_pdf", session=session_id))

            session_id = _parse_int(request.form.get("session_id"), 0, minimum=1)
            question = request.form.get("question", "")
            DocumentChatService.ask_question(
                session_id=session_id,
                user_id=current_user.id,
                question=question,
            )
            return redirect(url_for("workspace.chat_with_pdf", session=session_id))
        except Exception as exc:
            flash(str(exc), "danger")

    sessions = DocumentChatService.list_sessions(current_user.id)
    active_session = None
    messages: list[DocumentChatMessage] = []
    if active_session_id:
        try:
            active_session = DocumentChatService.get_session_for_user(active_session_id, current_user.id)
            messages = (
                DocumentChatMessage.query.filter_by(session_id=active_session.id)
                .order_by(DocumentChatMessage.created_at.asc())
                .all()
            )
        except Exception:
            active_session = None

    return render_template(
        "workspace/chat_with_pdf.html",
        sessions=sessions,
        active_session=active_session,
        messages=messages,
        suggested_questions=DocumentChatService.SUGGESTED_QUESTIONS,
    )


@workspace_bp.route("/bulk-processing", methods=["GET", "POST"])
@login_required
def bulk_processing():
    if request.method == "POST":
        try:
            tool_key = (request.form.get("tool_key") or "compress").strip().lower()
            uploads = [upload for upload in request.files.getlist("documents") if upload and upload.filename]
            if not uploads:
                raise ValueError("Please upload one or more files.")
            files = [
                StorageService.save_uploaded_file(upload, current_user.id, kind="upload")
                for upload in uploads
            ]
            options = {
                "compress_level": request.form.get("compress_level", "balanced"),
                "watermark_text": request.form.get("watermark_text", ""),
                "watermark_position": request.form.get("watermark_position", "diagonal"),
                "target_format": request.form.get("target_format", "txt"),
                "rename_prefix": request.form.get("rename_prefix", "batch"),
                "ocr_lang": request.form.get("ocr_lang", current_app.config.get("OCR_LANG", "eng")),
                "ocr_output": request.form.get("ocr_output", "txt"),
            }
            batch = BulkService.submit_batch(
                user_id=current_user.id,
                tool_key=tool_key,
                files=files,
                options=options,
                name=request.form.get("batch_name", ""),
            )
            flash("Bulk batch queued. Track status on the batch page.", "success")
            return redirect(url_for("workspace.bulk_batch_detail", batch_id=batch.id))
        except Exception as exc:
            flash(str(exc), "danger")

    batches = BulkService.list_batches_for_user(current_user.id)
    return render_template("workspace/bulk_processing.html", batches=batches)


@workspace_bp.route("/bulk-processing/<int:batch_id>")
@login_required
def bulk_batch_detail(batch_id: int):
    try:
        batch = BulkService.get_batch_for_user(batch_id, current_user.id)
    except Exception:
        abort(404)

    items = (
        BulkBatchItem.query.filter_by(batch_id=batch.id)
        .order_by(BulkBatchItem.sequence_index.asc())
        .all()
    )
    return render_template("workspace/bulk_batch_detail.html", batch=batch, items=items)


@workspace_bp.route("/bulk-processing/<int:batch_id>/download")
@login_required
def bulk_batch_download(batch_id: int):
    try:
        batch = BulkService.get_batch_for_user(batch_id, current_user.id)
        if not batch.output_archive_file_id:
            raise ValueError("Batch archive is not available yet.")
        archive_file = ManagedFile.query.filter_by(
            id=batch.output_archive_file_id,
            user_id=current_user.id,
            is_deleted=False,
        ).first()
        if not archive_file:
            raise ValueError("Batch archive not found.")
        absolute = StorageService.absolute_path(archive_file)
        return send_file(
            absolute,
            as_attachment=True,
            download_name=archive_file.original_name,
            mimetype=archive_file.mime_type,
        )
    except Exception as exc:
        flash(str(exc), "warning")
        return redirect(url_for("workspace.bulk_batch_detail", batch_id=batch_id))


@workspace_bp.route("/signatures", methods=["GET", "POST"])
@login_required
def signatures():
    if request.method == "POST":
        action = (request.form.get("action") or "create").strip().lower()
        try:
            if action == "create":
                upload = request.files.get("document")
                if not upload or not upload.filename:
                    raise ValueError("Please upload a PDF for signature request.")
                source_file = StorageService.save_uploaded_file(upload, current_user.id, kind="upload")

                raw_signers = (request.form.get("signers") or "").splitlines()
                signers = []
                for index, line in enumerate(raw_signers, start=1):
                    chunk = line.strip()
                    if not chunk:
                        continue
                    if "|" in chunk:
                        parts = [part.strip() for part in chunk.split("|")]
                        email = parts[0]
                        name = parts[1] if len(parts) > 1 else email.split("@", 1)[0]
                        order = _parse_int(parts[2] if len(parts) > 2 else str(index), index, minimum=1)
                    else:
                        email = chunk
                        name = email.split("@", 1)[0]
                        order = index
                    signers.append({"email": email, "name": name, "order": order})
                if not signers:
                    raise ValueError("Add at least one signer in the signers box.")

                fields = _parse_json(request.form.get("fields_json"), [])
                if not fields:
                    fields = [
                        {
                            "signer_id": None,
                            "page": 1,
                            "field_type": "signature",
                            "label": "Signer signature",
                            "x": 72,
                            "y": 120,
                            "width": 170,
                            "height": 36,
                            "required": True,
                        }
                    ]

                expires_days = _parse_int(request.form.get("expires_days"), 7, minimum=1, maximum=60)
                expires_at = None
                if not _is_checked("never_expires"):
                    expires_at = utcnow() + timedelta(days=expires_days)
                signature_request = SignatureRequestService.create_request(
                    requester_id=current_user.id,
                    file_id=source_file.id,
                    title=request.form.get("title", "Signature Request"),
                    message=request.form.get("message", ""),
                    expires_at=expires_at,
                    signers=signers,
                    fields=fields,
                    signer_order_enforced=_is_checked("signer_order_enforced"),
                )

                if _is_checked("send_now"):
                    SignatureRequestService.send_request(
                        request_id=signature_request.id,
                        requester_id=current_user.id,
                        access_url_builder=lambda token, email: url_for(
                            "workspace.signature_access",
                            token=token,
                            email=email,
                            _external=True,
                        ),
                    )
                    flash("Signature request created and sent.", "success")
                else:
                    flash("Signature request saved as draft.", "success")

            elif action == "send":
                request_id = _parse_int(request.form.get("request_id"), 0, minimum=1)
                SignatureRequestService.send_request(
                    request_id=request_id,
                    requester_id=current_user.id,
                    access_url_builder=lambda token, email: url_for(
                        "workspace.signature_access",
                        token=token,
                        email=email,
                        _external=True,
                    ),
                )
                flash("Signature request sent.", "success")

            elif action == "reminder":
                request_id = _parse_int(request.form.get("request_id"), 0, minimum=1)
                signer_id = _parse_int(request.form.get("signer_id"), 0, minimum=1)
                SignatureRequestService.send_reminder(
                    request_id=request_id,
                    requester_id=current_user.id,
                    signer_id=signer_id,
                    access_url_builder=lambda token, email: url_for(
                        "workspace.signature_access",
                        token=token,
                        email=email,
                        _external=True,
                    ),
                )
                flash("Reminder sent.", "success")
            else:
                raise ValueError("Unsupported signature action.")
        except Exception as exc:
            flash(str(exc), "danger")

    requests = SignatureRequestService.list_requests_for_user(current_user.id)
    return render_template("workspace/signatures.html", requests=requests)


@workspace_bp.route("/signatures/access/<token>", methods=["GET", "POST"])
def signature_access(token: str):
    email = (request.values.get("email") or "").strip().lower()
    code = (request.values.get("code") or "").strip()
    active_request = None
    signer = None
    signer_fields: list[SignatureField] = []
    access_error = ""
    success_message = ""

    if request.method == "POST":
        action = (request.form.get("action") or "verify").strip().lower()
        try:
            if action == "verify":
                active_request, signer = SignatureRequestService.get_signer_access(
                    token=token,
                    email=email,
                    verification_code=code,
                )
            elif action == "sign":
                active_request = SignatureRequestService.submit_signer_fields(
                    token=token,
                    email=email,
                    verification_code=code,
                    values=request.form,
                    ip_address=request.headers.get("X-Forwarded-For", request.remote_addr or ""),
                    user_agent=request.headers.get("User-Agent", ""),
                )
                signer = SignatureSigner.query.filter_by(request_id=active_request.id, email=email).first()
                success_message = "Signature submitted successfully."
            else:
                raise ValueError("Unsupported signer action.")
        except Exception as exc:
            access_error = str(exc)

    if not active_request and email and code and not access_error:
        try:
            active_request, signer = SignatureRequestService.get_signer_access(
                token=token,
                email=email,
                verification_code=code,
            )
        except Exception as exc:
            access_error = str(exc)

    if active_request and signer:
        signer_fields = SignatureField.query.filter_by(
            request_id=active_request.id,
            signer_id=signer.id,
        ).order_by(SignatureField.page.asc(), SignatureField.id.asc()).all()

    return render_template(
        "workspace/signature_access.html",
        token=token,
        email=email,
        code=code,
        active_request=active_request,
        signer=signer,
        signer_fields=signer_fields,
        access_error=access_error,
        success_message=success_message,
    )


@workspace_bp.route("/forms", methods=["GET", "POST"])
@login_required
def forms():
    if request.method == "POST":
        action = (request.form.get("action") or "create").strip().lower()
        try:
            if action == "create":
                upload = request.files.get("document")
                if not upload or not upload.filename:
                    raise ValueError("Upload a source PDF to build a form template.")
                source_file = StorageService.save_uploaded_file(upload, current_user.id, kind="upload")
                fields = _parse_json(request.form.get("fields_json"), [])
                if not fields:
                    fields = FormBuilderService.auto_detect_fields(source_file.id, current_user.id)
                template = FormBuilderService.create_template(
                    owner_id=current_user.id,
                    source_file_id=source_file.id,
                    name=request.form.get("name", "Untitled Form"),
                    description=request.form.get("description", ""),
                    fields=fields,
                )
                flash("Form template created.", "success")
                return redirect(url_for("workspace.form_builder", template_id=template.id))
        except Exception as exc:
            flash(str(exc), "danger")

    templates = FormBuilderService.list_templates(current_user.id)
    submissions = FormBuilderService.list_submissions_for_owner(current_user.id)
    return render_template("workspace/forms.html", templates=templates, submissions=submissions)


@workspace_bp.route("/forms/<int:template_id>/builder", methods=["GET", "POST"])
@login_required
def form_builder(template_id: int):
    try:
        template = FormBuilderService.get_template_for_owner(template_id, current_user.id)
    except Exception:
        abort(404)

    if request.method == "POST":
        try:
            fields = _parse_json(request.form.get("fields_json"), [])
            FormBuilderService.update_template(
                template_id=template.id,
                owner_id=current_user.id,
                name=request.form.get("name", template.name),
                description=request.form.get("description", template.description),
                fields=fields,
            )
            flash("Template updated.", "success")
            return redirect(url_for("workspace.form_builder", template_id=template.id))
        except Exception as exc:
            flash(str(exc), "danger")

    fields = (
        FormTemplateField.query.filter_by(template_id=template.id)
        .order_by(FormTemplateField.page.asc(), FormTemplateField.id.asc())
        .all()
    )
    return render_template("workspace/form_builder.html", template=template, fields=fields)


@workspace_bp.route("/forms/fill/<token>", methods=["GET", "POST"])
def form_fill(token: str):
    template = None
    success_token = ""
    error = ""
    try:
        template = FormBuilderService.get_template_by_token(token)
    except Exception as exc:
        error = str(exc)

    if template and request.method == "POST":
        try:
            submission = FormBuilderService.submit_form(
                template_token=token,
                values=request.form,
                submitted_by_user_id=(current_user.id if current_user.is_authenticated else None),
            )
            success_token = submission.submit_token
            flash("Form submitted successfully.", "success")
        except Exception as exc:
            error = str(exc)

    fields = []
    if template:
        fields = (
            FormTemplateField.query.filter_by(template_id=template.id)
            .order_by(FormTemplateField.page.asc(), FormTemplateField.id.asc())
            .all()
        )

    return render_template(
        "workspace/form_fill.html",
        template=template,
        fields=fields,
        error=error,
        success_token=success_token,
    )


@workspace_bp.route("/forms/submission/<token>/download")
def form_submission_download(token: str):
    submission = FormSubmission.query.filter_by(submit_token=(token or "").strip()).first()
    if not submission or not submission.output_file:
        abort(404)
    output_file = submission.output_file
    absolute = StorageService.absolute_path(output_file)
    return send_file(
        absolute,
        as_attachment=True,
        download_name=output_file.original_name,
        mimetype=output_file.mime_type,
    )


@workspace_bp.route("/cloud-integrations", methods=["GET", "POST"])
@login_required
def cloud_integrations():
    if request.method == "POST":
        action = (request.form.get("action") or "connect").strip().lower()
        provider = request.form.get("provider", "")
        try:
            if action == "connect":
                CloudIntegrationService.upsert_connection(
                    user_id=current_user.id,
                    provider=provider,
                    account_email=request.form.get("account_email", ""),
                    access_token=request.form.get("access_token", ""),
                    refresh_token=request.form.get("refresh_token", ""),
                )
                flash("Cloud provider connection updated.", "success")
            elif action == "disconnect":
                CloudIntegrationService.disconnect(current_user.id, provider)
                flash("Cloud provider disconnected.", "success")
            elif action == "import":
                upload = request.files.get("cloud_upload")
                if not upload or not upload.filename:
                    raise ValueError("Select a file to import from provider.")
                CloudIntegrationService.import_upload(
                    user_id=current_user.id,
                    provider=provider,
                    upload=upload,
                )
                flash("Cloud import completed.", "success")
            elif action == "export":
                file_id = _parse_int(request.form.get("file_id"), 0, minimum=1)
                CloudIntegrationService.export_file(
                    user_id=current_user.id,
                    provider=provider,
                    file_id=file_id,
                )
                flash("File exported to provider folder.", "success")
            else:
                raise ValueError("Unsupported cloud action.")
        except Exception as exc:
            flash(str(exc), "danger")

    statuses = CloudIntegrationService.provider_statuses(current_user.id)
    logs = CloudIntegrationService.recent_logs(current_user.id)
    files = (
        ManagedFile.query.filter_by(user_id=current_user.id, is_deleted=False)
        .order_by(ManagedFile.created_at.desc())
        .limit(40)
        .all()
    )
    return render_template(
        "workspace/cloud_integrations.html",
        statuses=statuses,
        logs=logs,
        files=files,
    )


@workspace_bp.route("/ocr-suite", methods=["GET", "POST"])
@login_required
def ocr_suite():
    latest_result = None
    if request.method == "POST":
        try:
            upload = request.files.get("document")
            if not upload or not upload.filename:
                raise ValueError("Upload a file for OCR.")
            file_record = StorageService.save_uploaded_file(upload, current_user.id, kind="upload")
            formats = request.form.getlist("output_formats") or [request.form.get("output_format", "txt")]
            result = AdvancedOCRService.process_upload(
                user_id=current_user.id,
                file_record=file_record,
                options={
                    "languages": request.form.get("languages", "eng+hin"),
                    "output_formats": formats,
                    "deskew": _is_checked("deskew"),
                    "denoise": _is_checked("denoise"),
                    "contrast_boost": _is_checked("contrast_boost"),
                    "black_white": _is_checked("black_white"),
                },
            )
            latest_result = result
            flash("OCR processing completed.", "success")
        except Exception as exc:
            flash(str(exc), "danger")

    recent_outputs = (
        ManagedFile.query.filter(
            ManagedFile.user_id == current_user.id,
            ManagedFile.label.ilike("%OCR%"),
            ManagedFile.is_deleted.is_(False),
        )
        .order_by(ManagedFile.created_at.desc())
        .limit(30)
        .all()
    )
    return render_template("workspace/ocr_suite.html", latest_result=latest_result, recent_outputs=recent_outputs)


@workspace_bp.route("/version-history", methods=["GET", "POST"])
@login_required
def version_history():
    if request.method == "POST":
        action = (request.form.get("action") or "create_group").strip().lower()
        try:
            if action == "create_group":
                VersioningService.create_group(
                    user_id=current_user.id,
                    name=request.form.get("group_name", "Document Set"),
                    description=request.form.get("group_description", ""),
                )
                flash("Version group created.", "success")
            elif action == "add_version":
                VersioningService.add_version(
                    group_id=_parse_int(request.form.get("group_id"), 0, minimum=1),
                    user_id=current_user.id,
                    file_id=_parse_int(request.form.get("file_id"), 0, minimum=1),
                    version_label=request.form.get("version_label", ""),
                    notes=request.form.get("notes", ""),
                )
                flash("Version added.", "success")
            elif action == "compare":
                comparison = VersioningService.compare_versions(
                    user_id=current_user.id,
                    version_a_id=_parse_int(request.form.get("version_a_id"), 0, minimum=1),
                    version_b_id=_parse_int(request.form.get("version_b_id"), 0, minimum=1),
                )
                flash("Comparison report generated.", "success")
                return redirect(url_for("main.download_file", file_id=comparison.report_file_id))
            else:
                raise ValueError("Unsupported version action.")
        except Exception as exc:
            flash(str(exc), "danger")

    groups = VersioningService.list_groups(current_user.id)
    versions = (
        DocumentVersion.query.filter(
            DocumentVersion.group_id.in_([group.id for group in groups] or [0])
        )
        .order_by(DocumentVersion.created_at.desc())
        .all()
    )
    comparisons = VersioningService.list_comparisons(current_user.id)
    files = (
        ManagedFile.query.filter_by(user_id=current_user.id, is_deleted=False)
        .order_by(ManagedFile.created_at.desc())
        .limit(60)
        .all()
    )
    return render_template(
        "workspace/versioning.html",
        groups=groups,
        versions=versions,
        comparisons=comparisons,
        files=files,
    )


@workspace_bp.route("/compliance", methods=["GET", "POST"])
@login_required
def compliance():
    if request.method == "POST":
        try:
            mode = (request.form.get("mode") or "check").strip().lower()
            file_id = _parse_int(request.form.get("file_id"), 0, minimum=0)
            if file_id <= 0:
                upload = request.files.get("document")
                if not upload or not upload.filename:
                    raise ValueError("Choose a file or upload a document.")
                file_record = StorageService.save_uploaded_file(upload, current_user.id, kind="upload")
                file_id = file_record.id
            report = ComplianceService.run_report(
                user_id=current_user.id,
                file_id=file_id,
                mode=mode,
                metadata_overrides={
                    "title": request.form.get("meta_title", ""),
                    "author": request.form.get("meta_author", ""),
                    "subject": request.form.get("meta_subject", ""),
                },
            )
            flash("Compliance report generated.", "success")
            return redirect(url_for("main.download_file", file_id=report.output_file_id))
        except Exception as exc:
            flash(str(exc), "danger")

    reports = ComplianceService.list_reports(current_user.id)
    files = (
        ManagedFile.query.filter_by(user_id=current_user.id, is_deleted=False)
        .order_by(ManagedFile.created_at.desc())
        .limit(40)
        .all()
    )
    return render_template("workspace/compliance.html", reports=reports, files=files)


@workspace_bp.route("/team-workspace", methods=["GET", "POST"])
@login_required
def team_workspace():
    active_workspace_id = _parse_int(request.args.get("workspace_id"), 0)

    if request.method == "POST":
        action = (request.form.get("action") or "create_workspace").strip().lower()
        try:
            if action == "create_workspace":
                TeamWorkspaceService.create_workspace(
                    owner=current_user,
                    name=request.form.get("name", "Team Workspace"),
                    description=request.form.get("description", ""),
                )
                flash("Workspace created.", "success")
            elif action == "invite_member":
                TeamWorkspaceService.invite_member(
                    workspace_id=_parse_int(request.form.get("workspace_id"), 0, minimum=1),
                    actor=current_user,
                    email=request.form.get("email", ""),
                    role=request.form.get("role", "viewer"),
                )
                flash("Member invitation saved.", "success")
            elif action == "create_project":
                TeamWorkspaceService.create_project(
                    workspace_id=_parse_int(request.form.get("workspace_id"), 0, minimum=1),
                    actor=current_user,
                    name=request.form.get("project_name", "Untitled Project"),
                    description=request.form.get("project_description", ""),
                )
                flash("Project created.", "success")
            elif action == "set_approval":
                TeamWorkspaceService.set_project_approval(
                    project_id=_parse_int(request.form.get("project_id"), 0, minimum=1),
                    actor=current_user,
                    status=request.form.get("approval_status", "pending"),
                )
                flash("Approval status updated.", "success")
            elif action == "attach_file":
                TeamWorkspaceService.attach_file(
                    project_id=_parse_int(request.form.get("project_id"), 0, minimum=1),
                    actor=current_user,
                    file_id=_parse_int(request.form.get("file_id"), 0, minimum=1),
                )
                flash("File attached to project.", "success")
            elif action == "comment":
                TeamWorkspaceService.add_comment(
                    project_id=_parse_int(request.form.get("project_id"), 0, minimum=1),
                    actor=current_user,
                    content=request.form.get("content", ""),
                )
                flash("Comment added.", "success")
            else:
                raise ValueError("Unsupported team workspace action.")
        except Exception as exc:
            flash(str(exc), "danger")

    workspaces = TeamWorkspaceService.list_accessible_workspaces(current_user)
    active_workspace = None
    if active_workspace_id:
        try:
            active_workspace = TeamWorkspaceService.get_workspace_for_user(active_workspace_id, current_user)
        except Exception:
            active_workspace = None
    if not active_workspace and workspaces:
        active_workspace = workspaces[0]

    projects: list[TeamProject] = []
    members: list[TeamWorkspaceMember] = []
    feed = []
    if active_workspace:
        projects = (
            TeamProject.query.filter_by(workspace_id=active_workspace.id)
            .order_by(TeamProject.updated_at.desc())
            .all()
        )
        members = (
            TeamWorkspaceMember.query.filter_by(workspace_id=active_workspace.id)
            .order_by(TeamWorkspaceMember.created_at.desc())
            .all()
        )
        feed = TeamWorkspaceService.workspace_feed(active_workspace.id, current_user)

    files = (
        ManagedFile.query.filter_by(user_id=current_user.id, is_deleted=False)
        .order_by(ManagedFile.created_at.desc())
        .limit(40)
        .all()
    )

    return render_template(
        "workspace/team_workspace.html",
        workspaces=workspaces,
        active_workspace=active_workspace,
        projects=projects,
        members=members,
        feed=feed,
        files=files,
    )


@workspace_bp.route("/api-automation", methods=["GET", "POST"])
@login_required
def api_automation():
    if request.method == "POST":
        action = (request.form.get("action") or "create_key").strip().lower()
        try:
            if action == "create_key":
                _key_row, raw_key = APIAutomationService.create_api_key(
                    user_id=current_user.id,
                    name=request.form.get("name", "Default API Key"),
                    rate_limit_per_minute=_parse_int(
                        request.form.get("rate_limit"),
                        60,
                        minimum=10,
                        maximum=2000,
                    ),
                    expires_days=_parse_int(
                        request.form.get("expires_days"),
                        0,
                        minimum=0,
                        maximum=365,
                    ),
                )
                flash(f"API key created. Copy now: {raw_key}", "warning")
            elif action == "revoke_key":
                APIAutomationService.revoke_api_key(
                    key_id=_parse_int(request.form.get("key_id"), 0, minimum=1),
                    user_id=current_user.id,
                )
                flash("API key revoked.", "success")
            elif action == "create_webhook":
                event_types = [
                    item.strip()
                    for item in (request.form.get("event_types") or "job.completed").split(",")
                    if item.strip()
                ]
                APIAutomationService.create_webhook_endpoint(
                    user_id=current_user.id,
                    name=request.form.get("webhook_name", "Webhook"),
                    url=request.form.get("webhook_url", ""),
                    event_types=event_types,
                    secret=request.form.get("webhook_secret", ""),
                )
                flash("Webhook endpoint created.", "success")
            elif action == "disable_webhook":
                APIAutomationService.disable_webhook(
                    webhook_id=_parse_int(request.form.get("webhook_id"), 0, minimum=1),
                    user_id=current_user.id,
                )
                flash("Webhook disabled.", "success")
            else:
                raise ValueError("Unsupported API action.")
        except Exception as exc:
            flash(str(exc), "danger")

    api_keys = APIAutomationService.list_api_keys(current_user.id)
    webhooks = APIAutomationService.list_webhooks(current_user.id)
    usage_logs = (
        ApiUsageLog.query.filter_by(user_id=current_user.id)
        .order_by(ApiUsageLog.created_at.desc())
        .limit(120)
        .all()
    )
    return render_template(
        "workspace/api_automation.html",
        api_keys=api_keys,
        webhooks=webhooks,
        usage_logs=usage_logs,
    )


@workspace_bp.route("/privacy", methods=["GET", "POST"])
@login_required
def privacy_center():
    if request.method == "POST":
        action = (request.form.get("action") or "update").strip().lower()
        try:
            if action == "update":
                PrivacyService.update_settings(
                    user_id=current_user.id,
                    auto_delete_hours=_parse_int(
                        request.form.get("auto_delete_hours"),
                        24,
                        minimum=1,
                        maximum=24 * 30,
                    ),
                    private_mode_enabled=_is_checked("private_mode_enabled"),
                    allow_share_links=_is_checked("allow_share_links"),
                    keep_download_history=_is_checked("keep_download_history"),
                )
                flash("Privacy settings updated.", "success")
            elif action == "delete_all":
                stats = PrivacyService.delete_all_user_files(current_user.id)
                flash(
                    f"Delete-all executed. deleted={stats['deleted']}, missing={stats['missing']}, failed={stats['failed']}",
                    "warning",
                )
            elif action == "apply_auto_cleanup":
                stats = PrivacyService.apply_auto_delete_policy(current_user.id)
                flash(f"Auto-delete cleanup removed {stats['removed']} files.", "success")
            else:
                raise ValueError("Unsupported privacy action.")
        except Exception as exc:
            flash(str(exc), "danger")

    settings = PrivacyService.get_or_create_settings(current_user.id)
    file_logs = PrivacyService.list_file_access_logs(current_user.id)
    share_logs = PrivacyService.list_share_access_logs(current_user.id)
    return render_template(
        "workspace/privacy_center.html",
        settings=settings,
        file_logs=file_logs,
        share_logs=share_logs,
    )

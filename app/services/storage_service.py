from __future__ import annotations

import base64
import hashlib
import mimetypes
import secrets
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zipfile import is_zipfile

from PIL import Image, UnidentifiedImageError
from flask import current_app
from werkzeug.datastructures import FileStorage
from werkzeug.utils import secure_filename

from app.extensions import db
from app.models import ManagedFile


class StorageService:
    ROOT_MAP = {
        "upload": "UPLOAD_ROOT",
        "output": "OUTPUT_ROOT",
        "cloud": "CLOUD_ROOT",
        "signature": "UPLOAD_ROOT",
        "scanner": "SCAN_ROOT",
    }
    SUSPICIOUS_EXTENSIONS = {
        ".exe",
        ".dll",
        ".bat",
        ".cmd",
        ".ps1",
        ".msi",
        ".com",
        ".scr",
        ".js",
        ".vbs",
        ".jar",
        ".sh",
    }
    VERIFY_IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tif", ".tiff"}
    TEMP_STORAGE_KINDS = {"upload", "output", "signature", "scanner"}

    @staticmethod
    def _root_for_kind(kind: str) -> Path:
        config_key = StorageService.ROOT_MAP.get(kind, "UPLOAD_ROOT")
        root = Path(current_app.config[config_key]).resolve()
        root.mkdir(parents=True, exist_ok=True)
        return root

    @staticmethod
    def _user_dir(user_id: int, kind: str) -> Path:
        directory = StorageService._root_for_kind(kind) / str(user_id)
        directory.mkdir(parents=True, exist_ok=True)
        return directory.resolve()

    @staticmethod
    def _validate_extension(filename: str) -> str:
        clean_name = Path(filename).name
        if not clean_name:
            raise ValueError("Invalid file name.")
        suffixes = [suffix.lower() for suffix in Path(clean_name).suffixes]
        if not suffixes:
            raise ValueError("File extension is required.")
        if any(suffix in StorageService.SUSPICIOUS_EXTENSIONS for suffix in suffixes[:-1]):
            raise ValueError("Suspicious upload rejected.")
        ext = suffixes[-1]
        if ext not in current_app.config["UPLOAD_EXTENSIONS"]:
            raise ValueError("Unsupported file type.")
        return ext

    @staticmethod
    def _sha256_for_path(path: Path) -> str:
        hasher = hashlib.sha256()
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                hasher.update(chunk)
        return hasher.hexdigest()

    @staticmethod
    def _as_utc(value: datetime | None) -> datetime | None:
        if not value:
            return None
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)

    @staticmethod
    def _validate_saved_file_content(absolute_path: Path, ext: str) -> None:
        if not absolute_path.exists():
            raise ValueError("Upload could not be saved.")
        size_bytes = absolute_path.stat().st_size
        if size_bytes <= 0:
            raise ValueError("Uploaded file is empty.")
        max_single = int(current_app.config.get("MAX_SINGLE_UPLOAD_BYTES") or (25 * 1024 * 1024))
        if size_bytes > max_single:
            raise ValueError(
                f"File too large. Max allowed size is {max_single // (1024 * 1024)} MB per file."
            )

        if ext == ".pdf":
            with absolute_path.open("rb") as stream:
                header = stream.read(5)
            if header != b"%PDF-":
                raise ValueError("Invalid PDF file. Upload a genuine PDF document.")

        if ext in StorageService.VERIFY_IMAGE_EXTENSIONS:
            try:
                with Image.open(absolute_path) as image:
                    image.verify()
            except (UnidentifiedImageError, OSError) as exc:
                raise ValueError("Invalid image file content.") from exc

        if ext == ".zip" and not is_zipfile(absolute_path):
            raise ValueError("Invalid ZIP file.")

    @staticmethod
    def _build_record(
        user_id: int,
        kind: str,
        original_name: str,
        stored_name: str,
        absolute_path: Path,
        label: str = "",
    ) -> ManagedFile:
        mime_type = mimetypes.guess_type(str(absolute_path))[0] or "application/octet-stream"
        relative_path = absolute_path.relative_to(Path(current_app.root_path).parent.resolve())
        file_hash = StorageService._sha256_for_path(absolute_path)
        duplicate = (
            ManagedFile.query.filter_by(
                user_id=user_id,
                storage_kind=kind,
                file_hash=file_hash,
                is_deleted=False,
            )
            .order_by(ManagedFile.created_at.desc())
            .first()
        )
        if duplicate:
            existing_path = (Path(current_app.root_path).parent.resolve() / duplicate.relative_path).resolve()
            if absolute_path.exists() and absolute_path != existing_path:
                absolute_path.unlink()
            if label and not duplicate.label:
                duplicate.label = label
                db.session.commit()
            return duplicate

        managed_file = ManagedFile(
            user_id=user_id,
            storage_kind=kind,
            original_name=original_name,
            stored_name=stored_name,
            relative_path=relative_path.as_posix(),
            mime_type=mime_type,
            size_bytes=absolute_path.stat().st_size,
            label=label,
            file_hash=file_hash,
        )
        db.session.add(managed_file)
        db.session.commit()
        return managed_file

    @staticmethod
    def save_uploaded_file(
        uploaded_file: FileStorage,
        user_id: int,
        kind: str = "upload",
        label: str = "",
    ) -> ManagedFile:
        if not uploaded_file or not uploaded_file.filename:
            raise ValueError("Please choose a file to upload.")
        safe_name = secure_filename(uploaded_file.filename)
        ext = StorageService._validate_extension(safe_name)
        stored_name = f"{secrets.token_hex(16)}{ext}"
        absolute_path = StorageService._user_dir(user_id, kind) / stored_name
        try:
            uploaded_file.save(absolute_path)
            StorageService._validate_saved_file_content(absolute_path, ext)
        except Exception:
            if absolute_path.exists():
                absolute_path.unlink()
            raise
        return StorageService._build_record(user_id, kind, safe_name, stored_name, absolute_path, label)

    @staticmethod
    def save_bytes(
        content: bytes,
        original_name: str,
        user_id: int,
        kind: str = "output",
        label: str = "",
    ) -> ManagedFile:
        safe_name = secure_filename(original_name)
        ext = StorageService._validate_extension(safe_name)
        stored_name = f"{secrets.token_hex(16)}{ext}"
        absolute_path = StorageService._user_dir(user_id, kind) / stored_name
        absolute_path.write_bytes(content)
        try:
            StorageService._validate_saved_file_content(absolute_path, ext)
        except Exception:
            if absolute_path.exists():
                absolute_path.unlink()
            raise
        return StorageService._build_record(user_id, kind, safe_name, stored_name, absolute_path, label)

    @staticmethod
    def register_existing_file(
        absolute_path: str | Path,
        user_id: int,
        kind: str,
        original_name: str | None = None,
        label: str = "",
    ) -> ManagedFile:
        absolute = Path(absolute_path).resolve()
        if not absolute.exists():
            raise ValueError("File does not exist.")
        original_name = original_name or absolute.name
        ext = StorageService._validate_extension(original_name)
        StorageService._validate_saved_file_content(absolute, ext)
        return StorageService._build_record(
            user_id=user_id,
            kind=kind,
            original_name=original_name,
            stored_name=f"{absolute.parent.name}_{absolute.name}",
            absolute_path=absolute,
            label=label,
        )

    @staticmethod
    def save_signature_data(data_url: str, user_id: int) -> ManagedFile:
        if "," not in data_url:
            raise ValueError("Invalid signature image data.")
        header, encoded = data_url.split(",", 1)
        if "png" not in header:
            raise ValueError("Signature data must be PNG.")
        payload = base64.b64decode(encoded)
        return StorageService.save_bytes(payload, "signature.png", user_id, "signature", "Drawn signature")

    @staticmethod
    def absolute_path(file_record: ManagedFile) -> Path:
        root = Path(current_app.root_path).parent.resolve()
        file_path = (root / file_record.relative_path).resolve()
        if root not in file_path.parents and file_path != root:
            raise ValueError("Invalid file path.")
        return file_path

    @staticmethod
    def list_cloud_files(user_id: int) -> list[ManagedFile]:
        return (
            ManagedFile.query.filter_by(user_id=user_id, storage_kind="cloud", is_deleted=False)
            .order_by(ManagedFile.created_at.desc())
            .all()
        )

    @staticmethod
    def rename_file(file_id: int, user_id: int, new_name: str) -> ManagedFile:
        file_record = ManagedFile.query.filter_by(
            id=file_id, user_id=user_id, is_deleted=False
        ).first_or_404()
        safe_name = secure_filename(new_name)
        if not safe_name:
            raise ValueError("Invalid file name.")
        file_record.original_name = safe_name
        db.session.commit()
        return file_record

    @staticmethod
    def delete_file(file_id: int, user_id: int) -> None:
        file_record = ManagedFile.query.filter_by(
            id=file_id, user_id=user_id, is_deleted=False
        ).first_or_404()
        absolute_path = StorageService.absolute_path(file_record)
        if absolute_path.exists():
            absolute_path.unlink()
        file_record.is_deleted = True
        db.session.commit()

    @staticmethod
    def cleanup_expired_temp_files(
        ttl_hours: int | None = None,
        storage_kinds: set[str] | None = None,
    ) -> dict[str, int]:
        ttl = int(ttl_hours or current_app.config.get("TEMP_FILE_TTL_HOURS") or 24)
        ttl = max(1, ttl)
        kinds = storage_kinds or StorageService.TEMP_STORAGE_KINDS
        cutoff = datetime.now(timezone.utc) - timedelta(hours=ttl)

        candidates = (
            ManagedFile.query.filter(ManagedFile.storage_kind.in_(list(kinds)))
            .order_by(ManagedFile.created_at.asc())
            .all()
        )

        removed_files = 0
        marked_deleted = 0
        failed = 0
        for file_record in candidates:
            created_at = StorageService._as_utc(file_record.created_at)
            if created_at and created_at > cutoff:
                continue
            try:
                absolute_path = StorageService.absolute_path(file_record)
                if absolute_path.exists():
                    absolute_path.unlink()
                    removed_files += 1
                if not file_record.is_deleted:
                    file_record.is_deleted = True
                    marked_deleted += 1
            except Exception:
                failed += 1

        if marked_deleted:
            db.session.commit()

        return {
            "removed_files": removed_files,
            "marked_deleted": marked_deleted,
            "failed": failed,
            "ttl_hours": ttl,
        }

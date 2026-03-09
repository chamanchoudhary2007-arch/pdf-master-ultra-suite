from __future__ import annotations

from datetime import timedelta

from flask import current_app

from app.extensions import db
from app.models import (
    FileAccessLog,
    ManagedFile,
    PrivacySetting,
    ShareAccessLog,
    ShareLink,
    utcnow,
)
from app.services.storage_service import StorageService


class PrivacyService:
    @staticmethod
    def get_or_create_settings(user_id: int) -> PrivacySetting:
        row = PrivacySetting.query.filter_by(user_id=user_id).first()
        if row:
            return row
        default_hours = int(current_app.config.get("PRIVACY_DEFAULT_AUTO_DELETE_HOURS") or 24)
        row = PrivacySetting(
            user_id=user_id,
            auto_delete_hours=max(1, min(default_hours, 24 * 30)),
            private_mode_enabled=False,
            allow_share_links=True,
            keep_download_history=True,
        )
        db.session.add(row)
        db.session.commit()
        return row

    @staticmethod
    def update_settings(
        *,
        user_id: int,
        auto_delete_hours: int,
        private_mode_enabled: bool,
        allow_share_links: bool,
        keep_download_history: bool,
    ) -> PrivacySetting:
        row = PrivacyService.get_or_create_settings(user_id)
        row.auto_delete_hours = max(1, min(24 * 30, int(auto_delete_hours or 24)))
        row.private_mode_enabled = bool(private_mode_enabled)
        row.allow_share_links = bool(allow_share_links)
        row.keep_download_history = bool(keep_download_history)
        db.session.commit()
        return row

    @staticmethod
    def should_allow_share_links(user_id: int) -> bool:
        settings = PrivacyService.get_or_create_settings(user_id)
        return bool(settings.allow_share_links)

    @staticmethod
    def log_file_access(
        *,
        owner_user_id: int,
        actor_user_id: int | None,
        file_id: int,
        action: str,
        ip_address: str = "",
        user_agent: str = "",
        details: dict | None = None,
    ) -> None:
        settings = PrivacyService.get_or_create_settings(owner_user_id)
        if not settings.keep_download_history:
            return

        row = FileAccessLog(
            owner_user_id=owner_user_id,
            actor_user_id=actor_user_id,
            file_id=file_id,
            action=(action or "download")[:40],
            ip_address=(ip_address or "")[:80],
            user_agent=(user_agent or "")[:255],
            details_json=details or {},
        )
        db.session.add(row)
        db.session.commit()

    @staticmethod
    def log_share_access(
        *,
        share_link_id: int,
        event: str,
        status: str,
        ip_address: str = "",
        user_agent: str = "",
        details: dict | None = None,
    ) -> None:
        row = ShareAccessLog(
            share_link_id=share_link_id,
            event=(event or "view")[:40],
            status=(status or "ok")[:20],
            ip_address=(ip_address or "")[:80],
            user_agent=(user_agent or "")[:255],
            details_json=details or {},
        )
        db.session.add(row)
        db.session.commit()

    @staticmethod
    def list_file_access_logs(user_id: int, limit: int = 200) -> list[FileAccessLog]:
        return (
            FileAccessLog.query.filter_by(owner_user_id=user_id)
            .order_by(FileAccessLog.created_at.desc())
            .limit(max(1, limit))
            .all()
        )

    @staticmethod
    def list_share_access_logs(user_id: int, limit: int = 200) -> list[ShareAccessLog]:
        return (
            ShareAccessLog.query.join(ShareLink, ShareLink.id == ShareAccessLog.share_link_id)
            .filter(ShareLink.user_id == user_id)
            .order_by(ShareAccessLog.created_at.desc())
            .limit(max(1, limit))
            .all()
        )

    @staticmethod
    def delete_all_user_files(user_id: int) -> dict[str, int]:
        files = ManagedFile.query.filter_by(user_id=user_id, is_deleted=False).all()
        deleted = 0
        missing = 0
        failed = 0

        for file_record in files:
            try:
                absolute = StorageService.absolute_path(file_record)
                if absolute.exists():
                    absolute.unlink()
                    deleted += 1
                else:
                    missing += 1
                file_record.is_deleted = True
            except Exception:
                failed += 1

        ShareLink.query.filter_by(user_id=user_id, is_active=True).update({"is_active": False})
        db.session.commit()
        return {"deleted": deleted, "missing": missing, "failed": failed}

    @staticmethod
    def apply_auto_delete_policy(user_id: int) -> dict[str, int]:
        settings = PrivacyService.get_or_create_settings(user_id)
        cutoff = utcnow() - timedelta(hours=max(1, int(settings.auto_delete_hours or 24)))
        candidates = ManagedFile.query.filter(
            ManagedFile.user_id == user_id,
            ManagedFile.is_deleted.is_(False),
            ManagedFile.created_at < cutoff,
        ).all()
        removed = 0
        for file_record in candidates:
            try:
                absolute = StorageService.absolute_path(file_record)
                if absolute.exists():
                    absolute.unlink()
                file_record.is_deleted = True
                removed += 1
            except Exception:
                continue
        db.session.commit()
        return {"removed": removed}

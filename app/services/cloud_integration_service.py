from __future__ import annotations

import shutil
from pathlib import Path

from flask import current_app

from app.extensions import db
from app.models import CloudIntegrationConnection, CloudTransferLog, ManagedFile
from app.services.storage_service import StorageService


class CloudIntegrationService:
    PROVIDERS = {
        "google_drive": {
            "label": "Google Drive",
            "env_keys": ("GOOGLE_DRIVE_CLIENT_ID", "GOOGLE_DRIVE_CLIENT_SECRET"),
        },
        "dropbox": {
            "label": "Dropbox",
            "env_keys": ("DROPBOX_APP_KEY", "DROPBOX_APP_SECRET"),
        },
        "onedrive": {
            "label": "OneDrive",
            "env_keys": ("ONEDRIVE_CLIENT_ID", "ONEDRIVE_CLIENT_SECRET"),
        },
    }

    @staticmethod
    def _normalize_provider(provider: str) -> str:
        key = (provider or "").strip().lower()
        if key not in CloudIntegrationService.PROVIDERS:
            raise ValueError("Unsupported cloud provider.")
        return key

    @staticmethod
    def is_provider_configured(provider: str) -> bool:
        normalized = CloudIntegrationService._normalize_provider(provider)
        keys = CloudIntegrationService.PROVIDERS[normalized]["env_keys"]
        return all((current_app.config.get(key) or "").strip() for key in keys)

    @staticmethod
    def provider_statuses(user_id: int) -> list[dict]:
        rows: list[dict] = []
        for provider, meta in CloudIntegrationService.PROVIDERS.items():
            connection = CloudIntegrationConnection.query.filter_by(
                user_id=user_id,
                provider=provider,
            ).first()
            configured = CloudIntegrationService.is_provider_configured(provider)
            rows.append(
                {
                    "provider": provider,
                    "label": meta["label"],
                    "configured": configured,
                    "connected": bool(connection and connection.status == "connected"),
                    "connection": connection,
                }
            )
        return rows

    @staticmethod
    def upsert_connection(
        *,
        user_id: int,
        provider: str,
        account_email: str,
        access_token: str = "",
        refresh_token: str = "",
    ) -> CloudIntegrationConnection:
        normalized = CloudIntegrationService._normalize_provider(provider)
        connection = CloudIntegrationConnection.query.filter_by(
            user_id=user_id,
            provider=normalized,
        ).first()
        if not connection:
            connection = CloudIntegrationConnection(
                user_id=user_id,
                provider=normalized,
                account_email="",
                access_token="",
                refresh_token="",
                status="disconnected",
                metadata_json={},
            )
            db.session.add(connection)

        connection.account_email = (account_email or "").strip()[:255]
        connection.access_token = (access_token or "").strip()
        connection.refresh_token = (refresh_token or "").strip()
        connection.status = "connected" if connection.account_email else "disconnected"
        connection.metadata_json = {
            **(connection.metadata_json or {}),
            "configured": CloudIntegrationService.is_provider_configured(normalized),
        }
        db.session.commit()
        return connection

    @staticmethod
    def disconnect(user_id: int, provider: str) -> None:
        normalized = CloudIntegrationService._normalize_provider(provider)
        connection = CloudIntegrationConnection.query.filter_by(
            user_id=user_id,
            provider=normalized,
        ).first()
        if not connection:
            return
        connection.status = "disconnected"
        connection.access_token = ""
        connection.refresh_token = ""
        db.session.commit()

    @staticmethod
    def import_upload(*, user_id: int, provider: str, upload) -> ManagedFile:
        normalized = CloudIntegrationService._normalize_provider(provider)
        file_record = StorageService.save_uploaded_file(
            upload,
            user_id=user_id,
            kind="cloud",
            label=f"Imported from {normalized}",
        )
        log = CloudTransferLog(
            user_id=user_id,
            provider=normalized,
            direction="import",
            source_file_id=file_record.id,
            target_file_id=file_record.id,
            status="completed",
            details_json={"mode": "manual_upload"},
        )
        db.session.add(log)
        db.session.commit()
        return file_record

    @staticmethod
    def export_file(*, user_id: int, provider: str, file_id: int) -> CloudTransferLog:
        normalized = CloudIntegrationService._normalize_provider(provider)
        source = ManagedFile.query.filter_by(id=file_id, user_id=user_id, is_deleted=False).first()
        if not source:
            raise ValueError("File not found for export.")

        source_path = StorageService.absolute_path(source)
        export_root = (
            Path(current_app.config["CLOUD_ROOT"])
            / "integrations"
            / normalized
            / str(user_id)
            / "exports"
        )
        export_root.mkdir(parents=True, exist_ok=True)
        target_path = export_root / source.original_name
        if target_path.exists():
            target_path = export_root / f"{target_path.stem}_{source.id}{target_path.suffix}"
        shutil.copy2(source_path, target_path)

        exported_file = StorageService.register_existing_file(
            absolute_path=target_path,
            user_id=user_id,
            kind="cloud",
            original_name=target_path.name,
            label=f"Exported to {normalized}",
        )

        log = CloudTransferLog(
            user_id=user_id,
            provider=normalized,
            direction="export",
            source_file_id=source.id,
            target_file_id=exported_file.id,
            status="completed",
            details_json={"destination": str(target_path)},
        )
        db.session.add(log)
        db.session.commit()
        return log

    @staticmethod
    def recent_logs(user_id: int, limit: int = 50) -> list[CloudTransferLog]:
        return (
            CloudTransferLog.query.filter_by(user_id=user_id)
            .order_by(CloudTransferLog.created_at.desc())
            .limit(max(1, limit))
            .all()
        )

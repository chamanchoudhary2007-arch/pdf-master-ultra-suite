from __future__ import annotations

import secrets
from datetime import timedelta

from flask import current_app

from app.extensions import db
from app.models import ShareLink, utcnow


class ShareService:
    @staticmethod
    def _validate_state(share_link: ShareLink) -> None:
        if share_link.expires_at < utcnow():
            raise ValueError("This link has expired.")
        if share_link.download_count >= share_link.max_downloads:
            raise ValueError("This link has reached its download limit.")

    @staticmethod
    def create_share_link(
        user_id: int,
        file_id: int,
        password: str = "",
        expiry_hours: int | None = None,
        max_downloads: int = 10,
    ) -> ShareLink:
        expiry_value = max(1, min(168, int(expiry_hours or current_app.config["SHARE_LINK_TTL_HOURS"])))
        max_download_limit = max(1, min(500, int(max_downloads)))
        share_link = ShareLink(
            user_id=user_id,
            file_id=file_id,
            token=secrets.token_urlsafe(24),
            expires_at=utcnow() + timedelta(hours=expiry_value),
            max_downloads=max_download_limit,
        )
        share_link.set_password(password)
        db.session.add(share_link)
        db.session.commit()
        return share_link

    @staticmethod
    def get_link_for_access(token: str) -> ShareLink:
        share_link = ShareLink.query.filter_by(token=token, is_active=True).first_or_404()
        ShareService._validate_state(share_link)
        return share_link

    @staticmethod
    def validate_link(token: str, password: str = "", check_password: bool = True) -> ShareLink:
        share_link = ShareLink.query.filter_by(token=token, is_active=True).first_or_404()
        ShareService._validate_state(share_link)
        if check_password and not share_link.check_password(password):
            raise ValueError("Incorrect share password.")
        return share_link

    @staticmethod
    def mark_download(share_link: ShareLink) -> None:
        share_link.download_count += 1
        db.session.commit()

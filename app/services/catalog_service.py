from __future__ import annotations

from sqlalchemy import or_

from app.extensions import db
from app.models import FavoriteTool, Job, ToolCatalog


class CatalogService:
    @staticmethod
    def get_enabled_tools(search: str = "", category: str = "") -> list[ToolCatalog]:
        query = ToolCatalog.query.filter_by(is_enabled=True)
        if category:
            query = query.filter(ToolCatalog.category == category)
        if search:
            pattern = f"%{search.strip()}%"
            query = query.filter(
                or_(
                    ToolCatalog.name.ilike(pattern),
                    ToolCatalog.description.ilike(pattern),
                    ToolCatalog.keywords.ilike(pattern),
                )
            )
        return query.order_by(ToolCatalog.category.asc(), ToolCatalog.name.asc()).all()

    @staticmethod
    def get_tool(tool_key: str, enabled_only: bool = False) -> ToolCatalog:
        query = ToolCatalog.query.filter_by(tool_key=tool_key)
        if enabled_only:
            query = query.filter_by(is_enabled=True)
        return query.first_or_404()

    @staticmethod
    def favorite_keys_for_user(user_id: int) -> set[str]:
        return {
            row.tool_key
            for row in FavoriteTool.query.with_entities(FavoriteTool.tool_key)
            .filter_by(user_id=user_id)
            .all()
        }

    @staticmethod
    def toggle_favorite(user_id: int, tool_key: str) -> bool:
        favorite = FavoriteTool.query.filter_by(user_id=user_id, tool_key=tool_key).first()
        if favorite:
            db.session.delete(favorite)
            db.session.commit()
            return False
        db.session.add(FavoriteTool(user_id=user_id, tool_key=tool_key))
        db.session.commit()
        return True

    @staticmethod
    def recent_tool_keys(user_id: int, limit: int = 6) -> list[str]:
        rows = (
            Job.query.with_entities(Job.tool_key)
            .filter_by(user_id=user_id)
            .order_by(Job.created_at.desc())
            .limit(limit)
            .all()
        )
        seen = []
        for row in rows:
            if row.tool_key not in seen:
                seen.append(row.tool_key)
        return seen

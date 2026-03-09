from __future__ import annotations

from datetime import timedelta

from sqlalchemy import func

from app.models import (
    ActivityLog,
    ApiKey,
    ApiUsageLog,
    BulkBatch,
    CloudIntegrationConnection,
    ComplianceReport,
    DocumentChatSession,
    FileAccessLog,
    Job,
    ManagedFile,
    SignatureRequest,
    TeamWorkspace,
    ToolCatalog,
    User,
    WalletTransaction,
    utcnow,
)


class AnalyticsService:
    @staticmethod
    def admin_summary() -> dict:
        total_users = User.query.count()
        total_tools = ToolCatalog.query.count()
        enabled_tools = ToolCatalog.query.filter_by(is_enabled=True).count()
        total_jobs = Job.query.count()
        completed_jobs = Job.query.filter_by(status="completed").count()
        failed_jobs = Job.query.filter_by(status="failed").count()
        revenue = (
            WalletTransaction.query.with_entities(func.sum(-WalletTransaction.amount_paise))
            .filter(WalletTransaction.transaction_type.in_(["debit", "subscription"]))
            .scalar()
            or 0
        )
        cloud_files = ManagedFile.query.filter_by(storage_kind="cloud", is_deleted=False).count()
        upload_files = ManagedFile.query.filter_by(storage_kind="upload", is_deleted=False).count()
        return {
            "total_users": total_users,
            "total_tools": total_tools,
            "enabled_tools": enabled_tools,
            "total_jobs": total_jobs,
            "completed_jobs": completed_jobs,
            "failed_jobs": failed_jobs,
            "revenue_paise": revenue,
            "cloud_files": cloud_files,
            "upload_files": upload_files,
        }

    @staticmethod
    def most_used_tools(limit: int = 10) -> list[tuple[str, int]]:
        return (
            Job.query.with_entities(Job.tool_key, func.count(Job.id).label("usage_count"))
            .group_by(Job.tool_key)
            .order_by(func.count(Job.id).desc())
            .limit(limit)
            .all()
        )

    @staticmethod
    def recent_logs(limit: int = 20) -> list[ActivityLog]:
        return ActivityLog.query.order_by(ActivityLog.created_at.desc()).limit(limit).all()

    @staticmethod
    def payment_rows(limit: int = 20) -> list[WalletTransaction]:
        return (
            WalletTransaction.query.order_by(WalletTransaction.created_at.desc()).limit(limit).all()
        )

    @staticmethod
    def user_dashboard_summary(user_id: int, window_days: int = 30) -> dict:
        window_start = utcnow() - timedelta(days=max(1, window_days))
        total_jobs = Job.query.filter_by(user_id=user_id).count()
        completed_jobs = Job.query.filter_by(user_id=user_id, status="completed").count()
        failed_jobs = Job.query.filter_by(user_id=user_id, status="failed").count()
        recent_jobs = Job.query.filter(Job.user_id == user_id, Job.created_at >= window_start).count()
        window_spend = (
            WalletTransaction.query.with_entities(func.sum(-WalletTransaction.amount_paise))
            .filter(
                WalletTransaction.user_id == user_id,
                WalletTransaction.transaction_type.in_(["debit", "subscription"]),
                WalletTransaction.created_at >= window_start,
            )
            .scalar()
            or 0
        )
        window_topups = (
            WalletTransaction.query.with_entities(func.sum(WalletTransaction.amount_paise))
            .filter(
                WalletTransaction.user_id == user_id,
                WalletTransaction.transaction_type == "topup",
                WalletTransaction.created_at >= window_start,
            )
            .scalar()
            or 0
        )
        success_rate = round((completed_jobs / total_jobs * 100), 1) if total_jobs else 0
        return {
            "window_days": max(1, window_days),
            "total_jobs": total_jobs,
            "completed_jobs": completed_jobs,
            "failed_jobs": failed_jobs,
            "recent_jobs": recent_jobs,
            "success_rate": success_rate,
            "window_spend_paise": int(window_spend),
            "window_topups_paise": int(window_topups),
        }

    @staticmethod
    def advanced_feature_summary() -> dict:
        return {
            "chat_sessions": DocumentChatSession.query.count(),
            "bulk_batches": BulkBatch.query.count(),
            "signature_requests": SignatureRequest.query.count(),
            "api_keys": ApiKey.query.filter_by(is_active=True).count(),
            "api_calls": ApiUsageLog.query.count(),
            "team_workspaces": TeamWorkspace.query.count(),
            "compliance_reports": ComplianceReport.query.count(),
            "cloud_connections": CloudIntegrationConnection.query.filter_by(status="connected").count(),
            "privacy_logs": FileAccessLog.query.count(),
        }

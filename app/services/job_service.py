from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from threading import Lock
from typing import Callable

from flask import current_app, g

from app.extensions import db
from app.models import Job, User, utcnow
from app.services.pricing_service import PricingService


class JobService:
    _executor: ThreadPoolExecutor | None = None
    _lock = Lock()

    @classmethod
    def _get_executor(cls) -> ThreadPoolExecutor:
        with cls._lock:
            if cls._executor is None:
                cls._executor = ThreadPoolExecutor(
                    max_workers=current_app.config["JOB_MAX_WORKERS"],
                    thread_name_prefix="pdfmaster-job",
                )
            return cls._executor

    @staticmethod
    def create_job(
        user_id: int,
        tool_key: str,
        price: int = 0,
        input_filename: str = "",
        options: dict | None = None,
    ) -> Job:
        job = Job(
            user_id=user_id,
            tool_key=tool_key,
            price=price,
            input_filename=input_filename,
            options_json=options or {},
            status="queued",
            progress=0,
        )
        db.session.add(job)
        db.session.commit()
        return job

    @staticmethod
    def update_job(job_id: int, **fields) -> Job:
        job = db.session.get(Job, job_id)
        if not job:
            raise ValueError("Job not found.")
        for key, value in fields.items():
            setattr(job, key, value)
        if fields.get("status") in {"completed", "failed"}:
            job.completed_at = utcnow()
        db.session.commit()
        return job

    @classmethod
    def submit_job(
        cls,
        user: User,
        tool,
        input_filename: str,
        options: dict,
        work_fn: Callable[[int, Callable[[int], None]], dict],
    ) -> Job:
        from app.services.subscription_service import SubscriptionService

        is_premium_user = SubscriptionService.is_user_premium(user)
        price = 0 if is_premium_user else (tool.price_paise if tool.is_payperuse_allowed else 0)
        job = cls.create_job(
            user_id=user.id,
            tool_key=tool.tool_key,
            price=price,
            input_filename=input_filename,
            options=options,
        )
        if price:
            try:
                PricingService.charge_tool(user, tool, f"JOB-{job.id}")
            except Exception:
                db.session.delete(job)
                db.session.commit()
                raise
        app = current_app._get_current_object()
        cls._get_executor().submit(cls._run_job, app, job.id, work_fn)
        return job

    @classmethod
    def _run_job(
        cls,
        app,
        job_id: int,
        work_fn: Callable[[int, Callable[[int], None]], dict],
    ) -> None:
        with app.app_context():
            job = db.session.get(Job, job_id)
            if not job:
                return

            def progress_callback(value: int) -> None:
                cls.update_job(job_id, progress=max(0, min(100, int(value))), status="processing")

            try:
                cls.update_job(job_id, status="processing", progress=10)
                worker_user = db.session.get(User, job.user_id)
                if not worker_user:
                    raise ValueError("Job user not found.")
                with app.test_request_context(f"/_jobs/{job_id}"):
                    g._login_user = worker_user
                    result = work_fn(job_id, progress_callback)
                cls.update_job(
                    job_id,
                    status="completed",
                    progress=100,
                    output_filename=result.get("output_filename", ""),
                    output_file_id=result.get("output_file_id"),
                    result_json=result.get("result_json", {}),
                    error_message="",
                )
            except Exception as exc:
                job = db.session.get(Job, job_id)
                user = db.session.get(User, job.user_id) if job else None
                if job and user and job.price:
                    PricingService.refund(
                        user=user,
                        amount_paise=job.price,
                        reference=f"JOB-{job.id}-REFUND",
                        note=f"Refund for failed job {job.tool_key}",
                    )
                cls.update_job(
                    job_id,
                    status="failed",
                    progress=100,
                    error_message=str(exc),
                )

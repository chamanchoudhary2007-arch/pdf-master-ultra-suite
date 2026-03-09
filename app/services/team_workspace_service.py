from __future__ import annotations

from app.extensions import db
from app.models import (
    ManagedFile,
    TeamActivity,
    TeamComment,
    TeamProject,
    TeamProjectFile,
    TeamWorkspace,
    TeamWorkspaceMember,
    User,
    utcnow,
)


class TeamWorkspaceService:
    @staticmethod
    def ensure_personal_workspace(user: User) -> TeamWorkspace:
        workspace = TeamWorkspace.query.filter_by(owner_id=user.id, is_personal=True).first()
        if workspace:
            return workspace
        workspace = TeamWorkspace(
            owner_id=user.id,
            name="Personal Workspace",
            description="Private workspace for your own projects.",
            is_personal=True,
            status="active",
        )
        db.session.add(workspace)
        db.session.flush()
        TeamWorkspaceService._upsert_owner_member(workspace, user)
        db.session.commit()
        TeamWorkspaceService.log_activity(workspace.id, user.id, "workspace.personal_created")
        return workspace

    @staticmethod
    def _upsert_owner_member(workspace: TeamWorkspace, user: User) -> None:
        member = TeamWorkspaceMember.query.filter_by(
            workspace_id=workspace.id,
            email=user.email,
        ).first()
        if member:
            member.user_id = user.id
            member.role = "owner"
            member.status = "accepted"
            member.joined_at = member.joined_at or utcnow()
            return

        db.session.add(
            TeamWorkspaceMember(
                workspace_id=workspace.id,
                user_id=user.id,
                invited_by_user_id=user.id,
                email=user.email,
                role="owner",
                status="accepted",
                joined_at=utcnow(),
            )
        )

    @staticmethod
    def list_accessible_workspaces(user: User, limit: int = 100) -> list[TeamWorkspace]:
        TeamWorkspaceService.ensure_personal_workspace(user)

        member_rows = TeamWorkspaceMember.query.filter(
            (TeamWorkspaceMember.user_id == user.id)
            | (TeamWorkspaceMember.email == user.email)
        ).all()
        member_workspace_ids = {row.workspace_id for row in member_rows}

        rows = TeamWorkspace.query.filter(
            (TeamWorkspace.owner_id == user.id)
            | (TeamWorkspace.id.in_(member_workspace_ids))
        ).order_by(TeamWorkspace.updated_at.desc()).limit(max(1, limit)).all()

        for member in member_rows:
            if member.user_id is None and member.email == user.email:
                member.user_id = user.id
                if member.status == "pending":
                    member.status = "accepted"
                    member.joined_at = utcnow()
        db.session.commit()
        return rows

    @staticmethod
    def create_workspace(owner: User, name: str, description: str = "") -> TeamWorkspace:
        workspace = TeamWorkspace(
            owner_id=owner.id,
            name=(name or "Team Workspace").strip()[:160],
            description=(description or "").strip(),
            is_personal=False,
            status="active",
        )
        db.session.add(workspace)
        db.session.flush()
        TeamWorkspaceService._upsert_owner_member(workspace, owner)
        db.session.commit()
        TeamWorkspaceService.log_activity(workspace.id, owner.id, "workspace.created")
        return workspace

    @staticmethod
    def get_workspace_for_user(workspace_id: int, user: User) -> TeamWorkspace:
        workspace = TeamWorkspace.query.get(workspace_id)
        if not workspace:
            raise ValueError("Workspace not found.")

        if workspace.owner_id == user.id:
            return workspace

        member = TeamWorkspaceMember.query.filter_by(
            workspace_id=workspace.id,
            email=user.email,
        ).first()
        if not member:
            raise ValueError("You do not have access to this workspace.")
        if member.user_id is None:
            member.user_id = user.id
        if member.status == "pending":
            member.status = "accepted"
            member.joined_at = utcnow()
        db.session.commit()
        return workspace

    @staticmethod
    def invite_member(
        *,
        workspace_id: int,
        actor: User,
        email: str,
        role: str = "viewer",
    ) -> TeamWorkspaceMember:
        workspace = TeamWorkspaceService.get_workspace_for_user(workspace_id, actor)
        if workspace.owner_id != actor.id:
            raise ValueError("Only workspace owner can invite members.")

        normalized_email = (email or "").strip().lower()
        if not normalized_email:
            raise ValueError("Member email is required.")

        if role not in {"viewer", "editor", "approver", "owner"}:
            role = "viewer"

        member = TeamWorkspaceMember.query.filter_by(
            workspace_id=workspace.id,
            email=normalized_email,
        ).first()
        if not member:
            member = TeamWorkspaceMember(
                workspace_id=workspace.id,
                email=normalized_email,
                role=role,
                status="pending",
                invited_by_user_id=actor.id,
            )
            db.session.add(member)
        else:
            member.role = role
            member.status = "accepted" if member.user_id else "pending"
            member.invited_by_user_id = actor.id

        existing_user = User.query.filter_by(email=normalized_email).first()
        if existing_user:
            member.user_id = existing_user.id
            member.status = "accepted"
            member.joined_at = member.joined_at or utcnow()

        db.session.commit()
        TeamWorkspaceService.log_activity(
            workspace.id,
            actor.id,
            "workspace.member_invited",
            details={"email": normalized_email, "role": role},
        )
        return member

    @staticmethod
    def create_project(
        *,
        workspace_id: int,
        actor: User,
        name: str,
        description: str = "",
    ) -> TeamProject:
        workspace = TeamWorkspaceService.get_workspace_for_user(workspace_id, actor)
        project = TeamProject(
            workspace_id=workspace.id,
            owner_id=actor.id,
            name=(name or "Untitled Project").strip()[:180],
            description=(description or "").strip(),
            approval_status="pending",
            metadata_json={},
        )
        db.session.add(project)
        db.session.commit()
        TeamWorkspaceService.log_activity(workspace.id, actor.id, "project.created", details={"project_id": project.id})
        return project

    @staticmethod
    def set_project_approval(*, project_id: int, actor: User, status: str) -> TeamProject:
        project = TeamProject.query.get(project_id)
        if not project:
            raise ValueError("Project not found.")
        workspace = TeamWorkspaceService.get_workspace_for_user(project.workspace_id, actor)
        member = TeamWorkspaceMember.query.filter_by(workspace_id=workspace.id, email=actor.email).first()
        allowed = workspace.owner_id == actor.id or (member and member.role in {"approver", "owner", "editor"})
        if not allowed:
            raise ValueError("You do not have permission to update approval state.")

        normalized = (status or "").strip().lower()
        if normalized not in {"pending", "approved", "rejected", "changes_requested"}:
            raise ValueError("Invalid approval status.")
        project.approval_status = normalized
        db.session.commit()
        TeamWorkspaceService.log_activity(
            workspace.id,
            actor.id,
            "project.approval_updated",
            details={"project_id": project.id, "status": normalized},
        )
        return project

    @staticmethod
    def attach_file(*, project_id: int, actor: User, file_id: int) -> TeamProjectFile:
        project = TeamProject.query.get(project_id)
        if not project:
            raise ValueError("Project not found.")
        TeamWorkspaceService.get_workspace_for_user(project.workspace_id, actor)

        file_record = ManagedFile.query.filter_by(id=file_id, user_id=actor.id, is_deleted=False).first()
        if not file_record:
            raise ValueError("File not found for attachment.")

        row = TeamProjectFile(
            project_id=project.id,
            file_id=file_record.id,
            added_by_user_id=actor.id,
        )
        db.session.add(row)
        db.session.commit()
        TeamWorkspaceService.log_activity(
            project.workspace_id,
            actor.id,
            "project.file_attached",
            details={"project_id": project.id, "file_id": file_record.id},
        )
        return row

    @staticmethod
    def add_comment(*, project_id: int, actor: User, content: str) -> TeamComment:
        project = TeamProject.query.get(project_id)
        if not project:
            raise ValueError("Project not found.")
        TeamWorkspaceService.get_workspace_for_user(project.workspace_id, actor)
        text = (content or "").strip()
        if not text:
            raise ValueError("Comment cannot be empty.")

        row = TeamComment(
            project_id=project.id,
            user_id=actor.id,
            content=text,
            target_type="project",
            target_id=str(project.id),
        )
        db.session.add(row)
        db.session.commit()
        TeamWorkspaceService.log_activity(
            project.workspace_id,
            actor.id,
            "project.comment_added",
            details={"project_id": project.id},
        )
        return row

    @staticmethod
    def workspace_feed(workspace_id: int, user: User, limit: int = 50) -> list[TeamActivity]:
        TeamWorkspaceService.get_workspace_for_user(workspace_id, user)
        return (
            TeamActivity.query.filter_by(workspace_id=workspace_id)
            .order_by(TeamActivity.created_at.desc())
            .limit(max(1, limit))
            .all()
        )

    @staticmethod
    def log_activity(workspace_id: int, actor_user_id: int | None, action: str, details: dict | None = None) -> None:
        row = TeamActivity(
            workspace_id=workspace_id,
            actor_user_id=actor_user_id,
            action=(action or "").strip()[:80],
            details_json=details or {},
        )
        db.session.add(row)
        db.session.commit()

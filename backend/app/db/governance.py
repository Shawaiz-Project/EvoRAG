from typing import Any
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Attributes, Base, Record


class UserFeedback(Record, Attributes, Base):
    __tablename__ = "user_feedback"
    __table_args__ = (
        sa.UniqueConstraint("run_id", "user_id"),
        sa.CheckConstraint("rating IS NULL OR rating BETWEEN 1 AND 5", name="rating"),
        sa.CheckConstraint(
            "rating IS NOT NULL OR comment IS NOT NULL OR correction IS NOT NULL", name="content"
        ),
    )

    run_id: Mapped[UUID] = mapped_column(sa.ForeignKey("rag_runs.id"))
    user_id: Mapped[UUID] = mapped_column(sa.ForeignKey("users.id"), index=True)
    rating: Mapped[int | None]
    comment: Mapped[str | None] = mapped_column(sa.Text)
    correction: Mapped[str | None] = mapped_column(sa.Text)


class LearningProposal(Record, Attributes, Base):
    __tablename__ = "learning_proposals"
    __table_args__ = (
        sa.CheckConstraint(
            "status IN ('pending','under_review','approved','rejected','published','rolled_back')",
            name="status",
        ),
        sa.CheckConstraint(
            "(author_kind = 'ai' AND author_user_id IS NULL)"
            " OR (author_kind = 'human' AND author_user_id IS NOT NULL)",
            name="author",
        ),
        sa.Index("ix_learning_proposals_queue", "status", "created_at"),
    )

    author_kind: Mapped[str]
    author_user_id: Mapped[UUID | None] = mapped_column(sa.ForeignKey("users.id"), index=True)
    feedback_id: Mapped[UUID | None] = mapped_column(sa.ForeignKey("user_feedback.id"), index=True)
    evaluation_id: Mapped[UUID | None] = mapped_column(
        sa.ForeignKey("teacher_evaluations.id"), index=True
    )
    target_version_id: Mapped[UUID | None] = mapped_column(
        sa.ForeignKey("document_versions.id"), index=True
    )
    published_version_id: Mapped[UUID | None] = mapped_column(
        sa.ForeignKey("document_versions.id"), index=True
    )
    status: Mapped[str] = mapped_column(server_default="pending")
    proposed_change: Mapped[dict[str, Any]] = mapped_column(JSONB)


class LearningApproval(Record, Attributes, Base):
    __tablename__ = "learning_approvals"
    __table_args__ = (
        sa.CheckConstraint("decision IN ('approve', 'reject')", name="decision"),
        sa.Index("ix_learning_approvals_history", "proposal_id", "created_at"),
    )

    proposal_id: Mapped[UUID] = mapped_column(sa.ForeignKey("learning_proposals.id"))
    reviewer_id: Mapped[UUID] = mapped_column(sa.ForeignKey("users.id"), index=True)
    decision: Mapped[str]
    reason: Mapped[str]


class AuditLog(Record, Attributes, Base):
    __tablename__ = "audit_logs"
    __table_args__ = (
        sa.Index("ix_audit_logs_subject_history", "subject_type", "subject_id", "created_at"),
        sa.Index("ix_audit_logs_created_at", "created_at"),
    )

    actor_user_id: Mapped[UUID | None] = mapped_column(sa.ForeignKey("users.id"), index=True)
    action: Mapped[str]
    subject_type: Mapped[str]
    subject_id: Mapped[UUID]

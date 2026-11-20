from datetime import datetime
from typing import Any
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Attributes, Base, Record


class Conversation(Record, Attributes, Base):
    __tablename__ = "conversations"
    __table_args__ = (sa.Index("ix_conversations_owner_history", "owner_id", "created_at"),)

    owner_id: Mapped[UUID] = mapped_column(sa.ForeignKey("users.id"))
    title: Mapped[str | None]


class Message(Record, Attributes, Base):
    __tablename__ = "messages"
    __table_args__ = (
        sa.UniqueConstraint("conversation_id", "id"),
        sa.Index("ix_messages_conversation_history", "conversation_id", "created_at", "id"),
        sa.CheckConstraint("role IN ('user', 'assistant', 'system')", name="role"),
    )

    conversation_id: Mapped[UUID] = mapped_column(sa.ForeignKey("conversations.id"))
    role: Mapped[str]
    content: Mapped[str] = mapped_column(sa.Text)


class RAGConfiguration(Record, Attributes, Base):
    __tablename__ = "rag_configurations"
    __table_args__ = (
        sa.UniqueConstraint("key", "version"),
        sa.CheckConstraint("version > 0", name="version"),
    )

    key: Mapped[str]
    version: Mapped[int]
    strategy: Mapped[str]
    embedding_space_id: Mapped[UUID | None] = mapped_column(
        sa.ForeignKey("embedding_spaces.id"), index=True
    )
    model_id: Mapped[UUID | None] = mapped_column(sa.ForeignKey("model_registry.id"), index=True)
    settings: Mapped[dict[str, Any]] = mapped_column(JSONB)


class RAGRun(Record, Attributes, Base):
    __tablename__ = "rag_runs"
    __table_args__ = (
        sa.ForeignKeyConstraint(
            ["conversation_id", "question_id"], ["messages.conversation_id", "messages.id"]
        ),
        sa.ForeignKeyConstraint(
            ["conversation_id", "answer_id"], ["messages.conversation_id", "messages.id"]
        ),
        sa.CheckConstraint(
            "role IN ('primary', 'teacher', 'benchmark')"
            " AND status IN ('running', 'completed', 'abstained', 'failed')",
            name="role_status",
        ),
        sa.CheckConstraint("completed_at IS NULL OR completed_at >= created_at", name="duration"),
        sa.Index("ix_rag_runs_conversation_history", "conversation_id", "created_at"),
    )

    conversation_id: Mapped[UUID] = mapped_column(sa.ForeignKey("conversations.id"))
    question_id: Mapped[UUID] = mapped_column(index=True)
    answer_id: Mapped[UUID | None] = mapped_column(index=True)
    configuration_id: Mapped[UUID] = mapped_column(
        sa.ForeignKey("rag_configurations.id"), index=True
    )
    parent_run_id: Mapped[UUID | None] = mapped_column(sa.ForeignKey("rag_runs.id"), index=True)
    role: Mapped[str]
    status: Mapped[str]
    completed_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))


class RetrievalResult(Record, Attributes, Base):
    __tablename__ = "retrieval_results"
    __table_args__ = (
        sa.UniqueConstraint("run_id", "stage", "iteration", "rank"),
        sa.UniqueConstraint("run_id", "stage", "iteration", "chunk_id"),
        sa.CheckConstraint("rank > 0 AND iteration >= 0", name="rank_iteration"),
    )

    run_id: Mapped[UUID] = mapped_column(sa.ForeignKey("rag_runs.id"))
    chunk_id: Mapped[UUID] = mapped_column(sa.ForeignKey("document_chunks.id"), index=True)
    stage: Mapped[str]
    iteration: Mapped[int] = mapped_column(server_default="0")
    rank: Mapped[int]
    score: Mapped[float | None] = mapped_column(sa.Double)


class TeacherEvaluation(Record, Attributes, Base):
    __tablename__ = "teacher_evaluations"
    __table_args__ = (
        sa.CheckConstraint(
            "primary_run_id <> teacher_run_id AND verdict IN ('pass', 'revise', 'reject')",
            name="distinct_runs_verdict",
        ),
    )

    primary_run_id: Mapped[UUID] = mapped_column(sa.ForeignKey("rag_runs.id"), index=True)
    teacher_run_id: Mapped[UUID] = mapped_column(sa.ForeignKey("rag_runs.id"), unique=True)
    verdict: Mapped[str]
    report: Mapped[dict[str, Any]] = mapped_column(JSONB)

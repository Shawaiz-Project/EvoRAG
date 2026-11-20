from typing import Any
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Attributes, Base, Record


class BenchmarkDataset(Record, Attributes, Base):
    __tablename__ = "benchmark_datasets"
    __table_args__ = (
        sa.UniqueConstraint("key", "version"),
        sa.CheckConstraint("version > 0", name="version"),
    )

    key: Mapped[str]
    version: Mapped[int]
    description: Mapped[str]


class BenchmarkCase(Record, Attributes, Base):
    __tablename__ = "benchmark_cases"
    __table_args__ = (
        sa.UniqueConstraint("dataset_id", "key"),
        sa.UniqueConstraint("dataset_id", "id"),
    )

    dataset_id: Mapped[UUID] = mapped_column(sa.ForeignKey("benchmark_datasets.id"))
    key: Mapped[str]
    question: Mapped[str] = mapped_column(sa.Text)
    golden_answer: Mapped[str | None] = mapped_column(sa.Text)
    expected_verdict: Mapped[str | None]


class BenchmarkEvidence(Record, Base):
    __tablename__ = "benchmark_evidence"
    __table_args__ = (
        sa.UniqueConstraint("case_id", "chunk_id"),
        sa.CheckConstraint("relevance > 0", name="relevance"),
    )

    case_id: Mapped[UUID] = mapped_column(sa.ForeignKey("benchmark_cases.id"))
    chunk_id: Mapped[UUID] = mapped_column(sa.ForeignKey("document_chunks.id"), index=True)
    relevance: Mapped[int] = mapped_column(server_default="1")


class BenchmarkResult(Record, Attributes, Base):
    __tablename__ = "benchmark_results"
    __table_args__ = (
        sa.ForeignKeyConstraint(
            ["dataset_id", "case_id"], ["benchmark_cases.dataset_id", "benchmark_cases.id"]
        ),
        sa.Index("ix_benchmark_results_history", "dataset_id", "created_at"),
    )

    dataset_id: Mapped[UUID] = mapped_column(sa.ForeignKey("benchmark_datasets.id"))
    case_id: Mapped[UUID | None] = mapped_column(index=True)
    configuration_id: Mapped[UUID] = mapped_column(
        sa.ForeignKey("rag_configurations.id"), index=True
    )
    run_id: Mapped[UUID | None] = mapped_column(sa.ForeignKey("rag_runs.id"), index=True)
    metrics: Mapped[dict[str, Any]] = mapped_column(JSONB)
    experiment_key: Mapped[str] = mapped_column(index=True)


class Experiment(Record, Attributes, Base):
    __tablename__ = "experiments"
    __table_args__ = (
        sa.CheckConstraint("status IN ('running', 'completed', 'failed')", name="status"),
        sa.Index("ix_experiments_owner_history", "owner_id", "created_at"),
    )

    owner_id: Mapped[UUID] = mapped_column(sa.ForeignKey("users.id"))
    dataset_id: Mapped[UUID] = mapped_column(sa.ForeignKey("benchmark_datasets.id"), index=True)
    name: Mapped[str]
    status: Mapped[str]
    settings: Mapped[dict[str, Any]] = mapped_column(JSONB)

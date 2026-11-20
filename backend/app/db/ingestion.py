from datetime import datetime
from typing import Any
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, Record


class IngestionJob(Record, Base):
    __tablename__ = "ingestion_jobs"
    __table_args__ = (
        sa.CheckConstraint("kind IN ('file', 'website', 'github')", name="kind"),
        sa.CheckConstraint("strategy IN ('recursive', 'sentence', 'semantic')", name="strategy"),
        sa.CheckConstraint(
            "status IN ('queued', 'processing', 'completed', 'failed') AND attempts >= 0",
            name="status",
        ),
        sa.CheckConstraint(
            "(status = 'processing') = (lease_until IS NOT NULL AND lease_token IS NOT NULL)",
            name="lease_status",
        ),
        sa.CheckConstraint(
            "kind <> 'file' OR status = 'completed' OR payload IS NOT NULL", name="file_payload"
        ),
        sa.Index("ix_ingestion_jobs_schedule", "status", "available_at"),
        sa.Index(
            "ix_ingestion_jobs_leases",
            "lease_until",
            postgresql_where=sa.text("lease_until IS NOT NULL"),
        ),
    )

    kind: Mapped[str]
    locator: Mapped[str]
    strategy: Mapped[str]
    status: Mapped[str] = mapped_column(server_default="queued")
    payload: Mapped[bytes | None] = mapped_column(sa.LargeBinary)
    attempts: Mapped[int] = mapped_column(server_default="0")
    available_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), server_default=sa.func.now()
    )
    lease_until: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))
    lease_token: Mapped[UUID | None]
    finished_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))
    error: Mapped[str | None]
    result: Mapped[dict[str, Any] | None] = mapped_column(JSONB(none_as_null=True))

from datetime import datetime, timedelta
from uuid import UUID, uuid4

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.ingestion import IngestionJob
from app.db.repositories import Repository

MAX_ATTEMPTS = 3
LEASE_DURATION = timedelta(minutes=10)


class JobRepository(Repository[IngestionJob]):
    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session, IngestionJob)

    async def database_time(self) -> datetime:
        result = await self.session.execute(
            sa.select(sa.func.clock_timestamp(type_=sa.DateTime(timezone=True)))
        )
        return result.scalar_one()

    async def claim(self) -> IngestionJob | None:
        now = await self.database_time()
        expired = (IngestionJob.status == "processing") & (IngestionJob.lease_until <= now)
        await self.session.execute(
            sa.update(IngestionJob)
            .where(expired, IngestionJob.attempts >= MAX_ATTEMPTS)
            .values(
                status="failed",
                lease_until=None,
                lease_token=None,
                finished_at=now,
                error="The worker lease expired after the final attempt",
            )
        )
        queued = (IngestionJob.status == "queued") & (IngestionJob.available_at <= now)
        query = (
            sa.select(IngestionJob)
            .where(sa.or_(queued, expired), IngestionJob.attempts < MAX_ATTEMPTS)
            .order_by(IngestionJob.available_at, IngestionJob.created_at)
            .limit(1)
            .with_for_update(skip_locked=True)
        )
        job = await self.session.scalar(query)
        if job is None:
            return None
        job.status = "processing"
        job.lease_token = uuid4()
        job.lease_until = now + LEASE_DURATION
        job.attempts += 1
        await self.session.flush()
        return job

    async def lock_claim(self, job_id: UUID, token: UUID) -> IngestionJob | None:
        return await self.session.scalar(
            sa.select(IngestionJob)
            .where(
                IngestionJob.id == job_id,
                IngestionJob.status == "processing",
                IngestionJob.lease_token == token,
            )
            .with_for_update()
        )

    async def retry(self, job_id: UUID) -> IngestionJob | None:
        job = await self.session.scalar(
            sa.select(IngestionJob).where(IngestionJob.id == job_id).with_for_update()
        )
        if job is None:
            return None
        if job.status != "failed":
            raise ValueError("Only failed jobs can be retried")
        job.status = "queued"
        job.available_at = await self.database_time()
        job.attempts = 0
        job.finished_at = None
        job.error = None
        await self.session.flush()
        return job

import asyncio
import logging
from datetime import timedelta
from hashlib import sha256
from typing import Any, cast
from uuid import UUID, uuid4

import aiohttp
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import Settings
from app.core.logging import configure_logging
from app.db.ingestion import IngestionJob
from app.db.knowledge import Chunk
from app.db.repositories import DocumentRepository
from app.db.session import session_factory
from app.ingestion.chunking import ChunkStrategy, chunk_blocks
from app.ingestion.extraction import InvalidDocument, PageOCR, extract_file, filename_and_mime
from app.ingestion.jobs import MAX_ATTEMPTS, JobRepository
from app.ingestion.remote import RemoteDocument, github, website

logger = logging.getLogger(__name__)


class IngestionWorker:
    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        settings: Settings,
        ocr: PageOCR | None = None,
    ) -> None:
        self.sessions = sessions
        self.settings = settings
        self.ocr = ocr

    async def run_once(self) -> bool:
        async with self.sessions.begin() as session:
            job = await JobRepository(session).claim()
        if job is None:
            return False
        token = job.lease_token
        assert token is not None
        try:
            async with asyncio.timeout(180):
                documents = await self.extract(job)
                await self.complete(job, token, documents)
            logger.info("Processed ingestion job %s", job.id)
        except InvalidDocument as error:
            await self.fail(job.id, token, str(error), retryable=False)
        except (TimeoutError, aiohttp.ClientError) as error:
            logger.warning("Ingestion request for job %s failed (%s)", job.id, type(error).__name__)
            await self.fail(job.id, token, "Remote request timed out or failed", retryable=True)
        except Exception as error:
            logger.warning("Ingestion job %s failed (%s)", job.id, type(error).__name__)
            await self.fail(job.id, token, "Extraction or persistence failed", retryable=False)
        return True

    async def extract(self, job: IngestionJob) -> list[RemoteDocument]:
        if job.kind == "website":
            return [await website(job.locator, self.settings.max_remote_bytes)]
        if job.kind == "github":
            return await github(job.locator, self.settings.max_remote_bytes)
        name, mime = filename_and_mime(job.locator)
        assert job.payload is not None
        blocks = await asyncio.to_thread(
            extract_file, name, job.payload, self.settings.max_upload_bytes, ocr=self.ocr
        )
        return [RemoteDocument(name, name, mime, job.payload, blocks, {"filename": name})]

    async def complete(self, claim: IngestionJob, token: UUID, docs: list[RemoteDocument]) -> None:
        async with self.sessions.begin() as session:
            jobs = JobRepository(session)
            job = await jobs.lock_claim(claim.id, token)
            if job is None:
                return
            document_repo = DocumentRepository(session)
            source_locator = "uploads" if job.kind == "file" else job.locator
            source_title = "Uploads" if job.kind == "file" else source_locator
            source = await document_repo.source(job.kind, source_locator, source_title)
            results: list[dict[str, Any]] = []
            for parsed in docs:
                document = await document_repo.document(source.id, parsed.locator, parsed.title)
                result = await document_repo.add_version(
                    document.id,
                    sha256=sha256(parsed.data).hexdigest(),
                    size_bytes=len(parsed.data),
                    mime_type=parsed.mime,
                    extracted_text="\n\n".join(block.text for block in parsed.blocks),
                    attributes={"provenance": parsed.provenance, "chunk_strategy": job.strategy},
                    make_current=True,
                )
                if result.created:
                    self.add_chunks(
                        session, result.record.id, parsed, cast(ChunkStrategy, job.strategy)
                    )
                    await session.flush()
                results.append(
                    {
                        "document_id": str(document.id),
                        "version_id": str(result.record.id),
                        "version": result.record.version,
                        "duplicate": not result.created,
                    }
                )
            job.result = {"documents": results}
            job.status = "completed"
            job.payload = None
            job.finished_at = await jobs.database_time()
            job.lease_token = None
            job.lease_until = None
            job.error = None

    @staticmethod
    def add_chunks(
        session: AsyncSession, version_id: UUID, document: RemoteDocument, strategy: ChunkStrategy
    ) -> None:
        drafts = list(chunk_blocks(document.blocks, strategy))
        ids = {draft.ordinal: uuid4() for draft in drafts}
        parents = {draft.parent for draft in drafts if draft.parent is not None}
        session.add_all(
            Chunk(
                id=ids[draft.ordinal],
                document_version_id=version_id,
                ordinal=draft.ordinal,
                parent_id=ids.get(draft.parent) if draft.parent is not None else None,
                content=draft.text,
                section=draft.block.section,
                page=draft.block.page,
                attributes={
                    **draft.block.attributes,
                    "kind": draft.block.kind,
                    "parent_chunk": draft.ordinal in parents,
                },
            )
            for draft in drafts
        )

    async def fail(self, job_id: UUID, token: UUID, error: str, *, retryable: bool) -> None:
        async with self.sessions.begin() as session:
            jobs = JobRepository(session)
            job = await jobs.lock_claim(job_id, token)
            if job is None:
                return
            now = await jobs.database_time()
            job.error = error
            job.lease_token = None
            job.lease_until = None
            if retryable and job.attempts < MAX_ATTEMPTS:
                job.status = "queued"
                job.available_at = now + timedelta(seconds=2**job.attempts)
            else:
                job.status = "failed"
                job.finished_at = now


async def main() -> None:
    settings = Settings()
    configure_logging(settings.log_level)
    engine = create_async_engine(settings.database_url, pool_pre_ping=True)
    worker = IngestionWorker(session_factory(engine), settings)
    try:
        while True:
            try:
                if not await worker.run_once():
                    await asyncio.sleep(1)
            except Exception as error:
                logger.error("Ingestion queue unavailable (%s)", type(error).__name__)
                await asyncio.sleep(5)
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())

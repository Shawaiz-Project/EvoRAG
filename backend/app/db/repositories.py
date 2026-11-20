from collections.abc import Sequence
from dataclasses import dataclass
from math import isfinite
from typing import Any
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.base import Base
from app.db.knowledge import ChunkEmbedding, Document, DocumentVersion, EmbeddingSpace, Source


class Repository[Model: Base]:
    def __init__(self, session: AsyncSession, model: type[Model]) -> None:
        self.session = session
        self.model = model

    async def get(self, record_id: UUID) -> Model | None:
        return await self.session.get(self.model, record_id)

    async def add(self, record: Model) -> Model:
        self.session.add(record)
        await self.session.flush()
        return record


@dataclass(frozen=True, slots=True)
class VersionWrite:
    record: DocumentVersion
    created: bool


class DocumentRepository(Repository[Document]):
    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session, Document)

    async def source(self, kind: str, locator: str, title: str) -> Source:
        await self.session.execute(
            insert(Source)
            .values(kind=kind, locator=locator, title=title)
            .on_conflict_do_nothing(index_elements=[Source.kind, Source.locator])
        )
        query = sa.select(Source).where(Source.kind == kind, Source.locator == locator)
        return (await self.session.scalars(query)).one()

    async def document(self, source_id: UUID, locator: str, title: str) -> Document:
        await self.session.execute(
            insert(Document)
            .values(source_id=source_id, locator=locator, title=title)
            .on_conflict_do_nothing(index_elements=[Document.source_id, Document.locator])
        )
        query = sa.select(Document).where(
            Document.source_id == source_id, Document.locator == locator
        )
        return (await self.session.scalars(query)).one()

    async def add_version(
        self,
        document_id: UUID,
        *,
        sha256: str,
        size_bytes: int,
        mime_type: str,
        extracted_text: str,
        make_current: bool,
        attributes: dict[str, Any] | None = None,
    ) -> VersionWrite:
        await self._lock_document(document_id)
        versions = sa.select(DocumentVersion).where(DocumentVersion.document_id == document_id)
        duplicate = await self.session.scalar(versions.where(DocumentVersion.sha256 == sha256))
        if duplicate is not None:
            return VersionWrite(duplicate, created=False)

        latest = await self.session.scalar(
            sa.select(sa.func.max(DocumentVersion.version)).where(
                DocumentVersion.document_id == document_id
            )
        )
        record = DocumentVersion(
            document_id=document_id,
            version=(latest or 0) + 1,
            sha256=sha256,
            size_bytes=size_bytes,
            mime_type=mime_type,
            extracted_text=extracted_text,
            attributes=attributes or {},
            is_current=False,
        )
        await Repository(self.session, DocumentVersion).add(record)
        if make_current:
            await self._publish_locked(record)
        return VersionWrite(record, created=True)

    async def set_current(self, document_id: UUID, version_id: UUID) -> DocumentVersion:
        await self._lock_document(document_id)
        target = await self.session.scalar(
            sa.select(DocumentVersion).where(
                DocumentVersion.document_id == document_id, DocumentVersion.id == version_id
            )
        )
        if target is None:
            raise LookupError("Version does not belong to the document")
        await self._publish_locked(target)
        return target

    async def _lock_document(self, document_id: UUID) -> None:
        query = sa.select(Document.id).where(Document.id == document_id).with_for_update()
        if await self.session.scalar(query) is None:
            raise LookupError("Document does not exist")

    async def _publish_locked(self, record: DocumentVersion) -> None:
        await self.session.execute(
            sa.update(DocumentVersion)
            .where(
                DocumentVersion.document_id == record.document_id,
                DocumentVersion.is_current.is_(True),
                DocumentVersion.id != record.id,
            )
            .values(is_current=False)
        )
        record.is_current = True
        await self.session.flush()


class EmbeddingRepository(Repository[ChunkEmbedding]):
    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session, ChunkEmbedding)

    async def put(self, space_id: UUID, chunk_id: UUID, vector: Sequence[float]) -> ChunkEmbedding:
        space = await self.session.get(EmbeddingSpace, space_id)
        if space is None:
            raise LookupError("Embedding space does not exist")
        if len(vector) != space.dimensions or not all(isfinite(value) for value in vector):
            raise ValueError(f"Expected {space.dimensions} finite vector components")

        statement = (
            insert(ChunkEmbedding)
            .values(
                embedding_space_id=space_id,
                chunk_id=chunk_id,
                dimensions=space.dimensions,
                embedding=list(vector),
            )
            .on_conflict_do_nothing(
                index_elements=[ChunkEmbedding.embedding_space_id, ChunkEmbedding.chunk_id]
            )
            .returning(ChunkEmbedding)
        )
        created = await self.session.scalar(statement)
        if created is not None:
            return created
        query = sa.select(ChunkEmbedding).where(
            ChunkEmbedding.embedding_space_id == space_id, ChunkEmbedding.chunk_id == chunk_id
        )
        return (await self.session.scalars(query)).one()

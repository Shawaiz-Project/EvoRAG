import asyncio
import os
from hashlib import sha256
from uuid import UUID, uuid4

import pytest
import sqlalchemy as sa
from alembic import command
from conftest import migrate
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine

from app.db import models  # noqa: F401
from app.db.base import Base
from app.db.execution import RAGConfiguration
from app.db.identity import ModelArtifact
from app.db.knowledge import Chunk, ChunkEmbedding, Document, DocumentVersion, EmbeddingSpace
from app.db.repositories import DocumentRepository, EmbeddingRepository, Repository
from app.db.seed import register_model, seed_defaults
from app.db.session import session_factory

pytestmark = [
    pytest.mark.anyio,
    pytest.mark.skipif(os.environ.get("EVORAG_DB_TESTS") != "1", reason="requires PostgreSQL"),
]


async def test_empty_database_upgrade_and_downgrade(empty_database: AsyncEngine) -> None:
    async with empty_database.connect() as connection:
        assert await connection.scalar(sa.text("SELECT to_regtype('vector') IS NULL"))
    await migrate(empty_database, command.upgrade, "head")
    async with empty_database.connect() as connection:
        tables = await connection.run_sync(lambda conn: sa.inspect(conn).get_table_names())
        assert set(tables) == set(Base.metadata.tables) | {"alembic_version"}
        assert await connection.scalar(sa.text("SELECT vector_dims('[1,2]'::vector)")) == 2
    await migrate(empty_database, command.downgrade, "base")
    async with empty_database.connect() as connection:
        tables = await connection.run_sync(lambda conn: sa.inspect(conn).get_table_names())
        assert tables == ["alembic_version"]
    await migrate(empty_database, command.upgrade, "head")


async def test_foreign_key_integrity_and_seed_idempotence(database: AsyncEngine) -> None:
    sessions = session_factory(database)
    async with sessions.begin() as session:
        await seed_defaults(session)
        await seed_defaults(session)
        assert await session.scalar(sa.select(sa.func.count()).select_from(RAGConfiguration)) == 3
        assert await session.scalar(sa.select(sa.func.count()).select_from(ModelArtifact)) == 3
        with pytest.raises(IntegrityError):
            async with session.begin_nested():
                await Repository(session, Document).add(
                    Document(source_id=uuid4(), locator="missing", title="Missing source")
                )


async def test_concurrent_duplicate_and_document_history(database: AsyncEngine) -> None:
    sessions = session_factory(database)
    async with sessions.begin() as session:
        documents = DocumentRepository(session)
        source = await documents.source("file", "test:history", "History")
        document = await documents.document(source.id, "file.txt", "Example")
        assert (await documents.document(source.id, "file.txt", "Other title")).id == document.id
        assert document.created_at.tzinfo is not None

    async def write(content: str) -> tuple[UUID, int, bool]:
        data = content.encode()
        async with sessions.begin() as session:
            result = await DocumentRepository(session).add_version(
                document.id,
                sha256=sha256(data).hexdigest(),
                size_bytes=len(data),
                mime_type="text/plain",
                extracted_text=content,
                make_current=True,
            )
            return result.record.id, result.record.version, result.created

    first, duplicate = await asyncio.gather(write("first"), write("first"))
    assert first[:2] == duplicate[:2]
    assert {first[2], duplicate[2]} == {True, False}
    second = await write("second")
    assert second[1:] == (2, True)
    assert (await write("first"))[2] is False
    async with sessions.begin() as session:
        versions = (
            await session.scalars(
                sa.select(DocumentVersion)
                .where(DocumentVersion.document_id == document.id)
                .order_by(DocumentVersion.version)
            )
        ).all()
        assert [version.extracted_text for version in versions] == ["first", "second"]
        assert [version.is_current for version in versions] == [False, True]
        await DocumentRepository(session).set_current(document.id, first[0])
        assert [version.is_current for version in versions] == [True, False]


async def test_vector_dimensions_and_space_isolation(database: AsyncEngine) -> None:
    async with session_factory(database).begin() as session:
        documents = DocumentRepository(session)
        source = await documents.source("file", "test:vectors", "Vectors")
        document = await documents.document(source.id, "vectors.txt", "Vectors")
        version = await documents.add_version(
            document.id,
            sha256=sha256(b"vectors").hexdigest(),
            size_bytes=7,
            mime_type="text/plain",
            extracted_text="vectors",
            make_current=True,
        )
        chunk = await Repository(session, Chunk).add(
            Chunk(document_version_id=version.record.id, ordinal=0, content="vectors")
        )
        model_id = await register_model(
            session, provider="test", kind="embedding", name="fixture", version="1", dimensions=3
        )
        repo = Repository(session, EmbeddingSpace)
        first = await repo.add(
            EmbeddingSpace(
                key="fixture",
                version=1,
                model_id=model_id,
                dimensions=3,
                metric="cosine",
                recipe="first",
            )
        )
        second = await repo.add(
            EmbeddingSpace(
                key="fixture",
                version=2,
                model_id=model_id,
                dimensions=3,
                metric="cosine",
                recipe="second",
            )
        )
        embeddings = EmbeddingRepository(session)
        with pytest.raises(ValueError, match="Expected 3"):
            await embeddings.put(first.id, chunk.id, [1, 2])
        with pytest.raises(IntegrityError):
            async with session.begin_nested():
                await embeddings.add(
                    ChunkEmbedding(
                        embedding_space_id=first.id,
                        chunk_id=chunk.id,
                        dimensions=3,
                        embedding=[1, 2],
                    )
                )
        one = await embeddings.put(first.id, chunk.id, [1, 2, 3])
        two = await embeddings.put(second.id, chunk.id, [3, 2, 1])
        assert one.id != two.id
        assert (await embeddings.put(first.id, chunk.id, [1, 2, 3])).id == one.id
        with pytest.raises(IntegrityError):
            async with session.begin_nested():
                await session.execute(
                    sa.update(EmbeddingSpace)
                    .where(EmbeddingSpace.id == first.id)
                    .values(recipe="changed")
                )

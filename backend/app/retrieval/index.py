import argparse
import asyncio
from collections.abc import Sequence
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import Settings
from app.db import models  # noqa: F401
from app.db.identity import ModelArtifact
from app.db.knowledge import Chunk, ChunkEmbedding, DocumentVersion, EmbeddingSpace
from app.db.repositories import EmbeddingRepository
from app.db.session import session_factory
from app.retrieval.providers import EmbeddingProvider, embedding_identity, embedding_provider


async def index_batch(
    sessions: async_sessionmaker[AsyncSession],
    space_id: UUID,
    provider: EmbeddingProvider,
    *,
    limit: int = 32,
    document_version_ids: Sequence[UUID] = (),
) -> int:
    missing = ~sa.exists().where(
        ChunkEmbedding.chunk_id == Chunk.id, ChunkEmbedding.embedding_space_id == space_id
    )
    query = (
        sa.select(Chunk.id, Chunk.content)
        .join(DocumentVersion, DocumentVersion.id == Chunk.document_version_id)
        .where(missing, sa.not_(Chunk.attributes.contains({"parent_chunk": True})))
        .order_by(Chunk.created_at, Chunk.ordinal, Chunk.id)
        .limit(limit)
    )
    if document_version_ids:
        query = query.where(Chunk.document_version_id.in_(document_version_ids))
    else:
        query = query.where(DocumentVersion.is_current.is_(True))
    async with sessions() as session:
        space = await session.get(EmbeddingSpace, space_id)
        if space is None:
            raise LookupError("Embedding space does not exist")
        model = await session.get(ModelArtifact, space.model_id)
        if model is None or embedding_identity(space, model) != provider.identity:
            raise ValueError("Embedding provider does not match the selected embedding space")
        chunks = (await session.execute(query)).tuples().all()
    if not chunks:
        return 0
    vectors = await provider.embed([text for _, text in chunks], "document")
    async with sessions.begin() as session:
        repository = EmbeddingRepository(session)
        for (chunk_id, _), vector in zip(chunks, vectors, strict=True):
            await repository.put(space_id, chunk_id, vector)
    return len(chunks)


async def main(key: str, version: int) -> None:
    settings = Settings()
    engine = create_async_engine(settings.database_url)
    sessions = session_factory(engine)
    try:
        async with sessions() as session:
            row = (
                await session.execute(
                    sa.select(EmbeddingSpace, ModelArtifact)
                    .join(ModelArtifact, ModelArtifact.id == EmbeddingSpace.model_id)
                    .where(EmbeddingSpace.key == key, EmbeddingSpace.version == version)
                )
            ).one_or_none()
            if row is None:
                raise LookupError("Embedding space version does not exist")
            space, model = row
        provider = embedding_provider(embedding_identity(space, model), settings)
        total = 0
        while count := await index_batch(sessions, space.id, provider):
            total += count
        print(f"Indexed {total} chunks in {key} version {version}")
    finally:
        await engine.dispose()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Index current document chunks into an embedding space."
    )
    parser.add_argument("--space", default="minilm")
    parser.add_argument("--version", type=int, default=1)
    args = parser.parse_args()
    asyncio.run(main(args.space, args.version))

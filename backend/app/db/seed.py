import asyncio
from typing import Any
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from app.core.config import Settings
from app.db.execution import RAGConfiguration
from app.db.identity import ModelArtifact, Role
from app.db.knowledge import EmbeddingSpace
from app.db.session import session_factory

LOCAL_EMBEDDING_MODEL = "sentence-transformers/all-MiniLM-L6-v2"
LOCAL_EMBEDDING_REVISION = "1110a243fdf4706b3f48f1d95db1a4f5529b4d41"
LOCAL_EMBEDDING_DIMENSIONS = 384


async def register_model(
    session: AsyncSession,
    *,
    provider: str,
    kind: str,
    name: str,
    version: str,
    dimensions: int | None = None,
) -> UUID:
    model = ModelArtifact
    await session.execute(
        insert(model)
        .values(provider=provider, kind=kind, name=name, version=version, dimensions=dimensions)
        .on_conflict_do_nothing(
            index_elements=[model.provider, model.kind, model.name, model.version]
        )
    )
    record = (
        await session.scalars(
            sa.select(model).where(
                model.provider == provider,
                model.kind == kind,
                model.name == name,
                model.version == version,
            )
        )
    ).one()
    if record.dimensions != dimensions:
        raise ValueError("A registered model revision already has different dimensions")
    return record.id


async def seed_defaults(session: AsyncSession) -> None:
    await session.execute(
        insert(Role)
        .values(
            [
                {
                    "key": "admin",
                    "description": "Administers the knowledge corpus and reviews proposals.",
                },
                {"key": "researcher", "description": "Runs queries and experiments."},
            ]
        )
        .on_conflict_do_nothing(index_elements=[Role.key])
    )
    embedding = await register_model(
        session,
        provider="sentence-transformers",
        kind="embedding",
        name=LOCAL_EMBEDDING_MODEL,
        version=LOCAL_EMBEDDING_REVISION,
        dimensions=LOCAL_EMBEDDING_DIMENSIONS,
    )
    await session.execute(
        insert(EmbeddingSpace)
        .values(
            key="minilm",
            version=1,
            model_id=embedding,
            dimensions=LOCAL_EMBEDDING_DIMENSIONS,
            metric="cosine",
            recipe="normalize-v1",
        )
        .on_conflict_do_nothing(index_elements=[EmbeddingSpace.key, EmbeddingSpace.version])
    )
    space = (
        await session.scalars(
            sa.select(EmbeddingSpace).where(
                EmbeddingSpace.key == "minilm", EmbeddingSpace.version == 1
            )
        )
    ).one()
    primary_name = "gemini-3.5-flash-lite"
    teacher_name = "gemini-3.6-flash"
    primary = await register_model(
        session, provider="gemini", kind="llm", name=primary_name, version=primary_name
    )
    teacher = await register_model(
        session, provider="gemini", kind="llm", name=teacher_name, version=teacher_name
    )
    presets: list[dict[str, Any]] = [
        {
            "key": "naive",
            "strategy": "naive",
            "model_id": primary,
            "settings": {"retrieval": "vector", "top_k": 5},
        },
        {
            "key": "advanced",
            "strategy": "advanced",
            "model_id": primary,
            "settings": {"retrieval": "hybrid", "top_k": 8, "candidate_k": 25, "rrf_k": 60},
        },
        {
            "key": "teacher",
            "strategy": "advanced",
            "model_id": teacher,
            "settings": {"retrieval": "hybrid", "top_k": 12, "candidate_k": 35, "rrf_k": 50},
        },
    ]
    await session.execute(
        insert(RAGConfiguration)
        .values([dict(preset, version=1, embedding_space_id=space.id) for preset in presets])
        .on_conflict_do_nothing(index_elements=[RAGConfiguration.key, RAGConfiguration.version])
    )


async def main() -> None:
    engine = create_async_engine(Settings().database_url)
    try:
        async with session_factory(engine).begin() as session:
            await seed_defaults(session)
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())

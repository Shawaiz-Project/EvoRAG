import asyncio

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from app.core.config import Settings
from app.db.execution import RAGConfiguration
from app.db.repositories import Repository
from app.db.session import session_factory
from app.rag.strategies import STRATEGIES


async def seed(session: AsyncSession) -> None:
    await session.execute(sa.text("SELECT pg_advisory_xact_lock(hashtext('workflow-presets'))"))
    configs = Repository(session, RAGConfiguration)
    active = (
        await session.scalars(
            sa.select(RAGConfiguration)
            .where(RAGConfiguration.key == "advanced")
            .order_by(RAGConfiguration.version.desc())
            .limit(1)
        )
    ).one()
    for strategy in STRATEGIES.keys() - {"advanced"}:
        existing = await session.scalar(
            sa.select(RAGConfiguration)
            .where(RAGConfiguration.key == strategy)
            .order_by(RAGConfiguration.version.desc())
            .limit(1)
        )
        if (
            existing is not None
            and existing.model_id == active.model_id
            and existing.embedding_space_id == active.embedding_space_id
            and existing.settings == active.settings
        ):
            continue
        await configs.add(
            RAGConfiguration(
                key=strategy,
                strategy=strategy,
                version=(existing.version if existing else 0) + 1,
                model_id=active.model_id,
                embedding_space_id=active.embedding_space_id,
                settings=active.settings,
            )
        )


async def main() -> None:
    engine = create_async_engine(Settings().database_url)
    try:
        async with session_factory(engine).begin() as session:
            await seed(session)
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())

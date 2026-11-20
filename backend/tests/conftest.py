from collections.abc import AsyncIterator, Callable
from pathlib import Path
from uuid import uuid4

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from app.core.config import Settings


@pytest.fixture(scope="module")
def anyio_backend() -> str:
    return "asyncio"


async def migrate(
    engine: AsyncEngine, operation: Callable[[Config, str], None], revision: str
) -> None:
    config = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
    async with engine.begin() as connection:

        def run(sync_connection: sa.Connection) -> None:
            config.attributes["connection"] = sync_connection
            operation(config, revision)

        await connection.run_sync(run)


@pytest.fixture
async def empty_database(anyio_backend: str) -> AsyncIterator[AsyncEngine]:
    database = f"evorag_test_{uuid4().hex}"
    base_url = Settings().database_url
    admin = create_async_engine(base_url.set(database="postgres"), isolation_level="AUTOCOMMIT")
    try:
        async with admin.connect() as connection:
            await connection.execute(sa.text(f'CREATE DATABASE "{database}" TEMPLATE template0'))
        engine = create_async_engine(base_url.set(database=database))
        try:
            yield engine
        finally:
            await engine.dispose()
            async with admin.connect() as connection:
                await connection.execute(sa.text(f'DROP DATABASE "{database}"'))
    finally:
        await admin.dispose()


@pytest.fixture
async def database(empty_database: AsyncEngine) -> AsyncEngine:
    await migrate(empty_database, command.upgrade, "head")
    return empty_database

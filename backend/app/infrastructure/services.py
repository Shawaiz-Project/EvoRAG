import asyncio
import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import AsyncExitStack, asynccontextmanager
from dataclasses import dataclass

from asyncpg.exceptions import InterfaceError as PostgresInterfaceError
from asyncpg.exceptions import PostgresError
from redis.asyncio import Redis
from redis.exceptions import RedisError
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.core.config import Settings
from app.db.session import session_factory

logger = logging.getLogger(__name__)
PROBE_TIMEOUT_SECONDS = 2


@dataclass(frozen=True, slots=True)
class Services:
    postgres: AsyncEngine
    redis: Redis
    sessions: async_sessionmaker[AsyncSession]

    async def check_postgres(self) -> bool:
        async with self.postgres.connect() as connection:
            result = await connection.scalar(text("SELECT '[1]'::vector IS NOT NULL"))
        return result is True

    async def check_redis(self) -> bool:
        return bool(await self.redis.ping())

    async def readiness(self) -> tuple[bool, bool]:
        postgres, redis = await asyncio.gather(
            probe("postgres", self.check_postgres),
            probe("redis", self.check_redis),
        )
        return postgres, redis


async def probe(name: str, check: Callable[[], Awaitable[bool]]) -> bool:
    try:
        async with asyncio.timeout(PROBE_TIMEOUT_SECONDS):
            return await check()
    except (
        TimeoutError,
        OSError,
        PostgresError,
        PostgresInterfaceError,
        SQLAlchemyError,
        RedisError,
    ) as error:
        logger.warning("%s readiness check failed (%s)", name, type(error).__name__)
        return False


@asynccontextmanager
async def open_services(settings: Settings) -> AsyncIterator[Services]:
    async with AsyncExitStack() as stack:
        postgres = create_async_engine(settings.database_url, pool_pre_ping=True)
        stack.push_async_callback(postgres.dispose)
        redis = Redis(
            host=settings.redis_host,
            port=settings.redis_port,
            socket_connect_timeout=PROBE_TIMEOUT_SECONDS,
            socket_timeout=PROBE_TIMEOUT_SECONDS,
        )
        stack.push_async_callback(redis.aclose)
        yield Services(postgres=postgres, redis=redis, sessions=session_factory(postgres))

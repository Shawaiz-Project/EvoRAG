import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.auth import router as auth_router
from app.api.evaluation import router as evaluation_router
from app.api.health import router
from app.api.ingestion import router as ingestion_router
from app.api.learning import router as learning_router
from app.api.operations import router as operations_router
from app.api.rag import router as rag_router
from app.api.teacher import router as teacher_router
from app.core.config import Settings
from app.core.logging import configure_logging
from app.infrastructure.services import open_services
from app.rag.engine import RAGEngine

logger = logging.getLogger(__name__)


def create_app(settings: Settings | None = None) -> FastAPI:
    config = settings if settings is not None else Settings()
    configure_logging(config.log_level)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        async with open_services(config) as services:
            app.state.services = services
            app.state.rag = RAGEngine(services.sessions, config)
            logger.info("EvoRAG API started")
            try:
                yield
            finally:
                logger.info("EvoRAG API shutting down")

    app = FastAPI(title="EvoRAG", lifespan=lifespan)
    app.state.settings = config
    app.add_middleware(
        CORSMiddleware,
        allow_origins=config.cors_origins,
        allow_methods=["GET", "POST"],
        allow_headers=["Authorization"],
        allow_credentials=True,
    )
    app.include_router(router)
    app.include_router(ingestion_router)
    app.include_router(learning_router)
    app.include_router(auth_router)
    app.include_router(rag_router)
    app.include_router(teacher_router)
    app.include_router(evaluation_router)
    app.include_router(operations_router)
    return app

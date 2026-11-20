import argparse
import asyncio

import aiohttp
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import create_async_engine

from app.core.config import Settings
from app.core.ollama import model_digests
from app.db.execution import RAGConfiguration
from app.db.knowledge import EmbeddingSpace
from app.db.repositories import Repository
from app.db.seed import register_model
from app.db.session import session_factory


async def register(
    primary: str, teacher: str, embed_model: str, dimensions: int, extractive: bool
) -> None:
    settings = Settings()
    async with aiohttp.ClientSession(
        timeout=aiohttp.ClientTimeout(total=10), trust_env=False
    ) as client:
        installed = await model_digests(client, settings.ollama_url)
    if {primary, teacher, embed_model} - installed.keys():
        raise ValueError("Pull the configured Ollama models before registering their profile")

    engine = create_async_engine(settings.database_url)
    try:
        async with session_factory(engine).begin() as session:
            await session.execute(
                sa.text("SELECT pg_advisory_xact_lock(hashtext('ollama-profile'))")
            )
            embed_id = await register_model(
                session,
                provider="ollama",
                kind="embedding",
                name=embed_model,
                version=installed[embed_model],
                dimensions=dimensions,
            )
            space = await session.scalar(
                sa.select(EmbeddingSpace).where(
                    EmbeddingSpace.key == "ollama",
                    EmbeddingSpace.model_id == embed_id,
                    EmbeddingSpace.recipe == "ollama-v1",
                )
            )
            if space is None:
                latest = await session.scalar(
                    sa.select(sa.func.max(EmbeddingSpace.version)).where(
                        EmbeddingSpace.key == "ollama"
                    )
                )
                space = await Repository(session, EmbeddingSpace).add(
                    EmbeddingSpace(
                        key="ollama",
                        version=(latest or 0) + 1,
                        model_id=embed_id,
                        dimensions=dimensions,
                        metric="cosine",
                        recipe="ollama-v1",
                    )
                )
            for key, strategy, name, retrieval, top_k in (
                ("naive", "naive", primary, "vector", 4),
                ("advanced", "advanced", primary, "hybrid", 5),
                ("teacher", "advanced", teacher, "hybrid", 8),
            ):
                model_id = await register_model(
                    session, provider="ollama", kind="llm", name=name, version=installed[name]
                )
                latest_config = await session.scalar(
                    sa.select(RAGConfiguration)
                    .where(
                        RAGConfiguration.key == key,
                    )
                    .order_by(RAGConfiguration.version.desc())
                    .limit(1)
                )
                options = {
                    "retrieval": retrieval,
                    "top_k": top_k,
                    "candidate_k": 16,
                    "context_budget": 4000,
                    "answer_tokens": 512,
                    "answer_style": "excerpt" if extractive and key != "teacher" else "claims",
                }
                if (
                    latest_config
                    and latest_config.model_id == model_id
                    and latest_config.embedding_space_id == space.id
                    and latest_config.settings == options
                ):
                    continue
                await Repository(session, RAGConfiguration).add(
                    RAGConfiguration(
                        key=key,
                        strategy=strategy,
                        version=(latest_config.version if latest_config else 0) + 1,
                        model_id=model_id,
                        embedding_space_id=space.id,
                        settings=options,
                    )
                )
            print(f"Registered active Ollama profiles and embedding space ollama v{space.version}")
    finally:
        await engine.dispose()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Activate versioned Ollama profiles.")
    parser.add_argument("--primary", required=True)
    parser.add_argument("--teacher", required=True)
    parser.add_argument("--embedding", default="all-minilm:latest")
    parser.add_argument("--dimensions", default=384, type=int)
    parser.add_argument("--extractive", action="store_true")
    args = parser.parse_args()
    asyncio.run(
        register(args.primary, args.teacher, args.embedding, args.dimensions, args.extractive)
    )

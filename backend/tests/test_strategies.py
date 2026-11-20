import os
import sqlite3
from collections.abc import Sequence
from hashlib import sha256
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine

from app.core.config import Settings
from app.db.execution import RAGConfiguration
from app.db.knowledge import Chunk, EmbeddingSpace
from app.db.repositories import DocumentRepository, Repository
from app.db.seed import register_model
from app.db.session import session_factory
from app.rag.engine import Question, RAGEngine, adaptive_depth, choose_strategy
from app.rag.strategies import Context, execute
from app.retrieval.index import index_batch
from app.retrieval.providers import EmbeddingIdentity, Purpose
from app.retrieval.search import Filters


class FixtureVectors:
    identity = EmbeddingIdentity("test", "strategy-fixture", "1", 2, "fixture-v1")

    async def embed(self, texts: Sequence[str], purpose: Purpose) -> list[list[float]]:
        return [[1, 0] for _ in texts]


def test_budget_changes_routing() -> None:
    assert choose_strategy("Compare version ancestry and rollback", 1) == "advanced"
    assert choose_strategy("Compare version ancestry and rollback", 3) == "agentic"
    assert choose_strategy("How do revisions relate to document lineage?", 3) == "graph"


@pytest.mark.parametrize(
    ("question", "filters", "category", "depth"),
    [
        ("What is RRF?", Filters(), "lookup", 3),
        ("How does ranking work?", Filters(), "technical", 7),
        ("Compare the methods", Filters(), "multi_document", 12),
        ("List the models", Filters(document_ids=[uuid4(), uuid4()]), "multi_document", 12),
        ("What is RRF? Why use it?", Filters(), "research", 12),
    ],
)
def test_adaptive_depth_respects_intent_and_profile_cap(
    question: str, filters: Filters, category: str, depth: int
) -> None:
    config = RAGConfiguration(
        key="advanced",
        version=2,
        strategy="advanced",
        settings={
            "candidate_k": 12,
            "top_k": 5,
            "adaptive_depths": {"lookup": 3, "technical": 7, "research": 20},
        },
    )
    request = Question(question=question, filters=filters, configuration="auto")
    selected, detail = adaptive_depth(request, config)
    assert detail == {"category": category, "top_k": depth, "configuration": "advanced"}
    assert selected.settings["top_k"] == depth
    assert selected.version == config.version
    assert config.settings["top_k"] == 5


@pytest.mark.anyio
@pytest.mark.skipif(os.getenv("EVORAG_DB_TESTS") != "1", reason="requires PostgreSQL")
async def test_same_question_uses_distinct_workflows(database: AsyncEngine, tmp_path: Path) -> None:
    sessions = session_factory(database)
    async with sessions.begin() as session:
        documents = DocumentRepository(session)
        source = await documents.source("file", "workflow:fixture", "Fixture")
        doc = await documents.document(source.id, "history.txt", "Version history")
        raw = b"Document lineage tracks previous versions and records the current revision."
        version = await documents.add_version(
            doc.id,
            sha256=sha256(raw).hexdigest(),
            size_bytes=len(raw),
            mime_type="text/plain",
            extracted_text=raw.decode(),
            make_current=True,
        )
        repo = Repository(session, Chunk)
        parent = await repo.add(
            Chunk(
                document_version_id=version.record.id,
                ordinal=0,
                content=raw.decode(),
                attributes={"parent_chunk": True},
            )
        )
        await repo.add(
            Chunk(
                document_version_id=version.record.id,
                ordinal=1,
                parent_id=parent.id,
                content="Document lineage records previous versions.",
            )
        )
        model = await register_model(
            session,
            provider="test",
            kind="embedding",
            name="strategy-fixture",
            version="1",
            dimensions=2,
        )
        space = await Repository(session, EmbeddingSpace).add(
            EmbeddingSpace(
                key="fixture",
                version=1,
                model_id=model,
                dimensions=2,
                metric="cosine",
                recipe="fixture-v1",
            )
        )
    assert await index_batch(sessions, space.id, FixtureVectors()) == 1
    db_path = tmp_path / "checkpoints.sqlite"
    settings = Settings().model_copy(update={"workflow_checkpoint_db": db_path})
    engine = RAGEngine(sessions, settings, embedder=lambda identity, config: FixtureVectors())
    question = "How does document lineage record previous versions?"
    stages = {}
    for strategy in ("advanced", "graph", "parent-child"):
        config = RAGConfiguration(
            key=strategy,
            version=1,
            strategy=strategy,
            settings={"top_k": 3},
            embedding_space_id=space.id,
        )
        result = await execute(Context(engine, config, question, Filters(), 3), uuid4())
        assert result.hits and result.workflow[0] == strategy
        stages[strategy] = set(result.stages)
        if strategy == "parent-child":
            assert result.hits[0].context == raw.decode()
    assert "vector:0" in stages["advanced"]
    assert "graph" in stages["graph"]
    assert "bm25:0" in stages["parent-child"]
    assert len({frozenset(value) for value in stages.values()}) == 3
    with sqlite3.connect(db_path) as conn:
        assert conn.execute("SELECT count(DISTINCT thread_id) FROM checkpoints").fetchone()[0] == 3

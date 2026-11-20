import json
import os
from collections.abc import Sequence
from hashlib import sha256
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine

from app.db.knowledge import Chunk, EmbeddingSpace
from app.db.repositories import DocumentRepository, EmbeddingRepository, Repository
from app.db.seed import register_model
from app.db.session import session_factory
from app.retrieval.index import index_batch
from app.retrieval.providers import EmbeddingIdentity, Purpose
from app.retrieval.search import (
    Filters,
    Hit,
    PostgresRetriever,
    SearchOptions,
    query_variants,
    reciprocal_rank_fusion,
)


class FixtureProvider:
    identity = EmbeddingIdentity("fixture", "retrieval-v1", "1", 4, "fixed-v1")

    def __init__(self, fixture: dict) -> None:
        self.by_text = {
            record["text"]: record["vector"] for record in fixture["documents"] + fixture["queries"]
        }

    async def embed(self, texts: Sequence[str], purpose: Purpose) -> list[list[float]]:
        return [self.by_text[text] for text in texts]


def test_query_variants_and_rank_fusion() -> None:
    query = "What is reciprocal rank fusion and also how do I calculate it?"
    variants = query_variants(query, SearchOptions(multi_query=True, rewrite=True))
    assert variants[0] == query
    assert "reciprocal rank fusion" in variants
    base = dict(
        document_version_id=uuid4(),
        document_id=uuid4(),
        source_id=uuid4(),
        title="Test",
        locator="test",
        content="Text",
        page=None,
        section=None,
        parent_id=None,
        score=1,
    )
    hits = [Hit(chunk_id=uuid4(), **base) for _ in range(3)]
    fused = reciprocal_rank_fusion([[hits[0], hits[1]], [hits[2], hits[1]]], damping=60)
    assert fused[0].chunk_id == hits[1].chunk_id
    assert fused[0].score == pytest.approx(2 / 62)


@pytest.mark.anyio
@pytest.mark.skipif(os.getenv("EVORAG_DB_TESTS") != "1", reason="requires PostgreSQL")
async def test_fixed_retrieval_benchmark(database: AsyncEngine) -> None:
    fixture = json.loads(
        (Path(__file__).parent / "fixtures" / "retrieval_v1.json").read_text(encoding="utf-8")
    )
    sessions = session_factory(database)
    provider = FixtureProvider(fixture)
    async with sessions.begin() as session:
        documents = DocumentRepository(session)
        source = await documents.source("file", "test:retrieval", "Retrieval")
        for entry in fixture["documents"]:
            doc = await documents.document(source.id, entry["key"], entry["key"])
            raw = entry["text"].encode()
            version = await documents.add_version(
                doc.id,
                sha256=sha256(raw).hexdigest(),
                size_bytes=len(raw),
                mime_type="text/plain",
                extracted_text=entry["text"],
                make_current=True,
            )
            parent = await Repository(session, Chunk).add(
                Chunk(
                    document_version_id=version.record.id,
                    ordinal=0,
                    section="Parent",
                    content="Parent context. " + entry["text"],
                    attributes={"parent_chunk": True},
                ),
            )
            leaf = await Repository(session, Chunk).add(
                Chunk(
                    document_version_id=version.record.id,
                    ordinal=1,
                    parent_id=parent.id,
                    content=entry["text"],
                    page=1,
                    section=entry["key"],
                ),
            )
        model_id = await register_model(
            session,
            provider="fixture",
            kind="embedding",
            name="retrieval-v1",
            version="1",
            dimensions=4,
        )
        space = await Repository(session, EmbeddingSpace).add(
            EmbeddingSpace(
                key="fixture",
                version=1,
                model_id=model_id,
                dimensions=4,
                metric="cosine",
                recipe="fixed-v1",
            ),
        )
        other_model = await register_model(
            session,
            provider="fixture",
            kind="embedding",
            name="incompatible",
            version="1",
            dimensions=2,
        )
        other_space = await Repository(session, EmbeddingSpace).add(
            EmbeddingSpace(
                key="fixture",
                version=2,
                model_id=other_model,
                dimensions=2,
                metric="cosine",
                recipe="other",
            ),
        )
        await EmbeddingRepository(session).put(other_space.id, leaf.id, [1, 0])
    assert await index_batch(sessions, space.id, provider) == 6
    assert await index_batch(sessions, space.id, provider) == 0
    with pytest.raises(ValueError, match="does not match"):
        await index_batch(sessions, other_space.id, provider)

    benchmark: dict[str, dict] = {}
    async with sessions() as session:
        search = PostgresRetriever(
            session, space=space, identity=provider.identity, provider=provider
        )
        for mode in ("keyword", "bm25", "vector", "hybrid"):
            placements = []
            for query in fixture["queries"]:
                results = await search.retrieve(
                    query["text"], SearchOptions(retrieval=mode, top_k=3, parent_child=True)
                )
                keys = [hit.locator for hit in results.hits]
                assert "final" in results.stages
                assert all(hit.context and hit.context.startswith("Parent") for hit in results.hits)
                placements.append(
                    keys.index(query["relevant"]) + 1 if query["relevant"] in keys else None
                )
            benchmark[mode] = {
                "ranks": placements,
                "recall_at_3": sum(rank is not None for rank in placements) / len(placements),
                "mrr_at_3": sum(1 / rank for rank in placements if rank) / len(placements),
            }
        picked = await search.retrieve(
            fixture["queries"][0]["text"],
            SearchOptions(retrieval="vector", vector_min_score=0.9),
            Filters(page=1, section="vectors"),
        )
        assert [item.locator for item in picked.hits] == ["vectors"]
        assert picked.space_id == space.id
    print(json.dumps(benchmark, sort_keys=True))
    assert benchmark["vector"]["ranks"] == [1, 1, 1, 1]
    assert benchmark["hybrid"]["recall_at_3"] == 1.0

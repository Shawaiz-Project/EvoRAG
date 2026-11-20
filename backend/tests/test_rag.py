import json
import os
from hashlib import sha256
from secrets import token_urlsafe
from uuid import uuid4

import pytest
from httpx2 import ASGITransport, AsyncClient
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncEngine

from app.api.rag import get_rag
from app.auth import encode_password
from app.core.config import Settings
from app.db.execution import RAGConfiguration
from app.db.identity import ModelArtifact, User
from app.db.knowledge import Chunk
from app.db.repositories import DocumentRepository, Repository
from app.db.session import session_factory
from app.main import create_app
from app.rag.engine import RAGEngine
from app.rag.evidence import ABSTENTION, Claim, Draft, EvidenceQuote, context, validate_draft
from app.rag.providers import Generation
from app.retrieval.search import Hit

QUOTE = "Reciprocal rank fusion sums inverse rank contributions."


class ControlledGenerator:
    async def generate[Output: BaseModel](
        self, instructions: str, prompt: str, schema: type[Output], max_tokens: int
    ) -> Generation[Output]:
        evidence = json.loads(prompt)["evidence"][0]
        draft = {
            "abstain": False,
            "claims": [
                {
                    "text": QUOTE,
                    "citations": [
                        {"source": evidence["source"], "quote": QUOTE},
                    ],
                }
            ],
        }
        return Generation(schema.model_validate(draft), "fixture-1", 21, 9)


def test_unknown_sources_and_mismatched_quotes_abstain() -> None:
    hit = Hit(
        chunk_id=uuid4(),
        document_version_id=uuid4(),
        document_id=uuid4(),
        source_id=uuid4(),
        title="Fixture",
        locator="notes",
        content=QUOTE,
        page=1,
        section=None,
        parent_id=None,
        score=1.0,
    )
    passages = context([hit], 1024)
    for source, passage in [("S9", QUOTE), ("S1", "This passage was not retrieved.")]:
        draft = Draft(
            abstain=False,
            claims=[
                Claim(
                    text="Invalid claim", citations=[EvidenceQuote(source=source, quote=passage)]
                ),
            ],
        )
        assert validate_draft(draft, passages) == (ABSTENTION, [], False)


@pytest.mark.anyio
@pytest.mark.skipif(os.getenv("EVORAG_DB_TESTS") != "1", reason="requires PostgreSQL")
async def test_authenticated_stream_and_trace(database: AsyncEngine) -> None:
    settings = Settings().model_copy(update={"postgres_db": database.url.database})
    sessions = session_factory(database)
    password = token_urlsafe(21)
    async with sessions.begin() as session:
        await Repository(session, User).add(
            User(
                email="researcher@example.test",
                display_name="Researcher",
                password_hash=encode_password(password),
            )
        )
        model = await Repository(session, ModelArtifact).add(
            ModelArtifact(kind="llm", provider="test", name="controlled", version="fixture-1")
        )
        await Repository(session, RAGConfiguration).add(
            RAGConfiguration(
                key="fixture",
                version=1,
                strategy="naive",
                model_id=model.id,
                settings={"retrieval": "bm25"},
            )
        )
        documents = DocumentRepository(session)
        source = await documents.source("file", "test:rag", "RAG")
        doc = await documents.document(source.id, "rrf", "Fusion")
        data = QUOTE.encode()
        version = await documents.add_version(
            doc.id,
            sha256=sha256(data).hexdigest(),
            size_bytes=len(data),
            mime_type="text/plain",
            extracted_text=QUOTE,
            make_current=True,
        )
        chunk = await Repository(session, Chunk).add(
            Chunk(document_version_id=version.record.id, ordinal=0, page=1, content=QUOTE)
        )

    app = create_app(settings)
    app.dependency_overrides[get_rag] = lambda: RAGEngine(
        sessions, settings, generator=lambda model, runtime: ControlledGenerator()
    )
    async with (
        app.router.lifespan_context(app),
        AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client,
    ):
        payload = {"question": "How does reciprocal rank fusion work?", "configuration": "fixture"}
        assert (await client.post("/api/v1/rag/query", json=payload)).status_code == 401
        sign_in = await client.post(
            "/api/v1/auth/login",
            json={
                "email": "researcher@example.test",
                "password": password,
            },
        )
        assert sign_in.status_code == 200, sign_in.text
        assert "HttpOnly" in sign_in.headers["set-cookie"]
        response = await client.post("/api/v1/rag/query/stream", json=payload)
        assert response.status_code == 200, response.text
        frames = response.text.split("\n\n")
        assert any(frame.startswith("event: delta") for frame in frames)
        done = next(frame for frame in frames if frame.startswith("event: done"))
        answer = json.loads(done.split("data: ", 1)[1])
        assert answer["status"] == "completed"
        assert answer["citations_verified"]
        assert answer["citations"][0]["chunk_id"] == str(chunk.id)
        trace = (await client.get(f"/api/v1/rag/runs/{answer['run_id']}")).json()
        assert trace["status"] == "completed"
        assert trace["retrieval"][0]["chunk_id"] == str(chunk.id)
        history = (
            await client.get(f"/api/v1/conversations/{answer['conversation_id']}/messages")
        ).json()
        assert [item["role"] for item in history] == ["user", "assistant"]
        assert (await client.post("/api/v1/auth/logout")).status_code == 204
        assert (await client.get("/api/v1/auth/me")).status_code == 401

        fallback = await client.post(
            "/api/v1/auth/token",
            json={"email": "researcher@example.test", "password": password},
        )
        assert fallback.status_code == 200
        assert fallback.headers["cache-control"] == "no-store"
        assert "set-cookie" not in fallback.headers
        headers = {"Authorization": f"Bearer {fallback.json()['access_token']}"}
        assert (await client.get("/api/v1/auth/me", headers=headers)).status_code == 200
        assert (await client.get("/api/v1/rag/configurations", headers=headers)).status_code == 200
        assert (await client.post("/api/v1/auth/logout", headers=headers)).status_code == 204
        assert (await client.get("/api/v1/auth/me", headers=headers)).status_code == 401

import os
from hashlib import sha256

import pytest
import sqlalchemy as sa
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncEngine

from app.core.config import Settings
from app.db.benchmarks import BenchmarkCase, BenchmarkDataset, BenchmarkEvidence
from app.db.execution import RAGConfiguration
from app.db.governance import AuditLog
from app.db.identity import ModelArtifact, User
from app.db.knowledge import Chunk, DocumentVersion
from app.db.repositories import DocumentRepository, Repository
from app.db.session import session_factory
from app.learning import Correction, FeedbackInput, LearningEngine, LearningError, ProposalCheck
from app.rag.engine import Question, RAGEngine
from app.rag.providers import Generation
from app.retrieval.search import Filters

OLD = "The test catalog labels available inventory as cobalt."
NEW = "The test catalog labels available inventory as indigo."


class FixtureTeacher:
    async def generate[Output: BaseModel](
        self, instructions: str, prompt: str, schema: type[Output], max_tokens: int
    ) -> Generation[Output]:
        assert schema is ProposalCheck
        return Generation(
            schema.model_validate({"conflict": True, "reason": "Review both declared labels."}),
            "test-v1",
            19,
            10,
        )


@pytest.mark.anyio
@pytest.mark.skipif(os.getenv("EVORAG_DB_TESTS") != "1", reason="requires PostgreSQL")
async def test_learning_review_publish_and_rollback(database: AsyncEngine) -> None:
    sessions = session_factory(database)
    async with sessions.begin() as session:
        actor = await Repository(session, User).add(
            User(
                email="learning@example.test",
                display_name="Researcher",
            )
        )
        reviewer = await Repository(session, User).add(
            User(
                email="admin-learning@example.test",
                display_name="Reviewer",
            )
        )
        documents = DocumentRepository(session)
        source = await documents.source("file", "test:learning", "Test corrections")
        doc = await documents.document(source.id, "catalog.txt", "Catalog")
        version = await documents.add_version(
            doc.id,
            sha256=sha256(OLD.encode()).hexdigest(),
            size_bytes=len(OLD),
            mime_type="text/plain",
            extracted_text=OLD,
            make_current=True,
        )
        original = version.record
        chunk = await Repository(session, Chunk).add(
            Chunk(
                document_version_id=original.id,
                ordinal=0,
                content=OLD,
            )
        )
        model = await Repository(session, ModelArtifact).add(
            ModelArtifact(
                provider="test",
                kind="llm",
                name="teacher",
                version="test-v1",
            )
        )
        config = await Repository(session, RAGConfiguration).add(
            RAGConfiguration(
                key="naive",
                version=1,
                strategy="naive",
                settings={"retrieval": "bm25"},
            )
        )
        await Repository(session, RAGConfiguration).add(
            RAGConfiguration(
                key="teacher",
                version=1,
                strategy="advanced",
                settings={"retrieval": "bm25"},
                model_id=model.id,
            )
        )
        dataset = await Repository(session, BenchmarkDataset).add(
            BenchmarkDataset(
                key="inventory",
                version=1,
                description="Expected inventory label",
            )
        )
        case = await Repository(session, BenchmarkCase).add(
            BenchmarkCase(
                dataset_id=dataset.id,
                key="label",
                question="What is the available label?",
                golden_answer="indigo",
            )
        )
        session.add(BenchmarkEvidence(case_id=case.id, chunk_id=chunk.id))

    engine = RAGEngine(sessions, Settings(), generator=lambda model, settings: FixtureTeacher())
    learning = LearningEngine(engine)
    question = "What label does the catalog use for available inventory?"
    run, _, _ = await engine.start(actor.id, Question(question=question, configuration="naive"))
    await engine.finish(run, "completed", OLD, {})

    def change(text: str) -> FeedbackInput:
        return FeedbackInput(
            correction=Correction(
                version_id=original.id,
                replace_text=OLD,
                new_text=text,
            )
        )

    _, wrong = await learning.feedback(actor.id, run.id, change("The label is red."))
    assert wrong is not None
    rejected = await learning.verify(reviewer.id, wrong)
    assert rejected.attributes["verification"]["benchmark"] == {"cases_checked": 1, "passed": False}
    with pytest.raises(LearningError, match="benchmarks"):
        await learning.decide(reviewer.id, wrong, "approve", "Check failed.")
    assert (
        await learning.decide(reviewer.id, wrong, "reject", "Incorrect label.")
    ).status == "rejected"

    _, proposed = await learning.feedback(actor.id, run.id, change(NEW))
    _, repeated = await learning.feedback(actor.id, run.id, change(NEW))
    assert proposed == repeated and proposed is not None
    with pytest.raises(LearningError, match="approval"):
        await learning.publish(reviewer.id, proposed)
    await learning.verify(reviewer.id, proposed)
    await learning.decide(reviewer.id, proposed, "approve", "Verified the catalog correction.")
    published = await learning.publish(reviewer.id, proposed)
    assert published.status == "published"
    search = await engine.retrieve(config, question, Filters(document_ids=[doc.id]))
    assert search.hits[0].content == NEW
    assert search.hits[0].document_version_id == published.published_version_id

    assert (await learning.rollback(reviewer.id, proposed)).status == "rolled_back"
    search = await engine.retrieve(config, question, Filters(document_ids=[doc.id]))
    assert search.hits[0].content == OLD
    async with sessions() as session:
        assert (
            await session.scalar(
                sa.select(sa.func.count())
                .select_from(DocumentVersion)
                .where(DocumentVersion.document_id == doc.id)
            )
            == 2
        )
        actions = set(await session.scalars(sa.select(AuditLog.action)))
        assert {
            "proposal.rejected",
            "proposal.approved",
            "proposal.published",
            "proposal.rolled_back",
        } <= actions

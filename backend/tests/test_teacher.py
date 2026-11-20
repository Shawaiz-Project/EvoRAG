import json
import os
from hashlib import sha256
from uuid import uuid4

import pytest
import sqlalchemy as sa
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncEngine

from app.core.config import Settings
from app.db.execution import RAGConfiguration, RAGRun, RetrievalResult, TeacherEvaluation
from app.db.identity import ModelArtifact, User
from app.db.knowledge import Chunk
from app.db.repositories import DocumentRepository, Repository
from app.db.session import session_factory
from app.rag.engine import Question, RAGEngine
from app.rag.evidence import Draft
from app.rag.providers import Generation
from app.rag.teacher import Critique, RefinementDenied, TeacherEngine
from app.rag.teacher_claims import ProposedAssessment, answer_claims, verify_claims
from app.retrieval.search import Filters

FACT = "The pilot study fixed k at 60."


class FixtureGenerator:
    async def generate[Output: BaseModel](
        self, instructions: str, prompt: str, schema: type[Output], max_tokens: int
    ) -> Generation[Output]:
        payload = json.loads(prompt)
        if schema is Critique:
            if "61" in payload["primary_answer"]:
                verdict = "revise"
            elif "exact value" in payload["question"]:
                verdict = "reject"
            else:
                verdict = "pass"
            result = {
                "verdict": verdict,
                "unsupported": "The cited excerpt supports 60."
                if "61" in payload["primary_answer"]
                else "",
                "missing": "",
                "reason": "The fixture answers the value of k.",
                "correction_query": "pilot study fix k",
                "claim_assessments": [
                    {
                        "claim_id": 1,
                        "status": "contradicted"
                        if "61" in payload["primary_answer"]
                        else "supported",
                        "evidence": [
                            {"identifier": payload["evidence"][0]["identifier"], "quote": FACT}
                        ],
                    }
                ],
            }
        else:
            assert schema is Draft
            result = {
                "abstain": False,
                "claims": [
                    {
                        "text": FACT,
                        "citations": [
                            {"source": payload["evidence"][0]["source"], "quote": FACT},
                        ],
                    },
                ],
            }
        return Generation(schema.model_validate(result), "fixture-1", 30, 10)


@pytest.mark.anyio
@pytest.mark.skipif(os.getenv("EVORAG_DB_TESTS") != "1", reason="requires PostgreSQL")
async def test_human_labeled_teacher_cases_and_refinement(database: AsyncEngine) -> None:
    sessions = session_factory(database)
    async with sessions.begin() as session:
        actor = await Repository(session, User).add(
            User(email="teacher-lab@example.test", display_name="Teacher Lab")
        )
        models = Repository(session, ModelArtifact)
        primary_model = await models.add(
            ModelArtifact(provider="test", kind="llm", name="primary", version="fixture-1")
        )
        teacher_model = await models.add(
            ModelArtifact(provider="test", kind="llm", name="teacher", version="fixture-1")
        )
        presets = Repository(session, RAGConfiguration)
        await presets.add(
            RAGConfiguration(
                key="naive",
                version=1,
                strategy="naive",
                model_id=primary_model.id,
                settings={"retrieval": "bm25"},
            )
        )
        await presets.add(
            RAGConfiguration(
                key="teacher",
                version=1,
                strategy="advanced",
                model_id=teacher_model.id,
                settings={"retrieval": "bm25", "top_k": 4},
            )
        )
        documents = DocumentRepository(session)
        source = await documents.source("file", "teacher:fixture", "Fixture")
        doc = await documents.document(source.id, "rrf.txt", "RRF")
        version = await documents.add_version(
            doc.id,
            sha256=sha256(FACT.encode()).hexdigest(),
            size_bytes=len(FACT.encode()),
            mime_type="text/plain",
            extracted_text=FACT,
            make_current=True,
        )
        chunk = await Repository(session, Chunk).add(
            Chunk(document_version_id=version.record.id, ordinal=0, content=FACT)
        )

    settings = Settings().model_copy(update={"teacher_max_refinements": 1})
    primary = RAGEngine(sessions, settings, generator=lambda model, runtime: FixtureGenerator())
    teacher = TeacherEngine(primary)
    question = Question(question="What value did the pilot study use for k?", configuration="naive")

    correct = await primary.ask(actor.id, question)
    accepted = await teacher.review(actor.id, correct.run_id)
    assert accepted.verdict == "pass"
    assert accepted.teacher_run_id != accepted.primary_run_id
    assert accepted.report["evidence_chunk_ids"] == [str(chunk.id)]
    assert accepted.report["claims"][0]["status"] == "supported"
    assert accepted.report["claims"][0]["evidence"][0]["chunk_id"] == str(chunk.id)
    with pytest.raises(RefinementDenied):
        await teacher.refine(actor.id, accepted.id)

    baseline, _, _ = await primary.start(actor.id, question)
    async with sessions.begin() as session:
        await Repository(session, RetrievalResult).add(
            RetrievalResult(run_id=baseline.id, stage="final", rank=1, chunk_id=chunk.id)
        )
    await primary.finish(
        baseline,
        "completed",
        "The pilot study fixed k at 61. [S1]",
        {
            "citations": [cite.model_dump(mode="json") for cite in correct.citations],
        },
    )
    challenged = await teacher.review(actor.id, baseline.id)
    assert challenged.verdict == "revise"
    assert challenged.report["claims"][0]["status"] == "contradicted"
    assert any("numerical" in problem for problem in challenged.report["integrity_problems"])
    corrected = await teacher.refine(actor.id, challenged.id)
    assert corrected.status == "completed" and "60" in corrected.answer
    with pytest.raises(RefinementDenied, match="Maximum"):
        await teacher.refine(actor.id, challenged.id)

    compound, _, _ = await primary.start(actor.id, question)
    async with sessions.begin() as session:
        await Repository(session, RetrievalResult).add(
            RetrievalResult(run_id=compound.id, stage="final", rank=1, chunk_id=chunk.id)
        )
    await primary.finish(
        compound,
        "completed",
        FACT + " [S1]\n\nThe method always improves ranking accuracy. [S1]",
        {"citations": [cite.model_dump(mode="json") for cite in correct.citations]},
    )
    partial = await teacher.review(actor.id, compound.id)
    assert partial.verdict == "revise"
    assert [item["status"] for item in partial.report["claims"]] == ["supported", "unverified"]

    exact, _, _ = await primary.start(
        actor.id,
        Question(question="What exact value did the pilot study use for k?", configuration="naive"),
    )
    async with sessions.begin() as session:
        await Repository(session, RetrievalResult).add(
            RetrievalResult(run_id=exact.id, stage="final", rank=1, chunk_id=chunk.id)
        )
    await primary.finish(
        exact,
        "completed",
        FACT + " [S1]",
        {
            "citations": [cite.model_dump(mode="json") for cite in correct.citations],
            "answer_style": "excerpt",
        },
    )
    corroborated = await teacher.review(actor.id, exact.id)
    assert corroborated.verdict == "pass"
    assert corroborated.report["model_critique"]["verdict"] == "reject"

    missing = await primary.ask(
        actor.id,
        question.model_copy(
            update={
                "filters": Filters(document_ids=[uuid4()]),
            }
        ),
    )
    rejected = await teacher.review(actor.id, missing.run_id)
    assert rejected.verdict == "reject"
    assert rejected.report["evidence_chunk_ids"] == []

    async with sessions() as session:
        assert await session.scalar(sa.select(sa.func.count()).select_from(TeacherEvaluation)) == 5
        teacher_run = await session.get(RAGRun, accepted.teacher_run_id)
        assert teacher_run.role == "teacher"
        assert teacher_run.configuration_id != baseline.configuration_id


def test_teacher_cannot_support_an_unknown_evidence_identifier() -> None:
    claims = answer_claims(FACT + " [S1]")
    proposed = [
        ProposedAssessment.model_validate(
            {
                "claim_id": 1,
                "status": "supported",
                "evidence": [{"identifier": "S9", "quote": FACT}],
            }
        )
    ]
    [review] = verify_claims(claims, {}, proposed)
    assert review.status == "unverified"
    assert review.evidence == []

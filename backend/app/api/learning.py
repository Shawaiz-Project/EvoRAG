from datetime import datetime
from typing import Annotated, Any, Literal
from uuid import UUID

import sqlalchemy as sa
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field

from app.api.auth import Actor, Principal
from app.api.rag import Engine, Runtime
from app.db.execution import Message, RAGRun
from app.db.governance import LearningProposal
from app.learning import FeedbackInput, LearningEngine, LearningError
from app.retrieval.providers import ModelUnavailable

router = APIRouter(prefix="/api/v1", tags=["learning"])


def require_admin(actor: Actor) -> Principal:
    if "admin" not in actor.roles:
        raise HTTPException(403, "Administrator access is required")
    return actor


Admin = Annotated[Principal, Depends(require_admin)]


def get_learning(engine: Engine) -> LearningEngine:
    return LearningEngine(engine)


Learning = Annotated[LearningEngine, Depends(get_learning)]


class FeedbackView(BaseModel):
    feedback_id: UUID
    proposal_id: UUID | None


class ProposalView(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    author_kind: str
    status: str
    created_at: datetime
    feedback_id: UUID | None
    target_version_id: UUID | None
    published_version_id: UUID | None
    proposed_change: dict[str, Any]
    attributes: dict[str, Any]


class Decision(BaseModel):
    decision: Literal["approve", "reject"]
    reason: str = Field(min_length=3, max_length=2000, pattern=r"\S")


class Gap(BaseModel):
    question: str
    failures: int


@router.get("/learning/gaps")
async def gaps(actor: Admin, services: Runtime) -> list[Gap]:
    count = sa.func.count(RAGRun.id)
    query = (
        sa.select(sa.func.lower(Message.content).label("question"), count.label("failures"))
        .join(RAGRun, RAGRun.question_id == Message.id)
        .where(RAGRun.role == "primary", RAGRun.status.in_(("abstained", "failed")))
        .group_by(sa.func.lower(Message.content))
        .having(count >= 2)
        .order_by(count.desc())
        .limit(50)
    )
    async with services.sessions() as session:
        rows = (await session.execute(query)).mappings()
        return [Gap.model_validate(row) for row in rows]


@router.post("/rag/runs/{run_id}/feedback")
async def feedback(
    run_id: UUID,
    payload: FeedbackInput,
    actor: Actor,
    learning: Learning,
) -> FeedbackView:
    try:
        feedback_id, proposal_id = await learning.feedback(actor.id, run_id, payload)
    except LookupError as error:
        raise HTTPException(404, str(error)) from error
    except LearningError as error:
        raise HTTPException(409, str(error)) from error
    return FeedbackView(feedback_id=feedback_id, proposal_id=proposal_id)


@router.get("/learning/proposals")
async def proposals(
    actor: Admin,
    services: Runtime,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
) -> list[ProposalView]:
    async with services.sessions() as session:
        rows = await session.scalars(
            sa.select(LearningProposal).order_by(LearningProposal.created_at.desc()).limit(limit)
        )
        return [ProposalView.model_validate(row) for row in rows]


@router.post("/learning/proposals/{proposal_id}/verify")
async def verify(proposal_id: UUID, actor: Admin, learning: Learning) -> ProposalView:
    try:
        return ProposalView.model_validate(await learning.verify(actor.id, proposal_id))
    except LookupError as error:
        raise HTTPException(404, str(error)) from error
    except LearningError as error:
        raise HTTPException(409, str(error)) from error
    except ModelUnavailable as error:
        raise HTTPException(503, str(error)) from error


@router.post("/learning/proposals/{proposal_id}/decision")
async def decide(
    proposal_id: UUID,
    payload: Decision,
    actor: Admin,
    learning: Learning,
) -> ProposalView:
    try:
        return ProposalView.model_validate(
            await learning.decide(actor.id, proposal_id, payload.decision, payload.reason)
        )
    except LookupError as error:
        raise HTTPException(404, str(error)) from error
    except LearningError as error:
        raise HTTPException(409, str(error)) from error


@router.post("/learning/proposals/{proposal_id}/publish")
async def publish(proposal_id: UUID, actor: Admin, learning: Learning) -> ProposalView:
    try:
        return ProposalView.model_validate(await learning.publish(actor.id, proposal_id))
    except LookupError as error:
        raise HTTPException(404, str(error)) from error
    except LearningError as error:
        raise HTTPException(409, str(error)) from error
    except ModelUnavailable as error:
        raise HTTPException(503, str(error)) from error


@router.post("/learning/proposals/{proposal_id}/rollback")
async def rollback(proposal_id: UUID, actor: Admin, learning: Learning) -> ProposalView:
    try:
        return ProposalView.model_validate(await learning.rollback(actor.id, proposal_id))
    except LookupError as error:
        raise HTTPException(404, str(error)) from error
    except LearningError as error:
        raise HTTPException(409, str(error)) from error

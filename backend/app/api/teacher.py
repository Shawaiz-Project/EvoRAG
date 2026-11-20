from typing import Annotated
from uuid import UUID

import sqlalchemy as sa
from fastapi import APIRouter, Depends, HTTPException, Query

from app.api.auth import Actor
from app.api.rag import Engine, Runtime
from app.db.execution import Conversation, RAGRun, TeacherEvaluation
from app.rag.engine import Answer, UnknownConfiguration, UnknownConversation
from app.rag.teacher import RefinementDenied, Review, TeacherEngine

router = APIRouter(prefix="/api/v1/teacher", tags=["teacher"])


def get_teacher(engine: Engine) -> TeacherEngine:
    return TeacherEngine(engine)


Teacher = Annotated[TeacherEngine, Depends(get_teacher)]


@router.post("/reviews/{run_id}")
async def review(run_id: UUID, actor: Actor, teacher: Teacher) -> Review:
    try:
        return await teacher.review(actor.id, run_id)
    except (UnknownConversation, UnknownConfiguration) as error:
        raise HTTPException(404, str(error)) from error
    except RefinementDenied as error:
        raise HTTPException(409, str(error)) from error


@router.post("/reviews/{review_id}/refine")
async def refine(review_id: UUID, actor: Actor, teacher: Teacher) -> Answer:
    try:
        return await teacher.refine(actor.id, review_id)
    except UnknownConversation as error:
        raise HTTPException(404, str(error)) from error
    except RefinementDenied as error:
        raise HTTPException(409, str(error)) from error


def owned_reviews(actor_id: UUID) -> sa.Select:
    return (
        sa.select(TeacherEvaluation)
        .join(RAGRun, RAGRun.id == TeacherEvaluation.primary_run_id)
        .join(Conversation, Conversation.id == RAGRun.conversation_id)
        .where(Conversation.owner_id == actor_id)
    )


@router.get("/reviews")
async def reviews(
    actor: Actor, services: Runtime, limit: Annotated[int, Query(ge=1, le=100)] = 50
) -> list[Review]:
    async with services.sessions() as session:
        records = await session.scalars(
            owned_reviews(actor.id).order_by(TeacherEvaluation.created_at.desc()).limit(limit)
        )
        return [
            Review(
                id=row.id,
                primary_run_id=row.primary_run_id,
                teacher_run_id=row.teacher_run_id,
                verdict=row.verdict,
                report=row.report,
            )
            for row in records
        ]


@router.get("/analytics")
async def analytics(actor: Actor, services: Runtime) -> dict[str, int]:
    source = owned_reviews(actor.id).subquery()
    query = sa.select(source.c.verdict, sa.func.count()).group_by(source.c.verdict)
    async with services.sessions() as session:
        counts = dict((await session.execute(query)).tuples().all())
    return {verdict: counts.get(verdict, 0) for verdict in ("pass", "revise", "reject")}

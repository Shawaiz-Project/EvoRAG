import asyncio
import json
from collections.abc import AsyncIterator
from datetime import datetime
from typing import Annotated, Any, cast
from uuid import UUID

import sqlalchemy as sa
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field

from app.api.auth import Actor
from app.api.dependencies import get_services
from app.db.execution import Conversation, Message, RAGConfiguration, RAGRun, RetrievalResult
from app.infrastructure.services import Services
from app.rag.engine import Answer, Question, RAGEngine, UnknownConfiguration, UnknownConversation
from app.retrieval.search import Filters

router = APIRouter(prefix="/api/v1", tags=["RAG"])
Runtime = Annotated[Services, Depends(get_services)]


def get_rag(request: Request) -> RAGEngine:
    return cast(RAGEngine, request.app.state.rag)


Engine = Annotated[RAGEngine, Depends(get_rag)]


@router.post("/rag/query")
async def query(payload: Question, actor: Actor, engine: Engine) -> Answer:
    try:
        return await engine.ask(actor.id, payload)
    except (UnknownConfiguration, UnknownConversation) as error:
        raise HTTPException(404, str(error)) from error


def event(kind: str, value: Any) -> str:
    return f"event: {kind}\ndata: {json.dumps(value, ensure_ascii=False)}\n\n"


@router.post("/rag/query/stream")
async def stream_query(payload: Question, actor: Actor, engine: Engine) -> StreamingResponse:
    async def stream() -> AsyncIterator[str]:
        task = asyncio.create_task(engine.ask(actor.id, payload))
        try:
            yield event("status", {"status": "running"})
            while not task.done():
                done, _ = await asyncio.wait({task}, timeout=10)
                if not done:
                    yield ": keep-alive\n\n"
            result = task.result()
            if result.answer:
                for start in range(0, len(result.answer), 160):
                    yield event("delta", {"text": result.answer[start : start + 160]})
            yield event("done", result.model_dump(mode="json"))
        except (UnknownConfiguration, UnknownConversation) as error:
            yield event("error", {"message": str(error)})
        finally:
            if not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


class ConversationView(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    title: str | None
    created_at: datetime


class MessageView(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    role: str
    content: str
    created_at: datetime


class TraceItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    chunk_id: UUID
    stage: str
    rank: int
    iteration: int
    score: float | None
    attributes: dict[str, Any]


class RunView(BaseModel):
    id: UUID
    status: str
    conversation_id: UUID
    attributes: dict[str, Any]
    retrieval: list[TraceItem]


class ConfigurationView(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    key: str
    version: int
    strategy: str


class Comparison(BaseModel):
    question: str = Field(min_length=1, max_length=4000, pattern=r"\S")
    configurations: list[str] = Field(min_length=2, max_length=9)
    filters: Filters = Field(default_factory=Filters)
    budget_steps: int = Field(default=3, ge=1, le=4)


@router.post("/rag/compare")
async def compare(payload: Comparison, actor: Actor, engine: Engine) -> list[Answer]:
    results = []
    for key in dict.fromkeys(payload.configurations):
        try:
            results.append(
                await engine.ask(
                    actor.id,
                    Question(
                        question=payload.question,
                        configuration=key,
                        filters=payload.filters,
                        budget_steps=payload.budget_steps,
                    ),
                )
            )
        except UnknownConfiguration as error:
            raise HTTPException(404, str(error)) from error
    return results


@router.get("/rag/configurations")
async def configurations(actor: Actor, services: Runtime) -> list[ConfigurationView]:
    async with services.sessions() as session:
        records = await session.scalars(
            sa.select(RAGConfiguration)
            .distinct(RAGConfiguration.key)
            .where(RAGConfiguration.key != "teacher")
            .order_by(RAGConfiguration.key, RAGConfiguration.version.desc())
        )
        configurations = [ConfigurationView.model_validate(record) for record in records]
        if any(config.key == "advanced" for config in configurations):
            configurations.append(ConfigurationView(key="auto", version=1, strategy="adaptive"))
        return configurations


@router.get("/conversations")
async def conversations(actor: Actor, services: Runtime) -> list[ConversationView]:
    async with services.sessions() as session:
        records = await session.scalars(
            sa.select(Conversation)
            .where(Conversation.owner_id == actor.id)
            .order_by(Conversation.created_at.desc())
            .limit(100)
        )
        return [ConversationView.model_validate(record) for record in records]


@router.get("/conversations/{conversation_id}/messages")
async def messages(conversation_id: UUID, actor: Actor, services: Runtime) -> list[MessageView]:
    async with services.sessions() as session:
        belongs = await session.scalar(
            sa.select(Conversation.id).where(
                Conversation.id == conversation_id,
                Conversation.owner_id == actor.id,
            )
        )
        if belongs is None:
            raise HTTPException(404, "Conversation not found")
        records = list(
            await session.scalars(
                sa.select(Message)
                .where(Message.conversation_id == conversation_id)
                .order_by(Message.created_at.desc(), Message.id.desc())
                .limit(200)
            )
        )
        return [MessageView.model_validate(record) for record in reversed(records)]


@router.get("/rag/runs/{run_id}")
async def run_trace(run_id: UUID, actor: Actor, services: Runtime) -> RunView:
    async with services.sessions() as session:
        run = await session.scalar(
            sa.select(RAGRun)
            .join(Conversation, Conversation.id == RAGRun.conversation_id)
            .where(RAGRun.id == run_id, Conversation.owner_id == actor.id)
        )
        if run is None:
            raise HTTPException(404, "RAG run not found")
        hits = await session.scalars(
            sa.select(RetrievalResult)
            .where(RetrievalResult.run_id == run.id)
            .order_by(RetrievalResult.iteration, RetrievalResult.stage, RetrievalResult.rank)
        )
        return RunView(
            id=run.id,
            status=run.status,
            conversation_id=run.conversation_id,
            attributes=run.attributes,
            retrieval=[TraceItem.model_validate(hit) for hit in hits],
        )

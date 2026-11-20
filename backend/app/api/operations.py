from datetime import datetime
from typing import Any
from uuid import UUID

import sqlalchemy as sa
from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict

from app.api.auth import Actor, Configuration
from app.api.rag import Runtime
from app.db.execution import Conversation, RAGConfiguration, RAGRun
from app.db.identity import ModelArtifact

router = APIRouter(prefix="/api/v1", tags=["operations"])


class ModelView(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    provider: str
    kind: str
    name: str
    version: str
    dimensions: int | None


@router.get("/models")
async def models(actor: Actor, services: Runtime) -> list[ModelView]:
    async with services.sessions() as session:
        records = await session.scalars(
            sa.select(ModelArtifact).order_by(ModelArtifact.kind, ModelArtifact.name).limit(200)
        )
        return [ModelView.model_validate(record) for record in records]


class WorkflowView(BaseModel):
    id: UUID
    configuration: str
    strategy: str
    status: str
    role: str
    created_at: datetime
    steps: list[str]


@router.get("/workflows")
async def workflows(actor: Actor, services: Runtime) -> list[WorkflowView]:
    query = (
        sa.select(RAGRun, RAGConfiguration.key, RAGConfiguration.strategy)
        .join(Conversation, Conversation.id == RAGRun.conversation_id)
        .join(RAGConfiguration, RAGConfiguration.id == RAGRun.configuration_id)
        .where(Conversation.owner_id == actor.id)
        .order_by(RAGRun.created_at.desc())
        .limit(100)
    )
    async with services.sessions() as session:
        return [
            WorkflowView(
                id=run.id,
                configuration=key,
                strategy=strategy,
                status=run.status,
                role=run.role,
                created_at=run.created_at,
                steps=run.attributes.get("workflow", []),
            )
            for run, key, strategy in (await session.execute(query)).all()
        ]


@router.get("/settings")
async def settings(actor: Actor, config: Configuration) -> dict[str, Any]:
    return {
        "max_upload_bytes": config.max_upload_bytes,
        "max_remote_bytes": config.max_remote_bytes,
        "teacher_max_refinements": config.teacher_max_refinements,
        "mlflow_enabled": bool(config.mlflow_tracking_uri),
        "ragas_enabled": bool(config.ragas_ollama_model),
        "secure_session_cookie": config.session_cookie_secure,
    }

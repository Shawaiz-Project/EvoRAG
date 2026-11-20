import asyncio
from datetime import datetime
from typing import Annotated, Any, Literal
from uuid import UUID

import sqlalchemy as sa
from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import defer

from app.api.auth import current_user
from app.api.dependencies import get_services, get_settings
from app.core.config import Settings
from app.db.ingestion import IngestionJob
from app.db.knowledge import Chunk, Document, DocumentVersion, Source
from app.infrastructure.services import Services
from app.ingestion.chunking import ChunkStrategy
from app.ingestion.extraction import InvalidDocument, validate_upload
from app.ingestion.jobs import JobRepository
from app.ingestion.remote import RemoteKind, github_location, safe_url

router = APIRouter(prefix="/api/v1", tags=["knowledge"], dependencies=[Depends(current_user)])
Runtime = Annotated[Services, Depends(get_services)]
Configuration = Annotated[Settings, Depends(get_settings)]


class JobView(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    kind: str
    locator: str
    strategy: str
    status: Literal["queued", "processing", "completed", "failed"]
    attempts: int
    created_at: datetime
    finished_at: datetime | None
    error: str | None
    result: dict[str, Any] | None


class RemoteInput(BaseModel):
    kind: RemoteKind
    url: str = Field(min_length=1, max_length=2000)
    strategy: ChunkStrategy = "recursive"


@router.post("/ingestion/files", response_model=JobView, status_code=status.HTTP_202_ACCEPTED)
async def upload(
    request: Request,
    services: Runtime,
    settings: Configuration,
    filename: Annotated[str, Query(min_length=1, max_length=1024)],
    strategy: ChunkStrategy = "recursive",
) -> IngestionJob:
    media_type = request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
    if media_type != "application/octet-stream":
        raise HTTPException(415, "Send the file as application/octet-stream")
    data = bytearray()
    async for part in request.stream():
        if len(data) + len(part) > settings.max_upload_bytes:
            raise HTTPException(status.HTTP_413_CONTENT_TOO_LARGE, "File exceeds the size limit")
        data.extend(part)
    body = bytes(data)
    try:
        name, _ = await asyncio.to_thread(
            validate_upload, filename, body, settings.max_upload_bytes
        )
    except InvalidDocument as error:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(error)) from error
    async with services.sessions.begin() as session:
        return await JobRepository(session).add(
            IngestionJob(kind="file", locator=name, strategy=strategy, payload=body)
        )


@router.post("/ingestion/remote", response_model=JobView, status_code=status.HTTP_202_ACCEPTED)
async def add_remote(payload: RemoteInput, services: Runtime) -> IngestionJob:
    try:
        if payload.kind == "github":
            owner, repo = github_location(payload.url)
            locator = f"https://github.com/{owner.lower()}/{repo.lower()}"
        else:
            locator = str(safe_url(payload.url))
    except InvalidDocument as error:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(error)) from error
    async with services.sessions.begin() as session:
        return await JobRepository(session).add(
            IngestionJob(kind=payload.kind, locator=locator, strategy=payload.strategy)
        )


@router.get("/ingestion/jobs", response_model=list[JobView])
async def list_jobs(
    services: Runtime, limit: Annotated[int, Query(ge=1, le=100)] = 50
) -> list[IngestionJob]:
    query = (
        sa.select(IngestionJob)
        .options(defer(IngestionJob.payload))
        .order_by(IngestionJob.created_at.desc())
        .limit(limit)
    )
    async with services.sessions() as session:
        return list(await session.scalars(query))


@router.get("/ingestion/jobs/{job_id}", response_model=JobView)
async def get_job(job_id: UUID, services: Runtime) -> IngestionJob:
    async with services.sessions() as session:
        job = await session.get(IngestionJob, job_id, options=[defer(IngestionJob.payload)])
        if job is None:
            raise HTTPException(404, "Ingestion job not found")
        return job


@router.post("/ingestion/jobs/{job_id}/retry", response_model=JobView, status_code=202)
async def retry_job(job_id: UUID, services: Runtime) -> IngestionJob:
    async with services.sessions.begin() as session:
        try:
            job = await JobRepository(session).retry(job_id)
        except ValueError as error:
            raise HTTPException(409, str(error)) from error
        if job is None:
            raise HTTPException(404, "Ingestion job not found")
        return job


class DocumentView(BaseModel):
    id: UUID
    title: str
    locator: str
    source_id: UUID
    source_kind: str
    source_locator: str


class VersionView(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    version: int
    sha256: str
    size_bytes: int
    mime_type: str
    is_current: bool
    created_at: datetime
    attributes: dict[str, Any]


class ChunkView(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    parent_id: UUID | None
    ordinal: int
    content: str
    page: int | None
    section: str | None
    attributes: dict[str, Any]


class DocumentDetail(BaseModel):
    document: DocumentView
    versions: list[VersionView]


class VersionDetail(BaseModel):
    version: VersionView
    extracted_text: str
    chunks: list[ChunkView]


def document_query() -> sa.Select:
    return sa.select(
        Document.id,
        Document.title,
        Document.locator,
        Document.source_id,
        Source.kind.label("source_kind"),
        Source.locator.label("source_locator"),
    ).join(Source, Source.id == Document.source_id)


@router.get("/documents")
async def list_documents(
    services: Runtime,
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
) -> list[DocumentView]:
    query = (
        document_query()
        .order_by(Document.created_at.desc(), Document.id)
        .offset(offset)
        .limit(limit)
    )
    async with services.sessions() as session:
        return [
            DocumentView.model_validate(row) for row in (await session.execute(query)).mappings()
        ]


@router.get("/documents/{document_id}")
async def get_document(document_id: UUID, services: Runtime) -> DocumentDetail:
    async with services.sessions() as session:
        row = (
            (await session.execute(document_query().where(Document.id == document_id)))
            .mappings()
            .one_or_none()
        )
        if row is None:
            raise HTTPException(404, "Document not found")
        versions = await session.scalars(
            sa.select(DocumentVersion)
            .options(defer(DocumentVersion.extracted_text))
            .where(DocumentVersion.document_id == document_id)
            .order_by(DocumentVersion.version.desc())
        )
        return DocumentDetail(
            document=DocumentView.model_validate(row),
            versions=[VersionView.model_validate(version) for version in versions],
        )


@router.get("/documents/{document_id}/versions/{version_id}")
async def get_version(
    document_id: UUID,
    version_id: UUID,
    services: Runtime,
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> VersionDetail:
    async with services.sessions() as session:
        version = await session.scalar(
            sa.select(DocumentVersion).where(
                DocumentVersion.id == version_id,
                DocumentVersion.document_id == document_id,
            )
        )
        if version is None:
            raise HTTPException(404, "Document version not found")
        chunks = await session.scalars(
            sa.select(Chunk)
            .where(Chunk.document_version_id == version_id)
            .order_by(Chunk.ordinal)
            .offset(offset)
            .limit(limit)
        )
        return VersionDetail(
            version=VersionView.model_validate(version),
            extracted_text=version.extracted_text,
            chunks=[ChunkView.model_validate(chunk) for chunk in chunks],
        )

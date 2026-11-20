from datetime import datetime
from typing import Annotated, Any, Literal
from uuid import UUID

import sqlalchemy as sa
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.exc import IntegrityError

from app.api.auth import Actor
from app.api.learning import Admin
from app.api.rag import Engine, Runtime
from app.db.benchmarks import (
    BenchmarkCase,
    BenchmarkDataset,
    BenchmarkEvidence,
    BenchmarkResult,
    Experiment,
)
from app.db.repositories import Repository
from app.evaluation.runner import EvaluationRunner, ExperimentInput

router = APIRouter(prefix="/api/v1", tags=["evaluation"])


class EvidenceLabel(BaseModel):
    chunk_id: UUID
    relevance: int = Field(default=1, ge=1, le=5)


class CaseInput(BaseModel):
    key: str = Field(min_length=1, max_length=60, pattern=r"^[a-zA-Z0-9_-]+$")
    question: str = Field(min_length=1, max_length=4000, pattern=r"\S")
    golden_answer: str | None = Field(default=None, max_length=4000)
    expected_verdict: Literal["pass", "revise", "reject"] | None = None
    evidence: list[EvidenceLabel] = Field(default_factory=list, max_length=60)


class DatasetInput(BaseModel):
    key: str = Field(min_length=1, max_length=60, pattern=r"^[a-zA-Z0-9_-]+$")
    version: int = Field(ge=1)
    description: str = Field(min_length=1, max_length=3000)
    cases: list[CaseInput] = Field(min_length=1, max_length=50)


class DatasetView(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    key: str
    version: int
    description: str
    created_at: datetime


@router.get("/benchmarks/datasets")
async def datasets(actor: Actor, services: Runtime) -> list[DatasetView]:
    async with services.sessions() as session:
        rows = await session.scalars(
            sa.select(BenchmarkDataset)
            .order_by(BenchmarkDataset.key, BenchmarkDataset.version.desc())
            .limit(100)
        )
        return [DatasetView.model_validate(row) for row in rows]


@router.post("/benchmarks/datasets", status_code=201)
async def create_dataset(payload: DatasetInput, actor: Admin, services: Runtime) -> DatasetView:
    try:
        async with services.sessions.begin() as session:
            dataset = await Repository(session, BenchmarkDataset).add(
                BenchmarkDataset(
                    key=payload.key,
                    version=payload.version,
                    description=payload.description,
                )
            )
            cases = Repository(session, BenchmarkCase)
            for item in payload.cases:
                case = await cases.add(
                    BenchmarkCase(
                        dataset_id=dataset.id,
                        key=item.key,
                        question=item.question,
                        golden_answer=item.golden_answer,
                        expected_verdict=item.expected_verdict,
                    )
                )
                session.add_all(
                    BenchmarkEvidence(
                        case_id=case.id,
                        chunk_id=evidence.chunk_id,
                        relevance=evidence.relevance,
                    )
                    for evidence in item.evidence
                )
    except IntegrityError as error:
        raise HTTPException(
            409, "Duplicate dataset, case or evidence, or invalid chunk ID"
        ) from error
    return DatasetView.model_validate(dataset)


class DatasetDetail(BaseModel):
    dataset: DatasetView
    cases: list[CaseInput]


@router.get("/benchmarks/datasets/{dataset_id}")
async def dataset_detail(dataset_id: UUID, actor: Actor, services: Runtime) -> DatasetDetail:
    async with services.sessions() as session:
        dataset = await session.get(BenchmarkDataset, dataset_id)
        if dataset is None:
            raise HTTPException(404, "Benchmark dataset not found")
        cases = list(
            await session.scalars(
                sa.select(BenchmarkCase)
                .where(BenchmarkCase.dataset_id == dataset_id)
                .order_by(BenchmarkCase.key)
            )
        )
        labels = list(
            await session.scalars(
                sa.select(BenchmarkEvidence).where(
                    BenchmarkEvidence.case_id.in_([item.id for item in cases])
                )
            )
        )
        return DatasetDetail(
            dataset=DatasetView.model_validate(dataset),
            cases=[
                CaseInput(
                    key=case.key,
                    question=case.question,
                    golden_answer=case.golden_answer,
                    expected_verdict=case.expected_verdict,
                    evidence=[
                        EvidenceLabel(chunk_id=label.chunk_id, relevance=label.relevance)
                        for label in labels
                        if label.case_id == case.id
                    ],
                )
                for case in cases
            ],
        )


class ExperimentView(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    dataset_id: UUID
    name: str
    status: str
    created_at: datetime
    settings: dict[str, Any]
    attributes: dict[str, Any]


class ResultView(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    case_id: UUID | None
    run_id: UUID | None
    configuration_id: UUID
    metrics: dict[str, Any]
    attributes: dict[str, Any]


class ExperimentDetail(BaseModel):
    experiment: ExperimentView
    results: list[ResultView]


@router.post("/experiments", status_code=201)
async def run_experiment(payload: ExperimentInput, actor: Actor, engine: Engine) -> ExperimentView:
    try:
        return ExperimentView.model_validate(await EvaluationRunner(engine).run(actor.id, payload))
    except LookupError as error:
        raise HTTPException(404, str(error)) from error
    except ValueError as error:
        raise HTTPException(422, str(error)) from error


@router.get("/experiments")
async def experiments(
    actor: Actor, services: Runtime, limit: Annotated[int, Query(ge=1, le=100)] = 50
) -> list[ExperimentView]:
    async with services.sessions() as session:
        rows = await session.scalars(
            sa.select(Experiment)
            .where(Experiment.owner_id == actor.id)
            .order_by(Experiment.created_at.desc())
            .limit(limit)
        )
        return [ExperimentView.model_validate(row) for row in rows]


@router.get("/experiments/{experiment_id}")
async def experiment_detail(
    experiment_id: UUID, actor: Actor, services: Runtime
) -> ExperimentDetail:
    async with services.sessions() as session:
        run = await session.scalar(
            sa.select(Experiment).where(
                Experiment.id == experiment_id,
                Experiment.owner_id == actor.id,
            )
        )
        if run is None:
            raise HTTPException(404, "Experiment not found")
        results = await session.scalars(
            sa.select(BenchmarkResult)
            .where(BenchmarkResult.experiment_key == str(run.id))
            .order_by(BenchmarkResult.created_at, BenchmarkResult.id)
        )
        return ExperimentDetail(
            experiment=ExperimentView.model_validate(run),
            results=[ResultView.model_validate(result) for result in results],
        )

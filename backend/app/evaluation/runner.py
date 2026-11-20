import asyncio
from datetime import UTC, datetime
from time import perf_counter
from typing import Any, Literal
from uuid import UUID

import sqlalchemy as sa
from pydantic import BaseModel, Field

from app.db.benchmarks import (
    BenchmarkCase,
    BenchmarkDataset,
    BenchmarkEvidence,
    BenchmarkResult,
    Experiment,
)
from app.db.execution import RAGConfiguration
from app.db.identity import ModelArtifact
from app.db.knowledge import Chunk, DocumentVersion, EmbeddingSpace
from app.db.repositories import Repository
from app.evaluation.integrations import ragas_faithfulness, track
from app.evaluation.metrics import answer_metrics, retrieval_metrics, teacher_metrics
from app.rag.engine import Answer, Question, RAGEngine
from app.rag.evidence import ABSTENTION, PROMPT_REVISION, SYSTEM_PROMPT
from app.rag.teacher import TEACHER_PROMPT, TEACHER_PROMPT_VERSION, TeacherEngine
from app.retrieval.search import Filters

Variant = Literal["naive", "advanced", "corrective", "teacher-guided", "without-parent-context"]


class ExperimentInput(BaseModel):
    name: str = Field(min_length=1, max_length=120, pattern=r"\S")
    dataset_id: UUID
    variants: list[Variant] = Field(
        default_factory=lambda: ["naive", "advanced", "corrective", "teacher-guided"],
        min_length=1,
        max_length=5,
    )
    k: int = Field(default=5, ge=1, le=20)


class EvaluationRunner:
    def __init__(self, rag: RAGEngine) -> None:
        self.rag = rag
        self.teacher = TeacherEngine(rag)
        self.sessions = rag.sessions

    async def run(self, actor: UUID, request: ExperimentInput) -> Experiment:
        variants = list(dict.fromkeys(request.variants))
        async with self.sessions.begin() as session:
            dataset = await session.get(BenchmarkDataset, request.dataset_id)
            if dataset is None:
                raise LookupError("Benchmark dataset not found")
            cases = list(
                await session.scalars(
                    sa.select(BenchmarkCase)
                    .where(BenchmarkCase.dataset_id == dataset.id)
                    .order_by(BenchmarkCase.key)
                    .limit(51)
                )
            )
            if not cases or len(cases) > 50:
                raise ValueError("An experiment needs 1 to 50 benchmark cases")
            case_ids = [case.id for case in cases]
            evidence = (
                await session.execute(
                    sa.select(
                        BenchmarkEvidence.case_id,
                        BenchmarkEvidence.chunk_id,
                        BenchmarkEvidence.relevance,
                        DocumentVersion.document_id,
                        DocumentVersion.attributes,
                    )
                    .join(Chunk, Chunk.id == BenchmarkEvidence.chunk_id)
                    .join(DocumentVersion, DocumentVersion.id == Chunk.document_version_id)
                    .where(BenchmarkEvidence.case_id.in_(case_ids))
                )
            ).all()
            presets: dict[str, RAGConfiguration] = {}
            fingerprints: dict[str, Any] = {}
            for variant in variants:
                key = (
                    "advanced"
                    if variant in {"teacher-guided", "without-parent-context"}
                    else variant
                )
                config = await self.rag.configuration(
                    Question(question="benchmark preset", configuration=key), session
                )
                if variant == "without-parent-context":
                    await session.execute(
                        sa.text("SELECT pg_advisory_xact_lock(hashtext('evaluation-ablation'))")
                    )
                    latest = await session.scalar(
                        sa.select(sa.func.max(RAGConfiguration.version)).where(
                            RAGConfiguration.key == "without-parent-context"
                        )
                    )
                    config = await Repository(session, RAGConfiguration).add(
                        RAGConfiguration(
                            key="without-parent-context",
                            version=(latest or 0) + 1,
                            strategy=config.strategy,
                            settings=config.settings | {"parent_child": False},
                            model_id=config.model_id,
                            embedding_space_id=config.embedding_space_id,
                        )
                    )
                presets[variant] = config
            settings_by_variant = presets.copy()
            if "teacher-guided" in variants:
                settings_by_variant["teacher"] = await self.rag.configuration(
                    Question(question="benchmark teacher", configuration="teacher"), session
                )
            for variant, config in settings_by_variant.items():
                model = (
                    await session.get(ModelArtifact, config.model_id) if config.model_id else None
                )
                space = (
                    await session.get(EmbeddingSpace, config.embedding_space_id)
                    if config.embedding_space_id
                    else None
                )
                embed = await session.get(ModelArtifact, space.model_id) if space else None
                fingerprints[variant] = {
                    "configuration_id": str(config.id),
                    "configuration_version": config.version,
                    "settings": config.settings,
                    "model": {
                        "provider": model.provider,
                        "name": model.name,
                        "version": model.version,
                        "pricing": model.attributes.get("pricing"),
                    }
                    if model
                    else None,
                    "embedding": {
                        "space": str(space.id),
                        "version": space.version,
                        "recipe": space.recipe,
                        "dimensions": space.dimensions,
                        "model": embed.name,
                        "model_version": embed.version,
                    }
                    if space and embed
                    else None,
                }
            snapshot = {
                "dataset_key": dataset.key,
                "dataset_version": dataset.version,
                "dataset_id": str(dataset.id),
                "k": request.k,
                "configurations": fingerprints,
                "answer_prompt": SYSTEM_PROMPT,
                "prompt_revision": PROMPT_REVISION,
                "teacher_prompt": TEACHER_PROMPT,
                "teacher_prompt_version": TEACHER_PROMPT_VERSION,
                "chunk_strategies": sorted(
                    {str(row[4].get("chunk_strategy") or "unspecified") for row in evidence}
                ),
                "ragas_model": self.rag.settings.ragas_ollama_model,
            }
            experiment = await Repository(session, Experiment).add(
                Experiment(
                    owner_id=actor,
                    dataset_id=dataset.id,
                    name=request.name,
                    status="running",
                    settings=snapshot,
                )
            )
        records = []
        labels: list[tuple[str, str]] = []
        try:
            for case in cases:
                gold = {chunk: grade for key, chunk, grade, _, _ in evidence if key == case.id}
                documents = {document for key, _, _, document, _ in evidence if key == case.id}
                for variant in variants:
                    started = perf_counter()
                    config = presets[variant]
                    answer = await self.rag.ask(
                        actor,
                        Question(
                            question=case.question,
                            configuration=config.key,
                            configuration_version=config.version,
                            filters=Filters(document_ids=sorted(documents)),
                        ),
                    )
                    review = None
                    if variant == "teacher-guided":
                        review = await self.teacher.review(actor, answer.run_id)
                        if case.expected_verdict:
                            labels.append((case.expected_verdict, review.verdict))
                        if review.verdict == "revise":
                            answer = await self.teacher.refine(actor, review.id)
                        elif review.verdict == "reject":
                            answer = answer.model_copy(
                                update={
                                    "status": "abstained",
                                    "answer": ABSTENTION,
                                    "citations": [],
                                }
                            )
                    metrics = (
                        retrieval_metrics([hit.chunk_id for hit in answer.sources], gold, request.k)
                        | answer_metrics(answer, case.question, case.golden_answer)
                        | self.usage(answer, fingerprints[variant]["model"])
                    )
                    metrics["faithfulness"] = await ragas_faithfulness(
                        self.rag.settings, case.question, answer
                    )
                    if self.rag.settings.ragas_ollama_model:
                        metrics["faithfulness_method"] = (
                            "ragas" if metrics["faithfulness"] is not None else None
                        )
                    elif answer.execution.get("answer_style") == "excerpt" and answer.citations:
                        metrics["faithfulness"] = float(answer.citations_verified)
                        metrics["faithfulness_method"] = "exact_excerpt"
                    metrics["wall_latency_ms"] = round((perf_counter() - started) * 1000, 1)
                    if review:
                        metrics["teacher_verdict"] = review.verdict
                        metrics["teacher_latency_ms"] = review.report.get("latency_ms")
                        metrics["teacher_input_tokens"] = review.report.get("input_tokens")
                        metrics["teacher_output_tokens"] = review.report.get("output_tokens")
                    async with self.sessions.begin() as session:
                        await Repository(session, BenchmarkResult).add(
                            BenchmarkResult(
                                dataset_id=dataset.id,
                                case_id=case.id,
                                configuration_id=config.id,
                                run_id=answer.run_id,
                                metrics=metrics,
                                experiment_key=str(experiment.id),
                                attributes={
                                    "variant": variant,
                                    "status": answer.status,
                                    "answer": answer.answer,
                                },
                            )
                        )
                    records.append({"case": case.key, "variant": variant, "metrics": metrics})
            tracking_id = await asyncio.to_thread(
                track,
                self.rag.settings,
                experiment.name,
                snapshot,
                records,
            )
            return await self.finish(
                experiment.id,
                "completed",
                {
                    "cases": len(cases),
                    "results": len(records),
                    "teacher": teacher_metrics(labels),
                    "mlflow_run_id": tracking_id,
                },
            )
        except Exception as error:
            await self.finish(
                experiment.id,
                "failed",
                {
                    "completed_results": len(records),
                    "error_type": type(error).__name__,
                },
            )
            raise

    async def finish(
        self, experiment_id: UUID, status: str, attributes: dict[str, Any]
    ) -> Experiment:
        async with self.sessions.begin() as session:
            experiment = await session.get(Experiment, experiment_id, with_for_update=True)
            assert experiment is not None
            experiment.status = status
            experiment.attributes = attributes | {"finished_at": datetime.now(UTC).isoformat()}
        return experiment

    @staticmethod
    def usage(answer: Answer, model: dict[str, Any] | None) -> dict[str, float | int | None]:
        input_tokens = answer.execution.get("input_tokens")
        output_tokens = answer.execution.get("output_tokens")
        cost = None
        if model and model["provider"] == "ollama":
            cost = 0.0
        elif (
            model
            and isinstance((pricing := model.get("pricing")), dict)
            and input_tokens is not None
            and output_tokens is not None
        ):
            rates = (pricing.get("input_per_million_usd"), pricing.get("output_per_million_usd"))
            if all(isinstance(rate, (float, int)) and rate >= 0 for rate in rates):
                cost = (input_tokens * rates[0] + output_tokens * rates[1]) / 1_000_000
        return {
            "latency_ms": answer.execution.get("latency_ms"),
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "provider_cost_usd": cost,
        }

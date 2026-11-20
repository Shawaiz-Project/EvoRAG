import asyncio
import json
import logging
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from time import perf_counter
from typing import Any, Literal
from uuid import UUID, uuid5

import sqlalchemy as sa
from pydantic import BaseModel, Field, ValidationError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings
from app.db.execution import Conversation, Message, RAGConfiguration, RAGRun
from app.db.identity import ModelArtifact
from app.db.knowledge import EmbeddingSpace
from app.db.repositories import Repository
from app.rag.evidence import (
    ABSTENTION,
    EXCERPT_PROMPT,
    PROMPT_REVISION,
    SYSTEM_PROMPT,
    Citation,
    Draft,
    Excerpt,
    Passage,
    context,
    excerpt_candidates,
    validate_draft,
    validate_excerpt,
)
from app.rag.providers import Generator, StructuredOutputError, generator_for
from app.rag.strategies import STRATEGIES, Context, coverage, execute, execution_configuration
from app.retrieval.providers import (
    CrossEncoderReranker,
    EmbeddingIdentity,
    EmbeddingProvider,
    ModelUnavailable,
    Reranker,
    embedding_identity,
    embedding_provider,
)
from app.retrieval.search import (
    Filters,
    Hit,
    PostgresRetriever,
    SearchOptions,
    SearchResult,
    rewrite_query,
)
from app.retrieval.traces import record_search

logger = logging.getLogger(__name__)


class Question(BaseModel):
    question: str = Field(min_length=1, max_length=4000, pattern=r"\S")
    conversation_id: UUID | None = None
    configuration: str = Field(default="advanced", min_length=1, max_length=100)
    configuration_version: int | None = Field(default=None, ge=1)
    budget_steps: int = Field(default=3, ge=1, le=4)
    filters: Filters = Field(default_factory=Filters)


type EvidenceCategory = Literal["lookup", "technical", "multi_document", "research"]


class AdaptiveDepths(BaseModel):
    lookup: int = Field(default=4, ge=1, le=100)
    technical: int = Field(default=8, ge=1, le=100)
    multi_document: int = Field(default=14, ge=1, le=100)
    research: int = Field(default=18, ge=1, le=100)


def adaptive_depth(
    request: Question, config: RAGConfiguration
) -> tuple[RAGConfiguration, dict[str, Any]]:
    category: EvidenceCategory = "lookup"
    intent = classify(request.question)
    if len(request.filters.document_ids) > 1 or intent == "comparison":
        category = "multi_document"
    elif len(request.question.split()) > 22 or request.question.count("?") > 1:
        category = "research"
    elif intent == "explanation":
        category = "technical"
    options = SearchOptions.model_validate(config.settings)
    depths = AdaptiveDepths.model_validate(config.settings.get("adaptive_depths", {}))
    top_k = min(getattr(depths, category), options.candidate_k)
    selected = execution_configuration(config, {"top_k": top_k})
    return selected, {"category": category, "top_k": top_k, "configuration": config.key}


class Answer(BaseModel):
    run_id: UUID
    conversation_id: UUID
    status: Literal["completed", "abstained", "failed"]
    answer: str
    strategy: str
    configuration: str
    configuration_version: int
    citations: list[Citation]
    citations_verified: bool
    sources: list[Hit]
    queries: list[str]
    execution: dict[str, Any]
    error: str | None = None


class UnknownConversation(LookupError):
    pass


class UnknownConfiguration(LookupError):
    pass


class CoverageReranker:
    async def score(self, question: str, passages: Sequence[str]) -> list[float]:
        terms = {word.casefold() for word in rewrite_query(question).split()}
        return [
            len(terms.intersection(rewrite_query(text).casefold().split())) / max(1, len(terms))
            for text in passages
        ]


async def generate_grounded(
    provider: Generator,
    question: str,
    evidence: dict[str, Passage],
    history: list[dict[str, str]],
    settings: dict[str, Any],
) -> tuple[str, list[Citation], bool, dict[str, Any]]:
    prompt = json.dumps(
        {
            "question": question,
            "conversation_history": history,
            "evidence": [
                {"source": label, "text": passage.text} for label, passage in evidence.items()
            ],
        },
        ensure_ascii=False,
    )
    counts: dict[str, int] = {}
    verified = False
    response = ABSTENTION
    citations: list[Citation] = []
    if settings.get("answer_style") != "excerpt":
        try:
            extra = settings.get("prompt", "")
            draft = await provider.generate(
                SYSTEM_PROMPT + ("\n" + extra if isinstance(extra, str) and extra else ""),
                prompt,
                Draft,
                max(256, min(4096, int(settings.get("answer_tokens", 1500)))),
            )
            response, citations, verified = validate_draft(draft.output, evidence)
            counts = {
                "input_tokens": draft.input_tokens or 0,
                "output_tokens": draft.output_tokens or 0,
            }
            if citations or verified:
                return (
                    response,
                    citations,
                    verified,
                    {
                        **counts,
                        "model_version": draft.model_version,
                        "answer_style": "claims",
                    },
                )
        except StructuredOutputError:
            pass
    excerpts = excerpt_candidates(evidence, question)
    selection_prompt = json.dumps(
        {
            "question": question,
            "evidence": [
                {"source": label, "text": passage.text} for label, passage in excerpts.items()
            ],
        },
        ensure_ascii=False,
    )
    selection = await provider.generate(EXCERPT_PROMPT, selection_prompt, Excerpt, 96)
    response, citations, verified = validate_excerpt(selection.output, excerpts)
    return (
        response,
        citations,
        verified,
        {
            "input_tokens": counts.get("input_tokens", 0) + (selection.input_tokens or 0),
            "output_tokens": counts.get("output_tokens", 0) + (selection.output_tokens or 0),
            "model_version": selection.model_version,
            "answer_style": "excerpt",
        },
    )


class RAGEngine:
    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        settings: Settings,
        *,
        generator: Callable[[ModelArtifact, Settings], Generator] = generator_for,
        embedder: Callable[[EmbeddingIdentity, Settings], EmbeddingProvider] = embedding_provider,
    ) -> None:
        self.sessions = sessions
        self.settings = settings
        self.generator = generator
        self.embedder = embedder
        self._embedders: dict[EmbeddingIdentity, EmbeddingProvider] = {}
        self._rerankers: dict[tuple[str, str], Reranker] = {}

    async def configuration(self, request: Question, session: AsyncSession) -> RAGConfiguration:
        requested = request.configuration
        if requested == "auto":
            requested = choose_strategy(request.question, request.budget_steps)
        query = sa.select(RAGConfiguration).where(RAGConfiguration.key == requested)
        if request.configuration_version is not None:
            query = query.where(RAGConfiguration.version == request.configuration_version)
        config = await session.scalar(query.order_by(RAGConfiguration.version.desc()).limit(1))
        if config is None and request.configuration == "auto":
            config = await session.scalar(
                sa.select(RAGConfiguration)
                .where(RAGConfiguration.key == "advanced")
                .order_by(RAGConfiguration.version.desc())
                .limit(1)
            )
        if config is None:
            raise UnknownConfiguration("RAG configuration not found")
        return config

    async def start(
        self, actor_id: UUID, request: Question
    ) -> tuple[RAGRun, RAGConfiguration, list[dict[str, str]]]:
        async with self.sessions.begin() as session:
            config = await self.configuration(request, session)
            if request.conversation_id is None:
                conversation = await Repository(session, Conversation).add(
                    Conversation(owner_id=actor_id, title=request.question.strip()[:120])
                )
            else:
                conversation = await session.scalar(
                    sa.select(Conversation).where(
                        Conversation.id == request.conversation_id,
                        Conversation.owner_id == actor_id,
                    )
                )
                if conversation is None:
                    raise UnknownConversation("Conversation not found")
            messages = list(
                await session.scalars(
                    sa.select(Message)
                    .where(Message.conversation_id == conversation.id)
                    .order_by(Message.created_at.desc(), Message.id.desc())
                    .limit(10)
                )
            )
            history = [
                {"role": message.role, "content": message.content[:700]}
                for message in reversed(messages)
            ]
            question = await Repository(session, Message).add(
                Message(
                    conversation_id=conversation.id, role="user", content=request.question.strip()
                )
            )
            run = await Repository(session, RAGRun).add(
                RAGRun(
                    configuration_id=config.id,
                    conversation_id=conversation.id,
                    question_id=question.id,
                    role="primary",
                    status="running",
                    attributes={
                        "prompt_revision": PROMPT_REVISION,
                        "filters": request.filters.model_dump(mode="json"),
                        "intent": classify(request.question),
                        "requested_configuration": request.configuration,
                        "budget_steps": request.budget_steps,
                    },
                )
            )
            return run, config, history

    async def retrieve(
        self,
        config: RAGConfiguration,
        question: str,
        filters: Filters,
    ) -> SearchResult:
        defaults: dict[str, Any] = {}
        if config.strategy != "naive":
            defaults.update(rewrite=True, multi_query=True, parent_child=True)
        options = SearchOptions.model_validate(defaults | config.settings)
        async with self.sessions() as session:
            space = None
            identity = None
            embedder = None
            if options.retrieval in {"vector", "hybrid"}:
                space = await session.get(EmbeddingSpace, config.embedding_space_id)
                if space is None:
                    raise ModelUnavailable("Select an embedding space for vector retrieval")
                model = await session.get(ModelArtifact, space.model_id)
                if model is None:
                    raise ModelUnavailable("The selected embedding model is missing")
                identity = embedding_identity(space, model)
                embedder = self._embedders.get(identity)
                if embedder is None:
                    embedder = self.embedder(identity, self.settings)
                    self._embedders[identity] = embedder
            reranker: Reranker | None = None
            if config.strategy != "naive":
                reranker = CoverageReranker()
                rerank = config.settings.get("reranker")
                if rerank:
                    key = (rerank["name"], rerank["version"])
                    reranker = self._rerankers.setdefault(key, CrossEncoderReranker(*key))
            return await PostgresRetriever(
                session,
                space=space,
                identity=identity,
                provider=embedder,
                reranker=reranker,
            ).retrieve(question, options, filters)

    async def ask(self, actor_id: UUID, request: Question) -> Answer:
        started = perf_counter()
        run, config, history = await self.start(actor_id, request)
        result = SearchResult([], [], {}, None)
        response = ABSTENTION
        citations: list[Citation] = []
        outcome: Literal["completed", "abstained", "failed"] = "abstained"
        verified = True
        error = None
        execution: dict[str, Any] = {}
        try:
            retrieval_config = config
            if request.configuration == "auto":
                retrieval_config, execution["retrieval_depth"] = adaptive_depth(request, config)
            if config.strategy in STRATEGIES:
                initial_budget = request.budget_steps
                if request.configuration == "auto" and initial_budget > 1:
                    initial_budget -= 1
                result = await execute(
                    Context(
                        self, retrieval_config, request.question, request.filters, initial_budget
                    ),
                    run.id,
                )
            else:
                result = await self.retrieve(retrieval_config, request.question, request.filters)
            if (
                request.configuration == "auto"
                and request.budget_steps > 1
                and config.strategy != "multi-query"
                and coverage(request.question, result.hits) < 0.25
            ):
                async with self.sessions() as session:
                    try:
                        fallback = await self.configuration(
                            request.model_copy(
                                update={
                                    "configuration": "multi-query",
                                    "configuration_version": None,
                                }
                            ),
                            session,
                        )
                    except UnknownConfiguration:
                        fallback = None
                if fallback is not None:
                    fallback_config, fallback_depth = adaptive_depth(request, fallback)
                    alternate = await execute(
                        Context(self, fallback_config, request.question, request.filters, 1),
                        uuid5(run.id, "adaptive-fallback"),
                    )
                    if coverage(request.question, alternate.hits) > coverage(
                        request.question, result.hits
                    ):
                        execution["retrieval_depth"] = fallback_depth
                        result = SearchResult(
                            alternate.hits,
                            list(dict.fromkeys(result.queries + alternate.queries)),
                            result.stages
                            | {
                                f"adaptive:{stage}": hits
                                for stage, hits in alternate.stages.items()
                            }
                            | {"final": alternate.hits},
                            result.space_id or alternate.space_id,
                            [*result.workflow, "adaptive fallback", *alternate.workflow],
                        )
            async with self.sessions.begin() as session:
                await record_search(session, run.id, result)
            budget = max(512, min(30_000, int(config.settings.get("context_budget", 12_000))))
            evidence = context(result.hits, budget, request.question)
            if evidence:
                async with self.sessions() as session:
                    model = (
                        await session.get(ModelArtifact, config.model_id)
                        if config.model_id
                        else None
                    )
                if model is None:
                    raise ModelUnavailable("Configure a model for answer generation")
                provider = self.generator(model, self.settings)
                response, citations, verified, usage = await generate_grounded(
                    provider, request.question, evidence, history, config.settings
                )
                if citations:
                    outcome = "completed"
                execution |= {"model": model.name, **usage}
        except (ModelUnavailable, ValidationError, ValueError) as failure:
            error = (
                str(failure)
                if isinstance(failure, ModelUnavailable)
                else "Invalid RAG configuration"
            )
            outcome = "failed"
            response = ""
            verified = False
        except asyncio.CancelledError:
            await asyncio.shield(self.finish(run, "failed", "", {"error": "Query cancelled"}))
            raise
        except Exception as failure:
            logger.warning("RAG run %s failed (%s)", run.id, type(failure).__name__)
            error = "Query execution failed"
            outcome = "failed"
            response = ""
            verified = False
        execution |= {
            "latency_ms": round((perf_counter() - started) * 1000, 1),
            "prompt_revision": PROMPT_REVISION,
            "intent": classify(request.question),
            "retrieval_stages": {name: len(hits) for name, hits in result.stages.items()},
            "embedding_space_id": str(result.space_id) if result.space_id else None,
            "workflow": result.workflow,
        }
        await self.finish(
            run,
            outcome,
            response,
            execution
            | {
                "queries": result.queries,
                "citations_verified": verified,
                "error": error,
                "citations": [citation.model_dump(mode="json") for citation in citations],
            },
        )
        return Answer(
            run_id=run.id,
            conversation_id=run.conversation_id,
            status=outcome,
            answer=response,
            strategy=config.strategy,
            configuration=config.key,
            configuration_version=config.version,
            citations=citations,
            citations_verified=verified,
            sources=result.hits,
            queries=result.queries,
            execution=execution,
            error=error,
        )

    async def finish(self, run: RAGRun, status: str, answer: str, details: dict[str, Any]) -> None:
        async with self.sessions.begin() as session:
            stored = await session.get(RAGRun, run.id, with_for_update=True)
            if stored is None or stored.status != "running":
                return
            if answer:
                message = await Repository(session, Message).add(
                    Message(
                        conversation_id=stored.conversation_id, role="assistant", content=answer
                    )
                )
                stored.answer_id = message.id
            stored.status = status
            stored.completed_at = datetime.now(UTC)
            stored.attributes = stored.attributes | details


def classify(question: str) -> str:
    text = question.casefold()
    if any(term in text for term in ("compare", "difference", "versus", " vs ")):
        return "comparison"
    if text.startswith(("how ", "why ")):
        return "explanation"
    return "lookup"


def choose_strategy(question: str, budget: int) -> str:
    text = question.casefold()
    if budget <= 1:
        return "advanced"
    if any(value in text for value in ("relate", "connection", "relationship", "linked")):
        return "graph"
    if "surrounding context" in text or "whole section" in text:
        return "parent-child"
    if " and also " in text or text.count("?") > 1:
        return "multi-query"
    if classify(text) == "comparison" and budget >= 3:
        return "agentic"
    return "corrective" if len(text.split()) > 15 else "advanced"

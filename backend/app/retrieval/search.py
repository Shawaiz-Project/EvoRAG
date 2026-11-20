import re
from collections import defaultdict
from dataclasses import dataclass, field, replace
from typing import Any, Literal, Protocol
from uuid import UUID

import sqlalchemy as sa
from pgvector.sqlalchemy import VECTOR
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import Select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.knowledge import (
    Chunk,
    ChunkEmbedding,
    Document,
    DocumentVersion,
    EmbeddingSpace,
    Source,
)
from app.retrieval.providers import EmbeddingIdentity, EmbeddingProvider, Reranker


class SearchOptions(BaseModel):
    retrieval: Literal["vector", "keyword", "bm25", "hybrid"] = "hybrid"
    top_k: int = Field(default=8, ge=1, le=100)
    candidate_k: int = Field(default=25, ge=1, le=400)
    rrf_k: int = Field(default=60, ge=1, le=1000)
    vector_min_score: float | None = Field(default=None, allow_inf_nan=False)
    min_score: float | None = Field(default=None, allow_inf_nan=False)
    multi_query: bool = False
    rewrite: bool = False
    parent_child: bool = False

    @model_validator(mode="after")
    def check_capacity(self) -> "SearchOptions":
        if self.candidate_k < self.top_k:
            raise ValueError("candidate_k must be at least top_k")
        return self


class Filters(BaseModel):
    source_ids: list[UUID] = Field(default_factory=list, max_length=100)
    document_ids: list[UUID] = Field(default_factory=list, max_length=100)
    source_kind: Literal["file", "website", "github"] | None = None
    section: str | None = Field(default=None, max_length=500)
    page: int | None = Field(default=None, ge=1)
    attributes: dict[str, Any] = Field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class Hit:
    chunk_id: UUID
    document_version_id: UUID
    document_id: UUID
    source_id: UUID
    title: str
    locator: str
    content: str
    page: int | None
    section: str | None
    parent_id: UUID | None
    score: float
    context: str | None = None


@dataclass(frozen=True, slots=True)
class SearchResult:
    hits: list[Hit]
    queries: list[str]
    stages: dict[str, list[Hit]]
    space_id: UUID | None
    workflow: list[str] = field(default_factory=list)


class Retriever(Protocol):
    async def retrieve(
        self, question: str, options: SearchOptions, filters: Filters | None = None
    ) -> SearchResult: ...


STOP_WORDS = frozenset(
    (
        "a an and are as at can could did do does for how i in is it me of on or please "
        "should tell that the this to was were what when where which who why will with would you"
    ).split()
)


def rewrite_query(question: str) -> str:
    terms = re.findall(r"[\w-]+", question)
    keywords = [term for term in terms if term.casefold() not in STOP_WORDS]
    return " ".join(keywords) or question.strip()


def query_variants(question: str, options: SearchOptions) -> list[str]:
    first = rewrite_query(question) if options.rewrite else question.strip()
    if not options.multi_query:
        return [first]
    clauses = re.split(r"[?!;]|\b(?:and also|as well as)\b", question)
    variants = [question.strip(), first, rewrite_query(question)]
    variants.extend(rewrite_query(clause) for clause in clauses if len(clause.split()) >= 3)
    return list(dict.fromkeys(variant for variant in variants if variant))[:5]


def reciprocal_rank_fusion(rankings: list[list[Hit]], damping: int) -> list[Hit]:
    totals: dict[UUID, float] = defaultdict(float)
    candidates: dict[UUID, Hit] = {}
    for ranking in rankings:
        for rank, hit in enumerate(ranking, 1):
            totals[hit.chunk_id] += 1.0 / (damping + rank)
            candidates.setdefault(hit.chunk_id, hit)
    order = sorted(
        totals,
        key=lambda identifier: (-totals[identifier], candidates[identifier].locator, identifier),
    )
    return [replace(candidates[identifier], score=totals[identifier]) for identifier in order]


def corpus(filters: Filters) -> Select:
    query = (
        sa.select(
            Chunk.id.label("chunk_id"),
            Chunk.document_version_id,
            Document.id.label("document_id"),
            Source.id.label("source_id"),
            Document.title,
            Document.locator,
            Chunk.content,
            Chunk.page,
            Chunk.section,
            Chunk.parent_id,
            Chunk.search_vector,
        )
        .select_from(Chunk)
        .join(DocumentVersion, DocumentVersion.id == Chunk.document_version_id)
        .join(Document, Document.id == DocumentVersion.document_id)
        .join(Source, Source.id == Document.source_id)
        .where(
            DocumentVersion.is_current.is_(True),
            sa.not_(Chunk.attributes.contains({"parent_chunk": True})),
        )
    )
    if filters.document_ids:
        query = query.where(Document.id.in_(filters.document_ids))
    if filters.source_ids:
        query = query.where(Source.id.in_(filters.source_ids))
    if filters.source_kind is not None:
        query = query.where(Source.kind == filters.source_kind)
    if filters.page is not None:
        query = query.where(Chunk.page == filters.page)
    if filters.section is not None:
        query = query.where(Chunk.section == filters.section)
    if filters.attributes:
        query = query.where(Chunk.attributes.contains(filters.attributes))
    return query


class PostgresRetriever:
    def __init__(
        self,
        session: AsyncSession,
        *,
        space: EmbeddingSpace | None = None,
        identity: EmbeddingIdentity | None = None,
        provider: EmbeddingProvider | None = None,
        reranker: Reranker | None = None,
    ) -> None:
        if provider is not None and (
            space is None
            or identity is None
            or identity != provider.identity
            or identity.dimensions != space.dimensions
            or identity.recipe != space.recipe
        ):
            raise ValueError("Embedding provider does not match the configured space")
        self.session = session
        self.space = space
        self.provider = provider
        self.reranker = reranker

    async def retrieve(
        self, question: str, options: SearchOptions, filters: Filters | None = None
    ) -> SearchResult:
        if not question.strip():
            raise ValueError("Search question cannot be empty")
        queries = query_variants(question, options)
        scope = corpus(filters or Filters())
        uses_vector = options.retrieval in {"vector", "hybrid"}
        if uses_vector and (self.provider is None or self.space is None):
            raise ValueError("Vector retrieval requires a configured embedding provider and space")
        vectors = (
            await self.provider.embed(queries, "query") if uses_vector and self.provider else []
        )
        stages: dict[str, list[Hit]] = {}
        for index, query in enumerate(queries):
            if uses_vector:
                stages[f"vector:{index}"] = await self.vector(scope, vectors[index], options)
            if options.retrieval == "keyword":
                stages[f"keyword:{index}"] = await self.keyword(scope, query, options.candidate_k)
            if options.retrieval in {"bm25", "hybrid"}:
                stages[f"bm25:{index}"] = await self.bm25(scope, query, options.candidate_k)

        rankings = list(stages.values())
        selected = (
            reciprocal_rank_fusion(rankings, options.rrf_k) if len(rankings) > 1 else rankings[0]
        )
        if len(rankings) > 1:
            stages["rrf"] = selected[: options.candidate_k]
        selected = selected[: options.candidate_k]
        if self.reranker and selected:
            scores = await self.reranker.score(question, [hit.content for hit in selected])
            selected = sorted(
                (replace(hit, score=score) for hit, score in zip(selected, scores, strict=True)),
                key=lambda hit: (-hit.score, hit.chunk_id),
            )
            stages["reranker"] = selected
        if options.min_score is not None:
            selected = [hit for hit in selected if hit.score >= options.min_score]
        selected = selected[: options.top_k]
        if options.parent_child:
            selected = await self.parents(selected)
        stages["final"] = selected
        space_id = self.space.id if uses_vector and self.space is not None else None
        return SearchResult(selected, queries, stages, space_id)

    async def collect(self, statement: Select) -> list[Hit]:
        rows = await self.session.execute(statement)
        return [Hit(**{**row, "score": float(row["score"])}) for row in rows.mappings()]

    async def keyword(self, scope: Select, query: str, limit: int) -> list[Hit]:
        chunks = scope.subquery()
        term = sa.func.plainto_tsquery(sa.literal_column("'simple'::regconfig"), query)
        score = sa.func.ts_rank_cd(chunks.c.search_vector, term).label("score")
        columns = [column for column in chunks.c if column.key != "search_vector"]
        return await self.collect(
            sa.select(*columns, score)
            .where(chunks.c.search_vector.op("@@")(term))
            .order_by(score.desc(), chunks.c.chunk_id)
            .limit(limit)
        )

    async def bm25(self, scope: Select, query: str, limit: int) -> list[Hit]:
        terms = await self.session.scalar(
            sa.select(
                sa.func.tsvector_to_array(
                    sa.func.to_tsvector(sa.literal_column("'simple'::regconfig"), query)
                )
            )
        )
        if not terms:
            return []
        chunks = scope.cte("search_corpus")
        terms_in_chunk = (
            sa.func.unnest(chunks.c.search_vector)
            .table_valued(
                "lexeme",
                "positions",
                "weights",
            )
            .lateral()
        )
        tokens = (
            sa.select(
                chunks.c.chunk_id,
                terms_in_chunk.c.lexeme,
                sa.func.cardinality(terms_in_chunk.c.positions).label("tf"),
            )
            .select_from(chunks.join(terms_in_chunk, sa.true()))
            .cte("search_tokens")
        )
        lengths = (
            sa.select(tokens.c.chunk_id, sa.func.sum(tokens.c.tf).label("length"))
            .group_by(tokens.c.chunk_id)
            .cte("search_lengths")
        )
        stats = sa.select(
            sa.func.count().label("n"),
            sa.func.avg(lengths.c.length).label("avg_length"),
        ).subquery()
        matched = sa.select(tokens).where(tokens.c.lexeme.in_(terms)).cte("search_matches")
        frequencies = (
            sa.select(matched.c.lexeme, sa.func.count().label("df"))
            .group_by(matched.c.lexeme)
            .subquery()
        )
        idf = sa.func.ln(1 + (stats.c.n - frequencies.c.df + 0.5) / (frequencies.c.df + 0.5))
        normalization = 1.2 * (0.25 + 0.75 * lengths.c.length / stats.c.avg_length)
        ranked = (
            sa.select(
                matched.c.chunk_id,
                sa.func.sum(idf * (matched.c.tf * 2.2) / (matched.c.tf + normalization)).label(
                    "score"
                ),
            )
            .select_from(
                matched.join(frequencies, matched.c.lexeme == frequencies.c.lexeme)
                .join(lengths, matched.c.chunk_id == lengths.c.chunk_id)
                .join(stats, sa.true())
            )
            .group_by(matched.c.chunk_id)
            .subquery()
        )
        columns = [column for column in chunks.c if column.key != "search_vector"]
        return await self.collect(
            sa.select(*columns, ranked.c.score)
            .join(ranked, chunks.c.chunk_id == ranked.c.chunk_id)
            .order_by(ranked.c.score.desc(), chunks.c.chunk_id)
            .limit(limit)
        )

    async def vector(self, scope: Select, query: list[float], options: SearchOptions) -> list[Hit]:
        assert self.space is not None
        if len(query) != self.space.dimensions:
            raise ValueError("Query dimensions do not match the selected space")
        chunks = scope.subquery()
        columns = [column for column in chunks.c if column.key != "search_vector"]
        compatible = (
            sa.select(*columns, ChunkEmbedding.embedding)
            .join(ChunkEmbedding, ChunkEmbedding.chunk_id == chunks.c.chunk_id)
            .where(
                ChunkEmbedding.embedding_space_id == self.space.id,
                ChunkEmbedding.dimensions == self.space.dimensions,
            )
            .cte("compatible")
            .prefix_with("MATERIALIZED", dialect="postgresql")
        )
        operand = sa.bindparam("query_vector", query, type_=VECTOR())
        embedding = compatible.c.embedding
        match self.space.metric:
            case "l2":
                measure = -embedding.l2_distance(operand)
            case "inner_product":
                measure = -embedding.max_inner_product(operand)
            case _:
                measure = 1 - embedding.cosine_distance(operand)
        score = measure.label("score")
        statement = sa.select(
            *(column for column in compatible.c if column.key != "embedding"),
            score,
        )
        if options.vector_min_score is not None:
            statement = statement.where(measure >= options.vector_min_score)
        statement = statement.where(measure != sa.literal_column("'NaN'::double precision"))
        return await self.collect(
            statement.order_by(score.desc(), compatible.c.chunk_id).limit(options.candidate_k)
        )

    async def parents(self, hits: list[Hit]) -> list[Hit]:
        identifiers = {hit.parent_id for hit in hits if hit.parent_id is not None}
        if not identifiers:
            return hits
        parents = dict(
            (
                await self.session.execute(
                    sa.select(Chunk.id, Chunk.content).where(Chunk.id.in_(identifiers))
                )
            )
            .tuples()
            .all()
        )
        return [
            replace(hit, context=parents.get(hit.parent_id)) if hit.parent_id else hit
            for hit in hits
        ]

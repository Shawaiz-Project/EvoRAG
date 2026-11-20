from __future__ import annotations

import asyncio
import json
import re
import sqlite3
from collections import defaultdict
from contextlib import closing
from dataclasses import dataclass, replace
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal, Protocol, TypedDict
from uuid import UUID

import sqlalchemy as sa
from langgraph.graph import END, START, StateGraph
from pydantic import BaseModel, Field, TypeAdapter

from app.db.execution import RAGConfiguration
from app.db.identity import ModelArtifact
from app.retrieval.providers import ModelUnavailable
from app.retrieval.search import (
    Filters,
    Hit,
    SearchResult,
    corpus,
    reciprocal_rank_fusion,
    rewrite_query,
)

if TYPE_CHECKING:
    from app.rag.engine import RAGEngine
    from app.rag.providers import Generator


def execution_configuration(
    configuration: RAGConfiguration, options: dict[str, Any], *, single_pass: bool = False
) -> RAGConfiguration:
    """Build per-execution settings while preserving the persisted configuration identity."""
    return RAGConfiguration(
        key=configuration.key,
        version=configuration.version,
        strategy="naive" if single_pass else configuration.strategy,
        model_id=configuration.model_id,
        embedding_space_id=configuration.embedding_space_id,
        settings=configuration.settings | options,
    )


@dataclass(frozen=True, slots=True)
class Context:
    engine: RAGEngine
    configuration: RAGConfiguration
    question: str
    filters: Filters
    budget: int

    async def search(self, question: str, **options: Any) -> SearchResult:
        single_pass = options.pop("single", False)
        config = execution_configuration(self.configuration, options, single_pass=single_pass)
        return await self.engine.retrieve(config, question, self.filters)

    async def generator(self) -> Generator:
        async with self.engine.sessions() as session:
            model = await session.get(ModelArtifact, self.configuration.model_id)
        if model is None:
            raise ModelUnavailable("Select a model for this workflow")
        return self.engine.generator(model, self.engine.settings)


class BaseRAG(Protocol):
    async def initial(self, ctx: Context) -> SearchResult: ...

    async def advance(
        self,
        ctx: Context,
        result: SearchResult,
        step: int,
    ) -> tuple[str, SearchResult] | None: ...


class SinglePass:
    async def advance(
        self,
        ctx: Context,
        result: SearchResult,
        step: int,
    ) -> tuple[str, SearchResult] | None:
        return None


class AdvancedRAG(SinglePass):
    async def initial(self, ctx: Context) -> SearchResult:
        return await ctx.search(ctx.question, retrieval="hybrid", rewrite=True, multi_query=True)


class MultiQueryRAG(SinglePass):
    async def initial(self, ctx: Context) -> SearchResult:
        return await ctx.search(
            ctx.question,
            retrieval="bm25",
            rewrite=True,
            multi_query=True,
            parent_child=False,
        )


class ParentChildRAG(SinglePass):
    async def initial(self, ctx: Context) -> SearchResult:
        return await ctx.search(
            ctx.question,
            retrieval="bm25",
            rewrite=True,
            multi_query=False,
            parent_child=True,
        )


class Hypothesis(BaseModel):
    text: str = Field(min_length=10, max_length=700)


class HyDERAG(SinglePass):
    async def initial(self, ctx: Context) -> SearchResult:
        provider = await ctx.generator()
        hypothetical = await provider.generate(
            "Write one short hypothetical reference passage answering the question. "
            "This passage is used only as a retrieval query. Put it in the text field.",
            ctx.question,
            Hypothesis,
            210,
        )
        result = await ctx.search(
            hypothetical.output.text,
            retrieval="vector",
            single=True,
            rewrite=False,
            multi_query=False,
        )
        return replace(result, workflow=["hypothetical passage used only for search"])


def terms(text: str) -> set[str]:
    return {part for part in re.findall(r"\w+", rewrite_query(text).casefold()) if len(part) >= 3}


def coverage(question: str, hits: list[Hit]) -> float:
    target = terms(question)
    return max(
        (len(terms(hit.content) & target) / max(1, len(target)) for hit in hits[:4]),
        default=0.0,
    )


def combine(before: SearchResult, after: SearchResult, label: str, top_k: int) -> SearchResult:
    hits = reciprocal_rank_fusion([before.hits, after.hits], 40)[:top_k]
    stages = before.stages | {f"{label}:{stage}": rows for stage, rows in after.stages.items()}
    stages["final"] = hits
    return SearchResult(
        hits,
        list(dict.fromkeys(before.queries + after.queries)),
        stages,
        before.space_id or after.space_id,
        before.workflow,
    )


class CorrectiveRAG:
    async def initial(self, ctx: Context) -> SearchResult:
        return await ctx.search(
            ctx.question,
            retrieval="vector",
            single=True,
            rewrite=False,
            multi_query=False,
        )

    async def advance(
        self, ctx: Context, result: SearchResult, step: int
    ) -> tuple[str, SearchResult] | None:
        if step > 0 or coverage(ctx.question, result.hits) >= 0.8:
            return None
        corrected = await ctx.search(
            rewrite_query(ctx.question),
            retrieval="bm25",
            rewrite=False,
            multi_query=False,
        )
        return "corrective lexical retrieval", combine(
            result,
            corrected,
            "corrective",
            int(ctx.configuration.settings.get("top_k", 5)),
        )


class SelfCheck(BaseModel):
    supported: bool
    query: str = Field(max_length=300)


class SelfRAG:
    async def initial(self, ctx: Context) -> SearchResult:
        return await ctx.search(ctx.question, retrieval="bm25", rewrite=True, multi_query=False)

    async def advance(
        self, ctx: Context, result: SearchResult, step: int
    ) -> tuple[str, SearchResult] | None:
        if step > 0:
            return None
        provider = await ctx.generator()
        assessment = await provider.generate(
            "Check whether the supplied evidence can answer the question. Set supported to true "
            "only if it can. Suggest a concise corrective query when evidence is insufficient.",
            json.dumps(
                {
                    "question": ctx.question,
                    "evidence": [hit.content[:360] for hit in result.hits[:3]],
                }
            ),
            SelfCheck,
            170,
        )
        if assessment.output.supported:
            return "self-check supported", result
        if not assessment.output.query.strip():
            return "self-check abstention", replace(
                result, hits=[], stages=result.stages | {"final": []}
            )
        corrected = await ctx.search(
            assessment.output.query,
            retrieval="hybrid",
            rewrite=True,
            multi_query=False,
        )
        return "self-check corrective search", combine(
            result,
            corrected,
            "self_check",
            int(ctx.configuration.settings.get("top_k", 5)),
        )


class AgenticRAG:
    async def initial(self, ctx: Context) -> SearchResult:
        return await ctx.search(ctx.question, retrieval="keyword", single=True, rewrite=True)

    async def advance(
        self, ctx: Context, result: SearchResult, step: int
    ) -> tuple[str, SearchResult] | None:
        if coverage(ctx.question, result.hits) >= 0.9 or step >= 3:
            return None
        tool: Literal["bm25", "vector", "hybrid"] = ("bm25", "vector", "hybrid")[step]
        followup = await ctx.search(
            ctx.question,
            retrieval=tool,
            rewrite=True,
            multi_query=tool == "hybrid",
        )
        return f"agent tool {tool}", combine(
            result,
            followup,
            f"tool_{step}",
            int(ctx.configuration.settings.get("top_k", 5)),
        )


class GraphRAG(SinglePass):
    async def initial(self, ctx: Context) -> SearchResult:
        candidates = corpus(ctx.filters).subquery()
        fields = [column for column in candidates.c if column.key != "search_vector"]
        async with ctx.engine.sessions() as session:
            rows = (
                (
                    await session.execute(
                        sa.select(*fields)
                        .order_by(candidates.c.locator, candidates.c.chunk_id)
                        .limit(400)
                    )
                )
                .mappings()
                .all()
            )
        links: dict[str, set[UUID]] = defaultdict(set)
        words: dict[UUID, set[str]] = {}
        hits = {}
        for row in rows:
            hit = Hit(**row, score=0)
            hits[hit.chunk_id] = hit
            words[hit.chunk_id] = terms(hit.content)
            for term in words[hit.chunk_id]:
                links[term].add(hit.chunk_id)
        initial = terms(ctx.question)
        frontier = set(initial)
        visited: set[str] = set()
        scores: dict[UUID, float] = defaultdict(float)
        for hop in range(min(3, ctx.budget)):
            reachable: set[str] = set()
            for term in sorted(frontier):
                neighbors = links[term]
                for chunk_id in neighbors:
                    scores[chunk_id] += 1 / ((hop + 1) * len(neighbors))
                    reachable.update(words[chunk_id])
            visited.update(frontier)
            frontier = reachable - visited
            if not frontier:
                break
        ordered = sorted(
            scores,
            key=lambda identifier: (-scores[identifier], hits[identifier].locator, identifier),
        )
        ranked = [replace(hits[item], score=scores[item]) for item in ordered]
        selected = ranked[: int(ctx.configuration.settings.get("top_k", 5))]
        return SearchResult(
            selected,
            [ctx.question],
            {"graph": ranked[:25], "final": selected},
            None,
            ["term-to-chunk graph traversal"],
        )


STRATEGIES: dict[str, BaseRAG] = {
    "advanced": AdvancedRAG(),
    "corrective": CorrectiveRAG(),
    "self-rag": SelfRAG(),
    "agentic": AgenticRAG(),
    "graph": GraphRAG(),
    "multi-query": MultiQueryRAG(),
    "hyde": HyDERAG(),
    "parent-child": ParentChildRAG(),
}


class State(TypedDict, total=False):
    result: str
    step: int
    stopped: bool
    trace: list[str]


SEARCH_RESULT = TypeAdapter(SearchResult)


def load_checkpoint(path: Path, run_id: UUID) -> State:
    with closing(sqlite3.connect(path, timeout=15)) as database, database:
        database.execute("""
            CREATE TABLE IF NOT EXISTS checkpoints (
                sequence INTEGER PRIMARY KEY,
                thread_id TEXT NOT NULL,
                state TEXT NOT NULL
            )
        """)
        database.execute("""
            CREATE INDEX IF NOT EXISTS ix_checkpoints_thread
            ON checkpoints(thread_id, sequence)
        """)
        row = database.execute(
            "SELECT state FROM checkpoints WHERE thread_id = ? ORDER BY sequence DESC LIMIT 1",
            (str(run_id),),
        ).fetchone()
    return json.loads(row[0]) if row else {}


def save_checkpoint(path: Path, run_id: UUID, state: State) -> None:
    with closing(sqlite3.connect(path, timeout=15)) as database, database:
        database.execute(
            "INSERT INTO checkpoints(thread_id, state) VALUES (?, ?)",
            (str(run_id), json.dumps(state)),
        )


async def execute(ctx: Context, run_id: UUID) -> SearchResult:
    strategy = STRATEGIES[ctx.configuration.strategy]
    path = ctx.engine.settings.workflow_checkpoint_db

    async def initial(state: State) -> State:
        result = await strategy.initial(ctx)
        checkpoint: State = {
            "result": SEARCH_RESULT.dump_json(result).decode(),
            "step": 0,
            "stopped": False,
            "trace": [ctx.configuration.strategy, *result.workflow],
        }
        await asyncio.to_thread(save_checkpoint, path, run_id, checkpoint)
        return checkpoint

    async def advance(state: State) -> State:
        current = SEARCH_RESULT.validate_json(state["result"])
        action = await strategy.advance(ctx, current, state["step"])
        if action is None:
            update: State = {"stopped": True}
            await asyncio.to_thread(save_checkpoint, path, run_id, state | update)
            return update
        label, result = action
        update = {
            "result": SEARCH_RESULT.dump_json(result).decode(),
            "step": state["step"] + 1,
            "trace": [*state["trace"], label],
        }
        await asyncio.to_thread(save_checkpoint, path, run_id, state | update)
        return update

    def next_node(state: State) -> Literal["advance", "__end__"]:
        return END if state["stopped"] or state["step"] + 1 >= ctx.budget else "advance"

    def entry(state: State) -> Literal["initial", "advance", "__end__"]:
        return next_node(state) if "result" in state else "initial"

    graph = StateGraph(State)
    graph.add_node("initial", initial)
    graph.add_node("advance", advance)
    graph.add_conditional_edges(START, entry)
    graph.add_conditional_edges("initial", next_node)
    graph.add_conditional_edges("advance", next_node)
    workflow = graph.compile()
    saved = await asyncio.to_thread(load_checkpoint, path, run_id)
    final: State = await workflow.ainvoke(saved or {"step": 0, "stopped": False, "trace": []})
    return replace(SEARCH_RESULT.validate_json(final["result"]), workflow=final["trace"])

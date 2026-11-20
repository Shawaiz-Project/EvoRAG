import argparse
import asyncio
import json
import re
from pathlib import Path
from uuid import UUID

from sqlalchemy.ext.asyncio import create_async_engine

from app.core.config import Settings
from app.db.session import session_factory
from app.rag.engine import Question, RAGEngine
from app.rag.strategies import STRATEGIES
from app.retrieval.search import Filters

FIXTURE = Path(__file__).resolve().parents[2] / "benchmarks" / "strategy_rrf_v1.json"


async def main(actor: UUID, document: UUID) -> None:
    fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))
    settings = Settings()
    engine = create_async_engine(settings.database_url)
    try:
        rag = RAGEngine(session_factory(engine), settings)
        rows = []
        for case in fixture["cases"]:
            for strategy in STRATEGIES:
                answer = await rag.ask(
                    actor,
                    Question(
                        question=case["question"],
                        configuration=strategy,
                        filters=Filters(document_ids=[document]),
                    ),
                )
                rows.append(
                    {
                        "case": case["key"],
                        "strategy": strategy,
                        "status": answer.status,
                        "correct": bool(re.search(case["expected_pattern"], answer.answer)),
                        "verified_citations": len(answer.citations)
                        if answer.citations_verified
                        else 0,
                        "run_id": str(answer.run_id),
                        "workflow": answer.execution.get("workflow"),
                        "latency_ms": answer.execution.get("latency_ms"),
                    }
                )
        print(json.dumps({"dataset_version": fixture["version"], "results": rows}, indent=2))
    finally:
        await engine.dispose()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Compare the RAG presets on the RRF paper.")
    parser.add_argument("--actor", required=True, type=UUID)
    parser.add_argument("--document", required=True, type=UUID)
    arguments = parser.parse_args()
    asyncio.run(main(arguments.actor, arguments.document))

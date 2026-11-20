from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.execution import RetrievalResult
from app.retrieval.search import SearchResult


async def record_search(
    session: AsyncSession, run_id: UUID, result: SearchResult, *, iteration: int = 0
) -> None:
    session.add_all(
        RetrievalResult(
            run_id=run_id,
            stage=stage,
            iteration=iteration,
            rank=rank,
            chunk_id=hit.chunk_id,
            score=hit.score,
            attributes={
                "parent_id": str(hit.parent_id) if hit.parent_id else None,
                "embedding_space_id": str(result.space_id) if result.space_id else None,
            },
        )
        for stage, hits in result.stages.items()
        for rank, hit in enumerate(hits, 1)
    )
    await session.flush()

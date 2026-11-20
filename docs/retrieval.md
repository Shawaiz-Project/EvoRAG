# Embedding and retrieval

The default seed registers MiniLM with a pinned model revision and a normalized 384-dimensional cosine space. Install the local inference dependencies and index the current corpus from `backend/`.

```sh
uv sync --locked --extra local-models
uv run --locked --extra local-models python -m app.retrieval.index --space minilm --version 1
```

Local providers load Sentence Transformers, BGE, and E5 artifacts with the recorded revision. The BGE and E5 recipes apply their respective query or passage prefixes. The Gemini provider uses `gemini-embedding-001`, explicit query and document task types, and `GEMINI_API_KEY` from the server environment. Supply a registered model and embedding space compatible with the recipe before indexing with a different provider. Cross-encoder reranking uses a separately configured model revision. The default local dependency extra includes PyTorch.

The PostgreSQL retriever supports exact pgvector search, PostgreSQL keyword ranking, BM25 scoring, and hybrid rank fusion. It can rewrite a question with a lexical heuristic, split compound questions into several variants, filter by source, document, page, section, or chunk metadata, and return parent context for matched leaves. Every retrieval returns stage ranks, scores, effective queries, and the embedding space ID. Saved RAG executions persist stage ranks and chunk IDs.

Vector scoring uses a materialized CTE restricted to one embedding space before applying distance operators. BM25 computes term statistics over the filtered corpus in SQL. These implementations favor correctness and repeatability on small research corpora. Large corpora will need space-specific vector indexes and precomputed or indexed BM25 statistics.

## Measured retrieval fixture

Run `EVORAG_DB_TESTS=1 uv run --locked pytest -s tests/test_retrieval.py` on a local PostgreSQL server. In PowerShell, set `$env:EVORAG_DB_TESTS = '1'` before invoking pytest.

The versioned fixture contains six passages and four questions with one known relevant passage each. The test loads it into a fresh migrated PostgreSQL database and runs each method with a cutoff of three. The vector rows use hand-assigned embeddings to verify ranking and space isolation. Learned embedding quality requires a separate benchmark.

| Method | Relevant ranks in question order | Recall@3 | MRR@3 |
| --- | --- | ---: | ---: |
| Keyword, all terms | None, none, none, none | 0.00 | 0.00 |
| BM25 | 1, 1, 1, 1 | 1.00 | 1.00 |
| Exact pgvector cosine | 1, 1, 1, 1 | 1.00 | 1.00 |
| Hybrid BM25 and vector via RRF | 1, 1, 1, 1 | 1.00 | 1.00 |

The keyword method required every term in each full question to appear in the same passage. BM25 returned partial matches, including each relevant passage at rank one on this fixture. The same test verifies metadata filters, parent expansion, idempotent indexing, and isolation with a second embedding space of a different dimension.

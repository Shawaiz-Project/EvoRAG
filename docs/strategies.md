# Strategy workflows

Run `uv run --locked python -m app.rag.seed_strategies` from `backend/` after activating and indexing the model profile. This versions alternative presets from the active Advanced model and embedding space.

| Preset | Search workflow |
| --- | --- |
| `advanced` | Query variants, hybrid BM25 and vector search, rank fusion, reranking, and parent context. |
| `corrective` | Single vector search, lexical coverage assessment, and corrective BM25 search when coverage is low. |
| `self-rag` | BM25 search followed by a model-based sufficiency check and an optional corrective search. |
| `agentic` | Bounded tool selection across keyword, BM25, vector, and hybrid search. Coverage determines whether to continue. |
| `graph` | Multi-hop traversal of term and chunk links in the filtered corpus. |
| `multi-query` | Several query variants followed by per-query BM25 rankings and reciprocal rank fusion. |
| `hyde` | Generate a hypothetical passage and use it as the vector retrieval query. Retrieved corpus chunks are the evidence for the answer. |
| `parent-child` | Retrieve leaf chunks via BM25 and expand their parent context before generation. |

Use an explicit configuration key for a forced strategy. `auto` selects a starting strategy using query intent, complexity, and `budget_steps`. Corrective and agentic workflows use retrieval coverage during execution. The adaptive and iterative paths are limited to the configured budget of one to four steps.

Auto also selects the maximum number of evidence chunks passed onward by retrieval. The starting targets are 4 for lookup, 8 for technical explanations, 14 for comparisons or explicit multi-document scopes, and 18 for long or multi-part research questions. A versioned RAG configuration can override any target under `adaptive_depths`, using the keys `lookup`, `technical`, `multi_document`, and `research`. The selected target respects the profile's `candidate_k` cap and may yield fewer chunks when evidence is sparse. An accepted fallback records its own budget. The API and saved run expose the category, effective `top_k`, and retrieval configuration in `execution.retrieval_depth`. Forced configurations and independent teacher reviews use their configured depths.

LangGraph executes the workflow and writes durable node state to a local SQLite checkpoint store keyed by the run UUID. Set `WORKFLOW_CHECKPOINT_DB` to change its location. RAG runs store the chosen configuration, workflow trace, queries, evidence, and stage ranks in PostgreSQL.

`POST /api/v1/rag/compare` executes selected configurations on the same question and filters. Each configuration writes its own run and returns an answer, citation status, and execution trace. The integration test runs Advanced, Graph, and Parent–Child over the same corpus and verifies distinct retrieval stages and persisted checkpoints.

The fixed RRF benchmark is stored in `backend/benchmarks/strategy_rrf_v1.json`. Upload and index the linked paper, then pass your authenticated user ID and the document ID to `uv run --locked python -m app.rag.benchmark_strategies --actor USER_UUID --document DOCUMENT_UUID`. The command emits measured answer matching, citation counts, latency, and run IDs for each preset.

## Local benchmark result

The following run used the two-page RRF paper, MiniLM embeddings, and Qwen 2.5 1.5B in extractive mode. All eight pipelines returned one verified citation. The expected answer check matched the value 60 as a standalone number. The measured latencies reflect one local CPU run and its model caches.

| Preset | Expected value found | Latency |
| --- | --- | ---: |
| Advanced | Yes | 10.2 s |
| Corrective | Yes | 7.7 s |
| Self-RAG | Yes | 10.8 s |
| Agentic | Yes | 1.6 s |
| Graph | No | 8.0 s |
| Multi-query | Yes | 7.7 s |
| HyDE | Yes | 14.5 s |
| Parent–Child | Yes | 8.6 s |

The Graph result demonstrates that citation verification and answer correctness measure different properties. The fixture supports comparison on this question only.

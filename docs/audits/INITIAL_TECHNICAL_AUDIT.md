# EvoRAG 2.0 initial technical audit

## 1. Executive summary

The existing local application has working Student and Teacher RAG, versioned knowledge, and a measured experiment pipeline. It also has two confirmed integration faults and one intermittent ingestion regression. The knowledge API allowed anonymous reads and writes even though the dashboard required sign-in. The configured development origin could authenticate by cookie, but its browser preflight for the bearer fallback returned HTTP 400. One ingestion test accepted a DOCX job, then the worker did not claim it immediately. Fix these boundaries before adding autonomous coordination or more dashboard pages.

Statuses are **VERIFIED**, **PARTIALLY IMPLEMENTED**, **PLACEHOLDER**, **MISSING**, **BROKEN**, and **NOT TESTABLE**. Verified evidence may come from the current live deployment, current tests, or a dated-by-scope prior run described in the table. Source inspection alone is marked separately where runtime behavior remains unverified. This report records the baseline before application changes.

## 2. Actual architecture

- Compose runs Nginx, FastAPI, PostgreSQL 17 with pgvector, Redis, a PostgreSQL-backed ingestion worker, and a one-shot Alembic/seed job. Nginx terminates local HTTP and proxies the API and SSE. Host Vite and FastAPI are an alternate development topology. See [Compose](../../compose.yaml), [Nginx](../../frontend/nginx.conf), and [typed environment settings](../../backend/app/core/config.py).
- `create_app` opens the SQLAlchemy/asyncpg engine and Redis client through `open_services`. FastAPI dependencies supply services and settings. `get_rag` exposes the lifespan-managed Student engine. `TeacherEngine` receives the Student engine to reuse provider construction and retrieval infrastructure, and resolves a separate teacher configuration and separate retrieval run. See [application wiring](../../backend/app/main.py), [services](../../backend/app/infrastructure/services.py), and [teacher service](../../backend/app/rag/teacher.py).
- The Python 3.12 and Node dependencies are locked with uv and npm. Model endpoints and keys are backend settings. Redis supplies readiness probing. PostgreSQL stores queue state and artifacts, and SQLite stores LangGraph checkpoints. The checkout has no Git metadata available, so the audit uses the workspace baseline. See [Python manifest](../../backend/pyproject.toml), [frontend manifest](../../frontend/package.json), and [workflow implementation](../../backend/app/rag/strategies.py).

## 3. Implemented features

| Capability | Status | Evidence |
| --- | --- | --- |
| API lifecycle, configuration, liveness, readiness | VERIFIED | `main.create_app`, `Services.readiness`; `GET /health`, `GET /ready`. The live readiness returned HTTP 200 with PostgreSQL and Redis true after runtime recovery. `test_health.py` passed. |
| SQLAlchemy, model constraints, migration and seed | VERIFIED | `db/knowledge.py`, `db/repositories.py`, Alembic `0001` through `0006`; live revision `0006`, no drift, and four passing database tests. Covered foreign keys, concurrent duplicates, upgrade/downgrade, dimensions, and embedding-space isolation. |
| Cookie and bearer sessions | VERIFIED | `api/auth.py` issues opaque tokens, stores hashes and expiry, and revokes on logout. `/auth/login`, `/auth/token`, `/auth/me`, `/auth/logout`. `test_authenticated_stream_and_trace` passes both transports. Separate-origin browser transport is broken below. |
| Text, PDF, DOCX and table extractors | VERIFIED | `ingestion/extraction.py::extract_file`, `pdf_blocks`, `docx_blocks`, `table_rows`; fixtures cover PDF, DOCX, TXT, Markdown, CSV, XLSX. Format and chunking tests passed. End-to-end queue timing is broken below. |
| Durable ingestion and versions | PARTIALLY IMPLEMENTED | `ingestion/jobs.py::JobRepository`, `IngestionWorker.run_once`; upload, remote, job status and retry routes. Lease recovery passed. Acceptance and immediate claim were intermittently inconsistent. |
| Vector and lexical retrieval | VERIFIED | `retrieval/search.py::PostgresRetriever` implements vector, plain keyword, BM25, hybrid RRF, document/source/metadata scopes, parent lookup, and reranking. `retrieval/index.py` maps chunks to versioned vector spaces. `test_fixed_retrieval_benchmark` passed. Real Ollama evidence and fixture scope are documented in [retrieval results](../retrieval.md). |
| Student answer and citations | VERIFIED | `rag/engine.py::RAGEngine.ask`, `evidence.validate_draft` and `validate_excerpt`; `/rag/query`, stream and trace. PostgreSQL integration verifies streaming, owned history, citations and ranks with a controlled model. The prior real-PDF run used Ollama. |
| Independent Teacher and correction | VERIFIED | `TeacherEngine.review` reads the question, resolves the separate `teacher` profile and calls retrieval with the original filters. Its context is from its own ranked results. It reads the Student answer and original citations separately to audit them. `/teacher/reviews/{run_id}` and `/teacher/reviews/{review_id}/refine` persist evidence and bound refinements. `test_human_labeled_teacher_cases_and_refinement` passed pass/revise/reject and attempt-limit checks. |
| Controlled publication | VERIFIED | `LearningEngine.verify/decide/publish/rollback`, `api/learning.py::require_admin`; feedback, proposals, verification, decisions, publication, and rollback. The test passed rejection, approval by a separate reviewer, re-indexing, retrieval of new knowledge and rollback with audit entries. No model-call path directly invokes approval. |
| Measured experiments | VERIFIED | `EvaluationRunner.run`, `evaluation/metrics.py`; benchmark and experiment endpoints. Unit metrics tests passed, and the [historical measured run](../evaluation-report.md) compares four strategies with an MLflow run ID. The single labeled question establishes pipeline operation and gives limited accuracy evidence. |

## 4. Partially implemented capabilities

| Capability | Status | Evidence and boundary |
| --- | --- | --- |
| Adaptive retrieval | PARTIALLY IMPLEMENTED | `RAGEngine.configuration` supports automatic strategy selection and `SearchOptions` exposes fixed top-k and candidate settings. `query_variants` rewrites and splits queries. Evidence budgets are still set by the selected profile and bounded strategy steps. No calibrated quality-driven depth policy is established. |
| Advanced extraction | PARTIALLY IMPLEMENTED | `PageOCR` is an extension interface. Worker startup does not configure an OCR implementation. PDF extraction comes from pypdf text; image semantics, PDF table reconstruction and research-paper structure extraction are absent. Semantic chunk boundaries use lexical similarity. |
| Teacher grading and reliability | PARTIALLY IMPLEMENTED | `Critique` stores whole-answer judgment and free-text unsupported/missing content. `primary_citations` checks original identifiers and quote/numeric integrity. There is no ordered per-claim verdict with independent quote references, calibrated confidence, contradiction inventory, or multi-teacher disagreement workflow. |
| Alternative strategies | PARTIALLY IMPLEMENTED | `strategies.py` implements separate Corrective, Self, Agentic, Graph, HyDE, Multi-query, Parent-child and Advanced variants. Graph uses an in-process graph over candidate text. Agentic uses bounded search, and Self-RAG reflection is focused on retrieval. The tests verify distinct traces and [the prior benchmark](../strategies.md) records eight real runs and a Graph answer miss. There is no persisted graph or general tool/workflow editor. |
| Evolution and gaps | PARTIALLY IMPLEMENTED | `api/learning.py::gaps` groups normalized repeated text for failed/abstained questions. `LearningEngine.verify` checks proposed text, conflicts, an optional external source, and linked golden cases. It does not cluster paraphrases, generate tested configuration/prompt changes, or measure the same benchmark before and after a publication. |
| Conversation memory | PARTIALLY IMPLEMENTED | `api/rag.py::conversations/messages` authorizes by owner, and `RAGEngine.start` includes bounded prior turns. There is no episodic or semantic memory service, retrieval policy, or retention/deletion UI. Verified long-term knowledge already lives in versioned document rows. |
| Research scorecard | PARTIALLY IMPLEMENTED | Experiment configuration snapshots, retrieval metrics, lexical scoring, provider usage, optional MLflow and RAGAS faithfulness exist. Extended RAGAS execution, claim-level teacher calibration, comparable before/after publication evidence, and representative regression cohorts are beyond the current measured baseline. |
| Error handling and logs | PARTIALLY IMPLEMENTED | Typed API errors, worker retry/backoff, rotating logs and probe failures exist. `stream_query` cancels on disconnect, and `RAGEngine.ask` attempts to persist cancellation. There is no recovery policy for records left running after the API process exits abruptly. |

## 5. Missing, placeholder and unverified modules

| Requested module | Status | Evidence |
| --- | --- | --- |
| AI Supervisor and live execution monitor | MISSING | `GET /ready` and `GET /workflows` provide readiness and records. No component orchestrates service health, routes incidents, or streams operational job state. |
| Independent Teacher Council | MISSING | `TeacherEngine.review` resolves one teacher profile per evaluation. |
| Knowledge Graph Explorer | MISSING | Graph retrieval has no durable entity/edge schema or explorer API. |
| Research Paper Assistant and Trust Center | MISSING | Inspector shows text and provenance, and Playground displays citations. Dedicated structured paper analysis, cross-paper comparison, source trust and conflict views are absent. |
| Visual Workflow Builder | MISSING | `WorkflowManager` reads stored traces. It has no graph editor, validation, versioned workflow definition, or deployment endpoint. |
| Production fake implementations | No PLACEHOLDER found in inspected routes | Existing data views use API clients and real retrieval/provider errors. Test-only provider doubles are in `backend/tests`. |
| Hosted Gemini and optional RAGAS execution | NOT TESTABLE in this audit | No successful live run was recorded for the configured Gemini key. Optional RAGAS was not exercised by the recorded benchmark. A provider adapter or dependency declaration does not establish live execution. |

## 6. Broken integrations

- **BROKEN, anonymous corpus API.** At the audit baseline unauthenticated `GET /api/v1/documents` and `GET /api/v1/ingestion/jobs` both returned 200 from the live API. Source inspection of `api/ingestion.py` shows the shared router also leaves detail, upload, remote enqueue, and retry unguarded. No unauthorized write was attempted.
- **BROKEN, development bearer transport.** An in-process preflight to `GET /api/v1/auth/me` from configured `http://127.0.0.1:5173` requesting `Authorization` returned 400. `create_app` restricts origins and methods but omitted the bearer header. Docker's same-origin dashboard does not need this preflight.
- **BROKEN intermittently, immediate ingestion claim.** After service recovery the full suite returned 37 passed and one failed. `test_upload_worker_and_inspector` accepted a DOCX upload with 202 but `worker.run_once()` returned false. The database creates `available_at` while `JobRepository.claim` compares it to the worker host clock. That mixed-clock condition can make committed jobs temporarily ineligible when hosts are skewed.

## 7. Security weaknesses

The unauthenticated corpus is the highest-priority issue. The current data model represents one shared corpus, with no tenant or per-document access policy. Restricting the HTTP boundary to a signed-in user preserves the current shared-workspace model. Protecting publisher-controlled sources from edits by ordinary members needs a distinct write/ownership policy. Existing boundaries include admin checks for proposal approval/publication, owner checks for runs and messages, bounded uploads, public-address validation for external fetches, secure-cookie configuration, and hashed server-side sessions. The audit did not find a systematic adversarial prompt-injection acceptance suite or a login throttling policy. Keep published content and retrieved text outside any authority to change settings or invoke external tools.

## 8. Performance weaknesses

`PostgresRetriever.vector` computes exact distance over its filtered embedding space, and the mapped table has no shipped ANN index. Measure representative plans before adding per-dimension, per-space indexes. `EvaluationRunner.run` serializes dataset cases and strategies inside a request, which risks request timeouts and stale status. The frontend build emitted an approximately 803 kB minified entry-bundle warning. Redis has no application-level cache or job orchestration responsibilities. Measure cold/warm latency and provider cost separately in a representative scorecard.

## 9. Technical debt

- `TeacherEngine`, `LearningEngine`, and `EvaluationRunner` depend on the full `RAGEngine` for shared operations. Extract narrow model and retrieval interfaces incrementally while retaining existing route contracts.
- `docs/database.md` names an `experiment_measurements` table, while `db/benchmarks.py` and the experiment API store and read per-case rows via `benchmark_results`.
- The repository has backend tests but no packaged frontend test command. Prior development smoke scripts lived in ignored local tooling. Add a reusable browser contract suite covering protected navigation and forms.

## 10. Database findings

The live database is at `0006` with no Alembic model drift. Content hashes, UUID identities, timezone-aware timestamps, embedding dimensions, current-version uniqueness and immutable history are enforced. PG stores both the available-time and lease-time fields, but scheduler decisions use the process clock. Knowledge and jobs have no ownership columns. The ranking path uses a GIN text index and exact vector search. The previous verified custom-format backup/restore is described in [deployment](../deployment.md). It was outside this baseline rerun.

## 11. Frontend and testing coverage

All fourteen routes exist. The preceding deployed-browser run exercised live protected requests on all fourteen at 1366 and 390 pixels. This audit reran lint, formatting, TypeScript and production build. Source inspection traced the actions below. Rows marked partial have a concrete missing control or advanced flow, even when their existing API is functional.

| Page | API and control evidence | Status |
| --- | --- | --- |
| Dashboard | `Operations.Dashboard`; readiness, document/job counts, experiments, workflows and teacher analytics | VERIFIED |
| RAG Playground | `Playground`; configurations, SSE query, teacher review/refinement, feedback; sources and run details | VERIFIED |
| Knowledge Base | `KnowledgeBase`; document/job reads, upload, remote import, retry; explicit indexing command outside UI | PARTIALLY IMPLEMENTED |
| Document Inspector | `DocumentInspector`; document/version/chunk detail, provenance and paging | VERIFIED |
| Teacher Center | `TeacherCenter`; reviews and analytics; no structured claim table | PARTIALLY IMPLEMENTED |
| Learning Center | `LearningCenter`; administrator proposals, gaps, verify, decide, publish and rollback | VERIFIED |
| Comparison Lab | `ComparisonLab`; configuration selection and `POST /rag/compare` | VERIFIED |
| Experiments | `Experiments`; datasets, runs, results and export; execution is synchronous | PARTIALLY IMPLEMENTED |
| Benchmark Manager | `BenchmarkManager`; create and inspect versioned datasets and cases | VERIFIED |
| Analytics | `Analytics`; live teacher aggregates and recorded experiment results | VERIFIED |
| Memory Center | `MemoryCenter`; ownership-scoped conversation/messages; no memory policy controls | PARTIALLY IMPLEMENTED |
| Model Registry | `ModelRegistry`; registered models and capabilities; no model creation/deactivation in UI | PARTIALLY IMPLEMENTED |
| Workflow Manager | `WorkflowManager`; persisted run/trace inspection; no editable definitions | PARTIALLY IMPLEMENTED |
| Settings | `SettingsPage`; local theme update and read-only backend runtime configuration | VERIFIED |

The shared `Data` component displays loading and errors. Ingestion and research forms show request feedback. Registry and developer configuration controls described as absent above have no dummy buttons. The first integration attempt produced 27 passes and 11 database setup errors while the local WSL engine was unavailable. Recovery restored both probes and migration checks. The second attempt produced 37 passes and one ingestion failure described above. Ruff, ESLint, Prettier and the production build passed. No packaged frontend test runner exists.

## 12. Recommended improvements

Preserve route payloads, version identities and evidence semantics. Add a regression test for every boundary fix. For the scorecard, run the same representative labeled suite at each pinned corpus/configuration revision. Track Recall@5, context precision, faithfulness, correctness, citation support, teacher precision and false approval, coverage, latency and cost. Record denominators and unavailable metrics explicitly. Compare publication candidates and retrieval presets with the same dataset, and publish only after a human decision.

## 13. Prioritized implementation roadmap

1. Require a valid session for corpus metadata, document contents, uploads, remote ingestion, and retries. Add negative and positive API coverage.
2. Use the PostgreSQL clock consistently for queue eligibility, retry deadlines and leases. Verify the regression with simulated worker clock skew.
3. Enable the bearer header for configured development origins while retaining an explicit origin policy.
4. Define corpus write permissions and a guarded publication policy for existing authoritative sources. Cover authorization and adversarial retrieved content in regressions.
5. Split model/retrieval interfaces from Student orchestration. Improve document structure, optional OCR, and adaptive evidence depth under reproducible benchmarks.
6. Add per-claim Teacher verdicts with independently validated evidence references, contradiction handling and calibration data. Add a Teacher Council only after the single-teacher contract is measured.
7. Add durable experiment jobs and a representative, versioned scorecard. Evaluate retrieval indexing and caching with representative plans and latency samples.
8. Add topic-level knowledge-gap clustering and before/after validation for approved versions, prompts and retrieval configuration candidates.
9. Define conversational, episodic and semantic memory policies with ownership, retention and deletion before exposing them in Memory Center.
10. Build an AI Supervisor, live monitoring and advanced workspace screens against verified interfaces. Add repeatable browser regressions and finish with a full integration report.

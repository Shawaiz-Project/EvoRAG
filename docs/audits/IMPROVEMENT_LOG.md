# EvoRAG 2.0 improvement log

The [initial technical audit](INITIAL_TECHNICAL_AUDIT.md) records the baseline. This log records changes and the checks that exercised them.

## 1. Shared-corpus authentication and development CORS

**Problem.** The deployed document and ingestion lists accepted anonymous requests. The CORS middleware rejected the bearer fallback on configured development origins.

**Change.** The knowledge router uses the existing `current_user` dependency for every route. The CORS middleware explicitly permits `Authorization` for its configured origins. Request and response payloads remain compatible. API-based ingestion clients now need a session. [Knowledge router](../../backend/app/api/ingestion.py) · [API setup](../../backend/app/main.py) · [Updated ingestion guide](../ingestion.md)

**Verification.** Eight parameterized endpoint checks require 401 without a session. The real PostgreSQL ingestion test signs in and exercises uploads and version inspection. The deployed anonymous document endpoint changed from 200 to 401. The configured Vite bearer preflight changed from 400 to 200, and a disallowed origin still fails the test. All fourteen dashboard routes still loaded at 1366 px and 390 px in an authenticated browser. [Boundary tests](../../backend/tests/test_health.py) · [Ingestion integration test](../../backend/tests/test_ingestion.py)

**Remaining limit.** The corpus is shared among signed-in local workspace users. Per-document ownership and a privileged write policy for authoritative sources require a separate change.

## 2. Consistent ingestion scheduling

**Problem.** PostgreSQL supplied the initial availability timestamp, and workers compared it to their host clocks. A successful DOCX upload intermittently remained temporarily invisible to an immediate worker claim.

**Change.** Job eligibility, lease deadlines, retry scheduling and completion times use PostgreSQL's clock. The same row locks, lease tokens, retry limits and API contracts apply. This required no migration. [Queue repository](../../backend/app/ingestion/jobs.py) · [Worker](../../backend/app/ingestion/worker.py)

**Verification.** The upload and inspection test simulates a worker clock that lags the database by five minutes and processes all six file types, then verifies duplicate detection and version creation. Stale-claim recovery also passed. The backend suite passed 47 tests after the foundation fixes, up from a baseline of 37 passes and one failure after runtime recovery. [Ingestion tests](../../backend/tests/test_ingestion.py)

**Remaining limit.** Worker liveness and overdue-job thresholds are still outside `/ready`.

## 3. Adaptive evidence depth in Student RAG

**Problem.** Auto chose a retrieval strategy, and each selected configuration still used its fixed top-k.

**Change.** Auto assigns an evidence category and a configurable top-k bounded by the selected profile's candidate limit. The engine applies the budget to the actual strategy search and persists the effective choice. Forced configurations and independent Teacher RAG keep their configured depths. [Student engine](../../backend/app/rag/engine.py) · [Strategy configuration](../../backend/app/rag/strategies.py) · [Operating guide](../strategies.md)

**Verification.** Five parameterized cases cover short lookup, technical explanation, comparison, explicit multi-document scope, and multi-part research. They verify configurable targets, the cap, and unchanged persisted profile settings. The full backend suite passed 52 tests with PostgreSQL enabled. [Strategy tests](../../backend/tests/test_strategies.py)

On the indexed RRF research PDF, fixed Advanced and Auto both used Advanced profile v3, answered 60, and returned a verified citation. Fixed Advanced used five chunks, 366 generation input tokens, and ranked its cited chunk first. Auto used four chunks, 221 input tokens, and ranked its cited chunk second. Wall times were 1,110 ms and 1,234 ms respectively in this sequential local run. The Auto budget also appeared in the saved trace. This comparison covers one lookup and establishes neither an accuracy gain nor a latency improvement.

**Remaining limit.** Routing uses deterministic question and scope heuristics. Its accuracy, latency and cost require a broader labeled benchmark.

## 4. Claim-level independent teacher review

**Problem.** The teacher saved an overall verdict and free-text critique. Its direct-excerpt shortcut could treat a supported first claim as representative of a longer answer.

**Change.** The teacher assesses up to eight claim segments against separately retrieved passages. The server resolves evidence identifiers and verifies quotes before attaching chunk provenance. A numeric claim needs matching numbers in its supporting evidence. A missing or unverified claim prevents a pass. Playground and Teacher Center show the claim status and verified quotes. Existing stored reports remain readable. [Teacher logic](../../backend/app/rag/teacher.py) · [Evidence validation](../../backend/app/rag/teacher_claims.py) · [Teacher guide](../teacher.md)

**Verification.** The PostgreSQL teacher test covers a supported claim, a contradiction, an answer with an unverified second claim despite an overall model pass, absent evidence, and bounded refinement. A separate test rejects an unknown evidence identifier. The full backend suite passed 53 tests with database integration enabled. Ruff, ESLint, Prettier, TypeScript and production Vite and Docker builds passed. [Teacher tests](../../backend/tests/test_teacher.py)

The first live review exposed a disagreement in Qwen's overall assessment. It rejected the short lookup even though independent retrieval verified the extract containing 60. A narrowly scoped regression test now requires a pass for a single lookup excerpt with intact Student citations and a matching independent quote, while retaining the model's rejection in the report. Longer and multi-claim answers continue through the claim and completeness checks.

After deployment, the teacher reviewed the same saved answer again and passed it with a verified quote containing 60. Teacher Center displayed that saved claim and source in a 390 px browser viewport.

**Remaining limit.** Claim segmentation uses generated paragraphs and sentence boundaries. Quote validation proves provenance and checks numerical support. Broader semantic grading, calibration, and council disagreements require a representative labeled benchmark.

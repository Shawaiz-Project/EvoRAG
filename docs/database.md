# PostgreSQL schema

All application tables use database-generated UUID primary keys and timezone-aware `created_at` columns. Foreign keys preserve references to historical records. JSONB `attributes` columns store flexible metadata. Dedicated JSONB columns hold configuration, structured reports, proposals, or metrics where those values are primary data.

| Area | Tables and links |
| --- | --- |
| Identity | `users` have case-insensitively unique emails. `user_roles` joins users to `roles`. |
| Sessions | `auth_sessions` associates expiring, hashed session tokens with users. Sign-out revokes the corresponding row. |
| Knowledge | `sources` own `documents`, which have numbered `document_versions` and ordered `document_chunks`. The optional parent chunk must belong to the same document version. The generated `search_vector` has a GIN index for keyword retrieval. |
| Ingestion | `ingestion_jobs` stores the submitted input, processing lease, attempt count, and result or failure details. |
| Models and embeddings | `model_registry` identifies provider artifacts by kind, name, and version. `embedding_spaces` versions the embedding recipe and distance metric. `chunk_embeddings` stores one vector per chunk and space. |
| RAG and teacher traces | `conversations` own `messages`. `rag_runs` link the question, answer, and versioned `rag_configurations`. `retrieval_results` record chunk IDs, stages, ranks, iterations, and scores. `teacher_evaluations` link distinct primary and teacher execution records. |
| Governance | `user_feedback` links to a run and user. `learning_proposals` can link feedback, a teacher evaluation, and the original and published document versions. `learning_approvals` record the reviewing user and decision. `audit_logs` are append-only for row updates and deletes. |
| Benchmarks | `benchmark_datasets` hold versioned `benchmark_cases` with golden answers and expected teacher verdicts. `benchmark_evidence` links labeled chunks to cases. `benchmark_results` link metrics to a dataset and configuration, with optional per-case and execution references. |
| Experiments | `experiments` record the dataset, configuration snapshot, aggregate metrics, and optional MLflow run. `benchmark_results` link case-level measurements to executed runs and to the experiment through its experiment key. |

## Integrity and transactions

- Content hashes are unique within a logical document. Concurrent writes lock that document before checking duplicate hashes and assigning the next version. Re-uploading old content returns the original version and preserves the current selection. Identical bytes can retain distinct document records across sources. The SHA-256 index supports corpus-wide duplicate lookups.
- A partial unique index permits at most one current version per document. Publishing a version or restoring a prior version changes the current flag in a single transaction. The database protects stored version content, chunk content, and embedding rows from in-place edits.
- A vector row must match both its declared space's dimensions and its actual pgvector dimensions. A space's dimensions must match its registry model. Space IDs distinguish models and recipes with equal dimensions. Retrieval is expected to select a single explicit space. Version-specific approximate nearest-neighbor indexes can be added when configuring those spaces for retrieval.
- Model identity, embedding space identity, retrieval configuration values, and historical content are protected by migration-installed triggers. Create a new version when these need to change. The role records and approval foreign keys provide the persistence boundary for administrator authorization in the learning application.
- Repository methods use caller-owned async sessions, flush changes as needed, and never commit. Use `async with sessions.begin() as session` to commit a complete operation or roll back on an exception. Postgres's default read-committed isolation is used for concurrent ingestion.

## Migrations and reference data

Run from `backend/` with the PostgreSQL settings in the root `.env`.

```sh
uv sync --locked
uv run --locked alembic upgrade head
uv run --locked alembic current
uv run --locked alembic check
uv run --locked python -m app.db.seed
```

`0001` creates the schema and installs pgvector if needed. `0002` adds immutability triggers. `0003` creates the durable ingestion queue. `0004` adds session authentication. `0005` adds the experiment registry. `0006` makes the embedding immutability trigger compatible with PostgreSQL archive restores. The seed command inserts the initial roles, a pinned local embedding artifact, Gemini model registry entries, and the Naive, Advanced, and independently configured Teacher presets. A repeated seed preserves existing versioned settings. Provider access is configured separately from registry entries.

`alembic downgrade base` removes application tables and their data. Reserve this operation for a disposable database or an explicitly planned, backed-up rollback. The pgvector extension is retained because other consumers in the database may use it.

## PostgreSQL integration tests

Enable with `EVORAG_DB_TESTS=1` and run `uv run --locked pytest`. On PowerShell, use `$env:EVORAG_DB_TESTS = '1'`. Each database test creates an empty database with a generated `evorag_test_` name through the `postgres` maintenance database, applies Alembic migrations, and drops the generated database during teardown. The integration tests use the configured PostgreSQL server and credentials. Run them on a local development or dedicated test server with permission to create databases and install pgvector.

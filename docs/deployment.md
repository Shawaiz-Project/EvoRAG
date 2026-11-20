# Local deployment and recovery

The [README](../README.md) covers Compose startup, administrator creation, and model setup. The web container proxies the API and its SSE response on the same origin. PostgreSQL and Redis ports are bound to loopback for development. Within Compose, the app connects by service name, and migrations run before the API and worker start.

## Back up and restore PostgreSQL

Use Python 3.12 and Docker Compose from the project directory. The backup writes a PostgreSQL custom-format archive and requires a new archive path. Restore creates a separate database and loads the schema and data in a single transaction. Use a new lowercase database name for each restore.

```sh
python scripts/database.py backup evorag-backup.dump
python scripts/database.py restore evorag-backup.dump --database evorag_restore
docker compose exec -T postgres psql -U evorag -d evorag_restore -Atqc "SELECT version_num FROM alembic_version"
docker compose exec -T postgres psql -U evorag -d evorag_restore -Atqc "SELECT count(*) FROM document_versions"
```

Replace the database role if you configured a different `POSTGRES_USER`. Keep archives outside the repository with access restricted to the people who can access the database. They contain corpus content, user records, and execution history. The archive covers PostgreSQL. Preserve the app-data volume separately if you need SQLite checkpoints and preserve your MLflow tracking store and local model files separately.

To run the app against a restored database, stop the API, worker, frontend, and migration service. Review the restored data, then point `POSTGRES_DB` at the restored database in `.env` and run Compose startup again. Changing `POSTGRES_DB` configures new connections and changes the health check. On a populated volume, the PostgreSQL entrypoint preserves existing databases.

## Verification and limits

The deployment started on a fresh Compose project with empty PostgreSQL, Redis, and application volumes. The migration container exited successfully at Alembic revision `0006`. PostgreSQL, Redis, FastAPI, and Nginx passed their health checks. The Playwright smoke test exercised authenticated requests from all 14 dashboard pages at 1366 px and 390 px. The containerized browser run streamed a cited answer from the research paper, displayed an independent teacher report, saved feedback, and confirmed that the worker recognized a repeat PDF upload. The backend passed 38 tests with PostgreSQL integration enabled. Ruff, ESLint, Prettier, TypeScript, and the production frontend build passed.

The PostgreSQL archive was restored to a separate database. The restored database reported revision `0006`, five document versions, and 23 embeddings. A verification run used Docker Engine inside WSL. The containers were healthy, and a loopback relay exposed Nginx locally on port 18080 because the WSL Docker port mapping timed out.

- [Retrieval comparison](retrieval.md) records the fixed-corpus benchmark and local embedding setup.
- [Strategy comparison](strategies.md) records the results for all eight pipelines on a research PDF.
- [Evaluation report](evaluation-report.md) records measured results from four configurations and the MLflow run ID. The single labeled question verifies the measurement pipeline. It supports limited conclusions about strategy quality. The backend image includes the MLflow client. Set `MLFLOW_TRACKING_URI` to a tracking server reachable from the container. For optional RAGAS scoring, set `EVORAG_ENABLE_RAGAS=true` and `RAGAS_OLLAMA_MODEL` in `.env` and rebuild.
- The worker extracts and chunks ordinary PDF text. Scanned PDFs require an OCR adapter. Multi-column PDFs may need a layout-aware extractor to preserve reading order.
- After ingestion, run the index command for the selected embedding space. An approved learning publication indexes its new version before making it current.
- Compose defaults to local HTTP with a loopback-bound web port. Enable secure session cookies when terminating HTTPS.
- If Docker Engine inside WSL cannot read Windows-mounted build metadata, build the checkout from a Linux filesystem. A WSL installation with working port forwarding or Docker Desktop can use the loopback URL directly.

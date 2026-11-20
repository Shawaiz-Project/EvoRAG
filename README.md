# EvoRAG

EvoRAG is a research platform for adaptive retrieval-augmented generation. It includes a React dashboard, a FastAPI API, PostgreSQL with pgvector, Redis, and a durable ingestion worker.

## Prerequisites

- Docker Engine with Docker Compose v2. On Windows, start Docker Desktop in Linux container mode.
- Python 3.12 and [uv](https://docs.astral.sh/uv/getting-started/installation/). If Python 3.12 is missing, run `uv python install 3.12`.
- Node.js 22.13+ on the 22.x line, or 24+.

## Start with Docker Compose

Copy `.env.example` to `.env` with `Copy-Item .env.example .env` in PowerShell or `cp .env.example .env` in a POSIX shell. Set `POSTGRES_PASSWORD` to a unique, locally generated password. The root `.env` is ignored by Git. Keep provider keys in backend variables. Values prefixed with `VITE_` are public.

```sh
docker compose up --build -d --wait
docker compose exec api python -m app.auth --email you@example.com
```

Compose waits for PostgreSQL, applies Alembic migrations, seeds the RAG profiles, starts the API and worker, and serves the dashboard at [127.0.0.1:8080](http://127.0.0.1:8080). Use the account created by the second command to sign in. [OpenAPI documentation](http://127.0.0.1:8080/docs)

For local Ollama, run it on an address the containers can reach and pull the models on the host. Docker defaults to `http://host.docker.internal:11434`. Change `OLLAMA_CONTAINER_URL` if your Docker installation needs a different address.

```sh
ollama pull qwen2.5:1.5b
ollama pull all-minilm:latest
docker compose exec api python -m app.rag.ollama_profile --primary qwen2.5:1.5b --teacher qwen2.5:1.5b --extractive
docker compose exec api python -m app.rag.seed_strategies
```

Upload a PDF in **Knowledge Base**. After the worker completes extraction, index the corpus into the Ollama embedding-space version printed by the profile command. Change `1` below if the profile printed a different version.

```sh
docker compose exec api python -m app.retrieval.index --space ollama --version 1
```

Select an Ollama profile in the **RAG Playground**, ask a question, and enable the teacher review to see its independently retrieved evidence. To use Gemini generation, supply a valid `GEMINI_API_KEY` and index into the embedding space of the selected Gemini profile. [Provider and index setup](docs/rag.md)

## Run development servers

Run commands from the repository root unless a step changes directories.

1. Copy `.env.example` to `.env` (`Copy-Item .env.example .env` in PowerShell or `cp .env.example .env` in a POSIX shell). Set `POSTGRES_PASSWORD` to a unique, locally generated password before starting. The root `.env` is ignored by Git. Only public values such as `VITE_API_BASE_URL` belong in variables prefixed with `VITE_`.
2. Start and inspect the backing services.

   ```sh
   docker compose up -d --wait postgres redis
   docker compose ps
   ```

3. Open a terminal for the API.

   ```sh
   cd backend
   uv sync --locked
   uv run --locked alembic upgrade head
   uv run --locked python -m app.db.seed
   uv run --locked uvicorn app.main:create_app --factory --reload
   ```

4. Open another terminal for the web app.

   ```sh
   cd frontend
   npm ci
   npm run dev
   ```

Open [the dashboard](http://127.0.0.1:5173) and [the API documentation](http://127.0.0.1:8000/docs). Use the same host name for both applications so the browser can send the session cookie. If you change the frontend origin or API port, update `CORS_ORIGINS` or `VITE_API_BASE_URL` respectively and restart the corresponding server.

To process knowledge uploads, start `uv run --locked python -m app.ingestion.worker` from `backend/` in another terminal. See [ingestion setup and API examples](docs/ingestion.md).

See [embedding setup and retrieval benchmark](docs/retrieval.md) for indexing, supported model configurations, and measured fixture results.

See [RAG setup](docs/rag.md) for creating a local account, configuring Gemini or Ollama, and querying the indexed corpus.

See [Teacher RAG](docs/teacher.md) for independent reviews, refinement, and the labeled verification cases.

See [strategy workflows](docs/strategies.md) for enabling alternative pipelines and comparing them on one question.

See [controlled learning](docs/learning.md) for feedback, proposal review, publication, and rollback.

See [research evaluation](docs/evaluation.md) for versioned labeled cases, result exports, model tracking, and running experiments.

## Verify

From the root, use `curl.exe` on Windows or `curl` on macOS/Linux.

```sh
docker compose ps
docker compose exec -T postgres psql -U evorag -d evorag -Atqc "SELECT vector_dims(ARRAY[1]::vector)"
docker compose exec -T redis redis-cli ping
curl --fail http://127.0.0.1:8080/health
curl --fail http://127.0.0.1:8080/ready
```

Use the configured `POSTGRES_USER` and `POSTGRES_DB` in the `psql` command if you changed them. For development servers, check port 8000. Expect healthy containers, a vector dimension of `1`, `PONG`, `{"status":"ok"}`, and `{"status":"ready","checks":{"postgres":true,"redis":true}}`. If a backing service is unavailable, `/ready` returns HTTP 503 with the failing check and `/health` still returns HTTP 200.

Run the backend checks from `backend/`.

```sh
uv run --locked pytest
uv run --locked ruff check .
uv run --locked ruff format --check .
```

Set `EVORAG_DB_TESTS=1` to enable the PostgreSQL integration tests, then run `uv run --locked pytest` from `backend/`. The tests require access to the local PostgreSQL server and permission to create disposable test databases and install pgvector. See [database setup and schema](docs/database.md) for migration commands and test isolation.

Run the frontend checks from `frontend/`.

```sh
npm run lint
npm run format:check
npm run build
```

## Stop and troubleshoot

For development, stop the API, worker, and Vite with Ctrl+C. Run `docker compose down` to stop Compose. Named volumes retain PostgreSQL, Redis, and workflow checkpoints.

| Symptom | Check |
| --- | --- |
| Docker daemon unavailable | Start Docker Desktop or your Docker Engine, then check `docker info`. |
| A published port is already in use | Change `WEB_PORT`, `POSTGRES_PORT`, or `REDIS_PORT` in `.env` and run `docker compose up -d --wait`. Update the development API environment as needed. |
| PostgreSQL password changed after first startup | The PostgreSQL image applies initialization settings to empty volumes. Change the password in the existing database before updating `.env`. |
| PostgreSQL readiness fails | Check `docker compose logs postgres`. The pgvector initialization script runs on a fresh volume. For an existing database, a database administrator can run `CREATE EXTENSION IF NOT EXISTS vector;` in the configured database. |
| Browser cannot check the API | Check `VITE_API_BASE_URL`, the API server, and the exact browser origin in the JSON array `CORS_ORIGINS`. |
| Compose exits during startup | Inspect `docker compose logs migrate api worker frontend`. Fix the first failing service and rerun startup. |
| Upload completes without vector results | Run the index command for the selected embedding space and version. Inspect job status and chunk content before indexing. |

When Docker Engine runs directly in WSL and port forwarding fails, run FastAPI in the same distribution as Docker. Set `UV_PROJECT_ENVIRONMENT` to a Linux path to keep its virtual environment separate from the Windows environment. If the published service ports also time out inside WSL, use `docker inspect` to find the PostgreSQL and Redis addresses on the private Compose network. Set `POSTGRES_HOST` and `REDIS_HOST` in the API process environment and look up the addresses again after recreating containers.

See the [architecture diagram](docs/architecture.md) and [end-to-end workflow guide](docs/workflow.md) for module boundaries and operating steps. See [deployment operations](docs/deployment.md) for backup, restore, and known limitations. [Measured benchmark report](docs/evaluation-report.md)

The [EvoRAG 2.0 technical audit](docs/audits/INITIAL_TECHNICAL_AUDIT.md) records the starting point and prioritized roadmap. The [improvement log](docs/audits/IMPROVEMENT_LOG.md) records changes, regression checks, and measured outcomes.

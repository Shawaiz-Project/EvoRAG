# Primary RAG

Apply the database migrations and seed the presets, then create an account from `backend/`.

```sh
uv run --locked alembic upgrade head
uv run --locked python -m app.db.seed
uv run --locked python -m app.auth --email 'you@example.com'
```

The last command prompts for a password without echoing it. The web app uses an HttpOnly session cookie. Local development uses HTTP and the same browser host name for the API and Vite. Set `SESSION_COOKIE_SECURE=true` behind HTTPS.

## Model setup

For Gemini generation, set `GEMINI_API_KEY` in the backend environment or root `.env`. Index the corpus into the default MiniLM space as described in [retrieval setup](retrieval.md). The seeded configurations use separate Gemini generation settings for the primary and teacher profiles.

For local Ollama, start its server, set `OLLAMA_URL` if its loopback port differs, and pull the generation and embedding models. This example uses the same installed model artifact in two independently versioned configurations.

```sh
ollama pull qwen2.5:0.5b
ollama pull all-minilm:latest
uv run --locked python -m app.rag.ollama_profile --primary qwen2.5:0.5b --teacher qwen2.5:0.5b
uv run --locked python -m app.retrieval.index --space ollama --version 1
```

The profile command uses installed Ollama model digests and creates new configuration versions when the model or embedding space changes. It prints the selected embedding space version. Use that version in the index command. Run indexing again after adding or replacing knowledge.

Pass `--extractive` to have the model select a directly quoted answer. This reduces the structured output required from small local models. The API still verifies the source identifier and the selected text.

## Query and inspect

In the Playground, sign in, select a configuration, and ask a question. Naive uses a single vector query. Advanced classifies intent, derives retrieval variants, combines vector and BM25 rankings with reciprocal rank fusion, reranks candidates, and assembles parent context where available. Configure a versioned cross-encoder in the configuration's `reranker` settings or use the built-in lexical coverage reranker.

- `POST /api/v1/rag/query` returns the completed execution as JSON.
- `POST /api/v1/rag/query/stream` returns Server-Sent Events for status, validated answer deltas, and the completed execution. Answer deltas are sent after citation verification.
- `GET /api/v1/rag/runs/{id}` returns the persisted execution metadata and stage-level retrieval ranks.
- `GET /api/v1/conversations/{id}/messages` returns prior turns for the signed-in owner.

The generation prompt carries a bounded evidence payload and recent conversation history. Claims must cite a supplied source identifier with an exact supporting quote from the assembled evidence. Invalid identifiers or quotes force abstention. Citation checking proves that the quoted text was retrieved. Semantic entailment requires independent verification.

# Knowledge ingestion

Run the API and `uv run --locked python -m app.ingestion.worker` from `backend/` after `alembic upgrade head`. The worker pools its own PostgreSQL connections and can run in multiple processes.

The knowledge and ingestion routes require a signed-in session. Use the dashboard or send an HttpOnly session cookie obtained from `POST /api/v1/auth/login`. The commands below use `EVORAG_SESSION_JAR` as the path to a private curl cookie jar. API clients can also send a session from `POST /api/v1/auth/token` in the Authorization header.

```sh
curl -X POST 'http://127.0.0.1:8000/api/v1/ingestion/files?filename=paper.pdf&strategy=recursive' \
  --cookie "$EVORAG_SESSION_JAR" -H 'Content-Type: application/octet-stream' --data-binary '@paper.pdf'
curl -X POST 'http://127.0.0.1:8000/api/v1/ingestion/remote' \
  --cookie "$EVORAG_SESSION_JAR" -H 'Content-Type: application/json' \
  -d '{"kind":"website","url":"https://example.com/","strategy":"sentence"}'
```

On Windows, use `curl.exe`. URL-encode any filename containing spaces or reserved characters. A successful submission returns HTTP 202 and a job ID. Use `GET /api/v1/ingestion/jobs/{id}` to inspect progress. A completed response contains the document and version IDs for each outcome, with a duplicate flag. Use `GET /api/v1/documents/{id}` for history and `GET /api/v1/documents/{id}/versions/{version_id}` for extracted text and paginated chunks.

| Input | Extraction and provenance |
| --- | --- |
| PDF | Page-specific text with a one-based page number. An injectable `PageOCR` interface can supply text for scanned pages. The default worker has no OCR engine configured. |
| DOCX | Paragraphs, heading-derived sections, and table rows labeled by their column headers. |
| TXT and Markdown | UTF-8 text with Markdown heading sections. |
| CSV and XLSX | Rows represented as column–value pairs, with row number and worksheet or table context. XLSX reads cached values. |
| Website | One public HTML, Markdown, or text page, with requested and resolved URLs. HTML extraction excludes scripts and other hidden elements. |
| GitHub | A public repository's default branch, pinned to the resolved commit for ingestion. Provenance records the repository, commit, path, and blob ID. The worker inspects up to 40 allowlisted files per job. |

Uploads default to 25 MiB and remote content to 5 MiB. Configure `MAX_UPLOAD_BYTES` and `MAX_REMOTE_BYTES` in the root `.env`. The API bounds raw uploads while streaming and validates extension-specific signatures. The parsers also limit archive expansion, page counts, extracted text, and table size.

Recursive chunking prefers paragraph, line, and word boundaries. Sentence chunking groups sentences by size. Semantic chunking uses lexical similarity between adjacent sentences to detect topic changes. Larger blocks produce parent chunks and leaf chunks. The ingested version and each chunk preserve provenance and source locations.

The worker claims jobs with PostgreSQL row locks and a time-limited lease. PostgreSQL supplies the clock for queue eligibility, lease expiry, and retry deadlines. An expired claim can be recovered by another worker. Upload payloads are discarded after successful completion and retained after failures for retry. Use `POST /api/v1/ingestion/jobs/{id}/retry` for failed jobs. Re-uploading identical content under the same filename returns the existing version. Changed bytes under that filename create a new version.

Website requests allow only HTTP(S) on standard ports, reject non-public addresses, and validate each redirect. The DNS resolver hands vetted addresses directly to the connector. The client disables environment proxies, cookies, and automatic decompression. GitHub access stays on its official HTTPS API and filters paths, file types, and common credential patterns. Heuristic content filtering cannot establish that arbitrary public code is free of sensitive data.

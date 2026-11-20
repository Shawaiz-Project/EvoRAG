# Research evaluation

Create a dataset version as an administrator using `POST /api/v1/benchmarks/datasets`. A case can record a golden answer, graded relevant chunk IDs, and an expected teacher verdict. To revise the golden answers or labels, create another version of the dataset.

`POST /api/v1/experiments` starts a benchmark over a dataset version and selected variants. The default compares Naive, Advanced, Corrective, and Teacher-guided RAG. Include `without-parent-context` to ablate parent expansion from Advanced. Read experiment history with `GET /api/v1/experiments`, or export the settings and per-case metrics as JSON using `GET /api/v1/experiments/{id}`.

The experiment snapshots the model identities, versions, embedding spaces, retrieval settings, prompts, and observed chunking recipes from labeled documents. Each result links to a persisted execution trace. The local backend records graded precision, recall, MRR, nDCG, context precision and recall, lexical answer relevancy, golden-answer matching, citation support, wall-clock latency, and provider-reported tokens. Unavailable denominators produce null metrics.

Teacher critique precision and recall treat revise or reject as a flagged answer. False approval rate counts human-labeled revise or reject cases that the teacher passed. False rejection rate counts human-labeled pass cases that the teacher rejected.

Local Ollama has zero metered provider cost. Configured registry pricing can supply `input_per_million_usd` and `output_per_million_usd` in the model's `pricing` attributes. Hosted rates remain null until explicitly configured.

## MLflow and RAGAS

Install tracking with `uv sync --locked --extra tracking`. Set `MLFLOW_TRACKING_URI` to a reachable MLflow server or a local SQLite tracking URI. The runner logs the configuration snapshot and raw result data as artifacts, records numeric metrics, and stores the MLflow run ID in the experiment record.

To score semantic faithfulness with RAGAS, install the `ragas` extra and set `RAGAS_OLLAMA_MODEL` to an installed Ollama model name. The adapter runs the RAGAS faithfulness rubric with that model, separately from primary generation. Without a semantic evaluator configured, exact-quote outputs can receive an explicitly labeled `exact_excerpt` faithfulness measurement.

See the [local experimental report](evaluation-report.md) for measured results.

# Local RAG evaluation report

The experiment evaluated one human-labeled question about the fixed damping constant in the two-page RRF research paper. The golden answer was 60, one relevant leaf chunk was labeled, and the cutoff was five. The corpus was chunked by sentence and indexed with Ollama MiniLM embeddings. The answer and teacher configurations used Qwen 2.5 1.5B. Primary answers selected a canonical excerpt. [RRF paper](https://plg.uwaterloo.ca/~gvcormac/cormacksigir09-rrf.pdf)

| Variant | Precision@5 | Recall@5 | MRR@5 | nDCG@5 | Answer correct | Wall time | Generation tokens in / out |
| --- | ---: | ---: | ---: | ---: | --- | ---: | ---: |
| Naive | 0.20 | 1.00 | 0.33 | 0.50 | Yes | 12.85 s | 418 / 8 |
| Advanced | 0.20 | 1.00 | 1.00 | 1.00 | Yes | 6.80 s | 375 / 8 |
| Corrective | 0.20 | 1.00 | 1.00 | 1.00 | Yes | 7.79 s | 489 / 8 |
| Teacher-guided | 0.20 | 1.00 | 1.00 | 1.00 | Yes | 48.22 s | 375 / 8 |

The teacher recorded a pass and used another 874 input and 277 output tokens. Citation support and exact-excerpt faithfulness were 1.00 for the four responses. Lexical answer relevancy was 0.57 for each. Semantic RAGAS scoring was outside this run. The single positive teacher label yields a false rejection rate of 0.00 and leaves false approval and critique precision and recall unscored.

MLflow run `aa22ad2e85e3449cb2344f7e78d42eca` contains the configuration and metrics artifacts. The observed latencies reflect local CPU execution and cache state. This single-case run verifies the measurement pipeline and provides limited evidence about strategy quality.

# Controlled learning

The Playground accepts a rating, comment, or replacement for a cited passage. A correction saves the feedback and creates a pending proposal. The proposal records the target version, original text, proposed replacement, and any associated teacher evaluation. A user may supply an ingested external source that contains the proposed text. The administrator can also inspect repeated failures through the knowledge gap API.

| API | Action |
| --- | --- |
| `POST /api/v1/rag/runs/{run_id}/feedback` | Save feedback on an owned run and optionally open a correction proposal. |
| `GET /api/v1/learning/proposals` | Inspect pending and historical proposals as an administrator. |
| `GET /api/v1/learning/gaps` | List questions with repeated failed or abstained runs. |
| `POST /api/v1/learning/proposals/{id}/verify` | Obtain independent teacher evidence, check for factual conflicts, and evaluate available golden answers linked to the target version. |
| `POST /api/v1/learning/proposals/{id}/decision` | Approve a verified proposal or reject a proposal, with a human review reason. |
| `POST /api/v1/learning/proposals/{id}/publish` | Stage the new version, index it in active embedding spaces, and then make it current with audit records. |
| `POST /api/v1/learning/proposals/{id}/rollback` | Restore the previous current version and retain the published revision and audit history. |

Repeated identical proposals share a record. The replacement must match one passage in the current version. Verified numerical changes and teacher findings are visible to the administrator. The benchmark report includes the number of matched cases. If that count is zero, the human review is the only publication decision supported by the workflow.

Publication requires a persisted administrator approval. Teacher verification produces advisory evidence and cannot take administrative actions. Staged content stays out of normal retrieval until its embeddings are ready and the database switches the current version. A failed indexing attempt can be retried from the approved proposal. Rollback checks that the published version is still current before restoring its predecessor.

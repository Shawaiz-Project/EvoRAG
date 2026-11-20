# Teacher RAG

The `teacher` preset selects its own versioned retrieval settings, model, and evaluation prompt. A teacher review starts a separate execution for the original question, retrieves fresh evidence from the current corpus within the original scope, and checks the primary answer and its stored citations. It saves the independent chunk ranks, evidence overlap, model critique, citation and numerical checks, and final verdict.

New reviews label individual claims as supported, conditional, unsupported, contradicted, or unverified. For evidence-bearing grades the server validates the identifier and quoted text against the teacher's own passages. A numeric claim needs its number present in the supporting quotes. Reports preserve the text, status, and validated chunk provenance under `report.claims`. A report cannot pass when a claim is missing or lacks support. The Playground and Teacher Center display these checks, and older reports remain readable.

The review currently divides generated claims at paragraph and sentence boundaries and processes up to eight segments using bounded independent context. It may leave compound statements together. Validating a quote confirms where the text came from. Semantic judgments still depend on the teacher model and need calibration on a representative labeled dataset.

For a short lookup with a single extractive answer, the teacher can pass an exact cited excerpt that its own retrieval corroborates. The report retains the model's assessment when it differs from this evidence-based decision.

| API | Behavior |
| --- | --- |
| `POST /api/v1/teacher/reviews/{run_id}` | Review a primary run owned by the signed-in user. |
| `GET /api/v1/teacher/reviews` | Read saved evaluations for the signed-in user. |
| `GET /api/v1/teacher/analytics` | Count that user's pass, revise, and reject outcomes. |
| `POST /api/v1/teacher/reviews/{review_id}/refine` | Retry retrieval and generation for an answer marked revise. |

The refinement run preserves the original question and links the evaluation that led to the retry. It can use the teacher's corrective query wording for search. A row lock on the original run enforces `TEACHER_MAX_REFINEMENTS` across concurrent requests. Refined answers use the same evidence and citation gates as primary answers. Teacher operations write execution and evaluation records only.

The PostgreSQL integration test contains three human-labeled cases: a supported numerical answer, an answer with an unsupported numerical value, and a question with an empty evidence scope. Run `EVORAG_DB_TESTS=1 uv run --locked pytest tests/test_teacher.py` from `backend/`. The test uses a controlled response fixture to make the pass, revise, reject, and refinement-limit checks repeatable.

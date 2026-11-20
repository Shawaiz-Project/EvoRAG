from uuid import uuid4

import pytest

from app.evaluation.metrics import retrieval_metrics, teacher_metrics


def test_retrieval_metrics_use_ranked_and_graded_evidence() -> None:
    first, second, unrelated = uuid4(), uuid4(), uuid4()
    metrics = retrieval_metrics([unrelated, second, first], {first: 3, second: 1}, 3)
    assert metrics["precision_at_k"] == pytest.approx(2 / 3)
    assert metrics["recall_at_k"] == 1.0
    assert metrics["mrr_at_k"] == 0.5
    assert 0 < metrics["ndcg_at_k"] < 1
    assert retrieval_metrics([first], {})["recall_at_k"] is None


def test_teacher_metrics_handle_unlabeled_denominators() -> None:
    metrics = teacher_metrics([("pass", "pass"), ("reject", "pass"), ("revise", "revise")])
    assert metrics["teacher_critique_precision"] == 1
    assert metrics["teacher_critique_recall"] == 0.5
    assert metrics["false_approval_rate"] == 0.5
    assert metrics["false_rejection_rate"] == 0.0
    assert teacher_metrics([])["false_approval_rate"] is None

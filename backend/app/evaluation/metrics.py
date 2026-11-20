from math import log2
from uuid import UUID

from app.rag.engine import Answer
from app.rag.evidence import NUMBERS, normalized
from app.rag.strategies import terms


def retrieval_metrics(
    ranks: list[UUID], relevant: dict[UUID, int], k: int = 5
) -> dict[str, float | None]:
    if not relevant:
        return {
            "precision_at_k": None,
            "recall_at_k": None,
            "mrr_at_k": None,
            "ndcg_at_k": None,
            "context_precision": None,
            "context_recall": None,
        }
    grades = [relevant.get(chunk, 0) for chunk in ranks[:k]]
    matched = [index for index, grade in enumerate(grades, 1) if grade > 0]
    ideal = sorted(relevant.values(), reverse=True)[:k]
    dcg = sum((2**grade - 1) / log2(index + 2) for index, grade in enumerate(grades))
    idcg = sum((2**grade - 1) / log2(index + 2) for index, grade in enumerate(ideal))
    recall = len(matched) / len(relevant)
    return {
        "precision_at_k": len(matched) / k,
        "recall_at_k": recall,
        "mrr_at_k": 1 / matched[0] if matched else 0.0,
        "ndcg_at_k": dcg / idcg if idcg else 0.0,
        "context_precision": sum(order / rank for order, rank in enumerate(matched, 1))
        / max(1, min(len(relevant), k)),
        "context_recall": recall,
    }


def answer_metrics(answer: Answer, question: str, golden: str | None) -> dict[str, float | None]:
    words = terms(question)
    text = normalized(answer.answer).casefold()
    correct = None
    if golden:
        expected = normalized(golden).casefold()
        if expected.isnumeric():
            correct = float(expected in NUMBERS.findall(text))
        else:
            correct = float(expected in text)
    return {
        "answer_correctness": correct,
        "answer_relevancy_lexical": len(words & terms(answer.answer)) / max(1, len(words))
        if answer.status == "completed"
        else 0.0,
        "citation_support_rate": float(answer.citations_verified) if answer.citations else None,
    }


def teacher_metrics(labels: list[tuple[str, str]]) -> dict[str, float | None]:
    positives = sum(expected != "pass" for expected, _ in labels)
    flags = sum(actual != "pass" for _, actual in labels)
    correct_flags = sum(expected != "pass" and actual != "pass" for expected, actual in labels)
    approved = sum(expected == "pass" for expected, _ in labels)
    return {
        "teacher_critique_precision": correct_flags / flags if flags else None,
        "teacher_critique_recall": correct_flags / positives if positives else None,
        "false_approval_rate": sum(
            expected != "pass" and actual == "pass" for expected, actual in labels
        )
        / positives
        if positives
        else None,
        "false_rejection_rate": sum(
            expected == "pass" and actual == "reject" for expected, actual in labels
        )
        / approved
        if approved
        else None,
    }

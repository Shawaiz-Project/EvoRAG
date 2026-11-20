import re
from collections import Counter
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from math import sqrt
from typing import Any, Literal

ChunkStrategy = Literal["recursive", "sentence", "semantic"]


@dataclass(frozen=True, slots=True)
class Block:
    text: str
    page: int | None = None
    section: str | None = None
    kind: str = "text"
    attributes: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ChunkDraft:
    ordinal: int
    parent: int | None
    block: Block
    text: str


def recursive(text: str, capacity: int) -> Iterator[str]:
    remaining = text.strip()
    while remaining:
        if len(remaining) <= capacity:
            yield remaining
            return
        end = capacity
        for separator in ("\n\n", "\n", ". ", " "):
            point = remaining.rfind(separator, capacity // 2, capacity)
            if point >= 0:
                end = point + (1 if separator == ". " else 0)
                break
        yield remaining[:end].strip()
        remaining = remaining[end:].strip()


def similarity(left: str, right: str) -> float:
    a = Counter(re.findall(r"\w+", left.casefold()))
    b = Counter(re.findall(r"\w+", right.casefold()))
    denominator = sqrt(
        sum(value * value for value in a.values()) * sum(value * value for value in b.values())
    )
    return sum(value * b[word] for word, value in a.items()) / denominator if denominator else 0


def sentences(text: str, capacity: int, *, semantic: bool) -> Iterator[str]:
    pending: list[str] = []
    length = 0
    for sentence in re.split(r"(?<=[.!?])\s+|\n+", text.strip()):
        for part in recursive(sentence, capacity):
            topic_break = (
                semantic
                and bool(pending)
                and length >= capacity // 3
                and similarity(pending[-1], part) < 0.10
            )
            if pending and (length + len(part) + 1 > capacity or topic_break):
                yield " ".join(pending)
                pending.clear()
                length = 0
            length += len(part) + bool(pending)
            pending.append(part)
    if pending:
        yield " ".join(pending)


def chunk_blocks(
    blocks: Iterable[Block],
    strategy: ChunkStrategy,
    *,
    max_chars: int = 900,
    parent_chars: int = 3000,
) -> Iterator[ChunkDraft]:
    if not 0 < max_chars <= parent_chars:
        raise ValueError("Expected positive child size and parent size at least as large")
    ordinal = 0
    for block in blocks:
        for parent_text in recursive(block.text, parent_chars):
            if block.kind == "table" or strategy == "recursive":
                children = list(recursive(parent_text, max_chars))
            else:
                children = list(sentences(parent_text, max_chars, semantic=strategy == "semantic"))
            if len(children) == 1:
                yield ChunkDraft(ordinal, None, block, children[0])
                ordinal += 1
                continue
            parent = ordinal
            yield ChunkDraft(ordinal, None, block, parent_text)
            ordinal += 1
            for child in children:
                yield ChunkDraft(ordinal, parent, block, child)
                ordinal += 1

import re
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field

from app.rag.evidence import ABSTENTION, MIN_QUOTE_CHARS, NUMBERS, Passage, match_quote

MAX_CLAIMS = 8
type ClaimStatus = Literal["supported", "conditional", "unsupported", "contradicted", "unverified"]


class ProposedQuote(BaseModel):
    identifier: str = Field(pattern=r"^S[1-9][0-9]*$")
    quote: str = Field(min_length=MIN_QUOTE_CHARS, max_length=350)


class ProposedAssessment(BaseModel):
    claim_id: int = Field(ge=1, le=MAX_CLAIMS)
    status: ClaimStatus
    evidence: list[ProposedQuote] = Field(default_factory=list, max_length=2)


class IndependentQuote(BaseModel):
    identifier: str
    quote: str
    chunk_id: UUID
    title: str
    page: int | None


class ClaimReview(BaseModel):
    claim_id: int
    text: str
    status: ClaimStatus
    evidence: list[IndependentQuote]


def answer_claims(answer: str) -> list[str]:
    if not answer or answer == ABSTENTION:
        return []
    text = re.sub(r"\[S[1-9][0-9]*\]", "", answer)
    return [
        claim.strip()
        for claim in re.split(r"\n\s*\n|(?<=[.!?;])\s+(?=[A-Z])", text)
        if claim.strip()
    ]


def verified_quote(
    identifier: str, quote: str, evidence: dict[str, Passage]
) -> IndependentQuote | None:
    passage = evidence.get(identifier)
    if passage is None:
        return None
    matched = match_quote(quote, passage.text)
    if matched is None:
        return None
    return IndependentQuote(
        identifier=identifier,
        quote=matched,
        chunk_id=passage.hit.chunk_id,
        title=passage.hit.title,
        page=passage.hit.page,
    )


def verify_claims(
    claims: list[str],
    evidence: dict[str, Passage],
    proposed: list[ProposedAssessment],
) -> list[ClaimReview]:
    by_id: dict[int, list[ProposedAssessment]] = {}
    for assessment in proposed:
        by_id.setdefault(assessment.claim_id, []).append(assessment)
    reviews = []
    for claim_id, text in enumerate(claims[:MAX_CLAIMS], 1):
        assessments = by_id.get(claim_id, [])
        status: ClaimStatus = "unverified"
        quotes: list[IndependentQuote] = []
        if len(assessments) == 1:
            assessment = assessments[0]
            for item in assessment.evidence:
                verified = verified_quote(item.identifier, item.quote, evidence)
                if verified is not None and verified not in quotes:
                    quotes.append(verified)
            cited_numbers = {number for item in quotes for number in NUMBERS.findall(item.quote)}
            if assessment.status == "unsupported" or (
                quotes
                and (
                    assessment.status != "supported" or set(NUMBERS.findall(text)) <= cited_numbers
                )
            ):
                status = assessment.status
        elif not assessments:
            exact = next(
                (
                    item
                    for identifier in evidence
                    if (item := verified_quote(identifier, text, evidence))
                ),
                None,
            )
            if exact is not None:
                status, quotes = "supported", [exact]
        reviews.append(ClaimReview(claim_id=claim_id, text=text, status=status, evidence=quotes))
    return reviews

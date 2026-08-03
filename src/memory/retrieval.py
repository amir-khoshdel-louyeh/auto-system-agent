"""Phase 7 item [67]: similarity retrieval and top-k ranking (FR-14).

Retrieval is a pure function over stored items: each candidate contributes
its stored embedding, the query is embedded with the same deterministic
model, and results are ranked by cosine similarity with a stable tie-break
(score, then reputation, then id) so the same query always returns the same
order. Nothing here writes or mutates state.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

from .embeddings import cosine, deserialize, embed

#: Default number of candidates a caller sees.
DEFAULT_TOP_K = 5


class Embeddable(Protocol):
    """Anything rankable: an id, a stored embedding and a reputation."""

    @property
    def id(self) -> str: ...  # pragma: no cover - protocol

    @property
    def embedding(self) -> str: ...  # pragma: no cover - protocol

    @property
    def reputation(self) -> float: ...  # pragma: no cover - protocol


@dataclass(frozen=True)
class RetrievedItem:
    """One ranked candidate: the item, its score and why it ranked there."""

    item: Any
    score: float
    rank: int

    @property
    def id(self) -> str:
        return str(getattr(self.item, "id", ""))

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "score": self.score,
            "rank": self.rank,
            "reputation": float(getattr(self.item, "reputation", 0.0)),
            "state": str(getattr(self.item, "state", "")),
            "intent": str(getattr(self.item, "intent", "")),
        }


@dataclass(frozen=True)
class RetrievalResult:
    """Ranked hits plus the query that produced them (for the audit trail)."""

    query: str
    hits: tuple[RetrievedItem, ...] = field(default_factory=tuple)

    @property
    def hit(self) -> bool:
        return bool(self.hits)

    @property
    def best(self) -> RetrievedItem | None:
        return self.hits[0] if self.hits else None

    def as_dict(self) -> dict[str, Any]:
        return {
            "query": self.query,
            "hits": [hit.as_dict() for hit in self.hits],
        }


def score_item(query_vector: list[float], item: Any) -> float:
    """Cosine similarity between the query and one stored item."""
    stored = getattr(item, "embedding", "")
    vector = deserialize(stored) if isinstance(stored, str) else list(stored or [])
    return round(cosine(query_vector, vector), 6)


def rank(
    query: str,
    items: list[Any],
    *,
    top_k: int = DEFAULT_TOP_K,
    min_score: float = 0.0,
) -> RetrievalResult:
    """Rank candidates by similarity, keeping the best ``top_k``."""
    if top_k < 1:
        raise ValueError("top_k must be >= 1")
    query_vector = embed(query)
    scored = [
        (
            score_item(query_vector, item),
            float(getattr(item, "reputation", 0.0)),
            str(getattr(item, "id", "")),
            item,
        )
        for item in items
    ]
    ordered = sorted(
        (entry for entry in scored if entry[0] > min_score),
        key=lambda entry: (-entry[0], -entry[1], entry[2]),
    )[:top_k]
    hits = tuple(
        RetrievedItem(item=item, score=score, rank=index)
        for index, (score, _, _, item) in enumerate(ordered, start=1)
    )
    return RetrievalResult(query=query, hits=hits)

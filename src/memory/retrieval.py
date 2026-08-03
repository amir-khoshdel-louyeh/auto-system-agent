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
from .states import PUBLIC, is_reusable

#: Default number of candidates a caller sees.
DEFAULT_TOP_K = 5

#: Risk ordering for the `max_risk` filter (higher = more dangerous).
RISK_ORDER: dict[str, int] = {"LOW": 0, "MEDIUM": 1, "HIGH": 2, "UNSAFE": 3}


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
class RetrievalFilters:
    """What a caller is willing to reuse (FR-14 filters, item [68]).

    Defaults are the safe ones: only ``PUBLIC`` items, at most ``MEDIUM``
    risk, any reputation above the floor and any operating system.
    """

    states: tuple[str, ...] = (PUBLIC,)
    max_risk: str = "MEDIUM"
    os_name: str = ""
    min_reputation: float = 0.0
    allowed_intents: tuple[str, ...] = ()
    exclude_ids: tuple[str, ...] = ()

    def rejects(self, item: Any) -> str:
        """Empty string when the item passes, else the reason it was dropped."""
        state = str(getattr(item, "state", ""))
        if self.states and state not in self.states:
            return f"state {state or '?'} not in {list(self.states)}"
        if not self.states and not is_reusable(state):
            return f"state {state or '?'} is not reusable"
        risk = str(getattr(item, "risk", "LOW"))
        if RISK_ORDER.get(risk, 3) > RISK_ORDER.get(self.max_risk, 1):
            return f"risk {risk} above {self.max_risk}"
        item_os = str(getattr(item, "os_name", ""))
        if self.os_name and item_os and item_os != self.os_name:
            return f"os {item_os} incompatible with {self.os_name}"
        if float(getattr(item, "reputation", 0.0)) < self.min_reputation:
            return f"reputation below {self.min_reputation}"
        if self.allowed_intents and str(getattr(item, "intent", "")) not in self.allowed_intents:
            return f"intent {getattr(item, 'intent', '?')} not allowed"
        if str(getattr(item, "id", "")) in self.exclude_ids:
            return "excluded by the caller"
        return ""

    def accepts(self, item: Any) -> bool:
        return not self.rejects(item)

    def as_dict(self) -> dict[str, Any]:
        return {
            "states": list(self.states),
            "max_risk": self.max_risk,
            "os_name": self.os_name,
            "min_reputation": self.min_reputation,
            "allowed_intents": list(self.allowed_intents),
            "exclude_ids": list(self.exclude_ids),
        }


@dataclass(frozen=True)
class RetrievalResult:
    """Ranked hits plus the query and the filter bookkeeping."""

    query: str
    hits: tuple[RetrievedItem, ...] = field(default_factory=tuple)
    considered: int = 0
    filtered: int = 0
    rejections: dict[str, int] = field(default_factory=dict)

    @property
    def hit(self) -> bool:
        return bool(self.hits)

    @property
    def best(self) -> RetrievedItem | None:
        return self.hits[0] if self.hits else None

    def as_dict(self) -> dict[str, Any]:
        return {
            "query": self.query,
            "considered": self.considered,
            "filtered": self.filtered,
            "rejections": dict(self.rejections),
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
    """Rank candidates by similarity, keeping the best ``top_k`` (no filters)."""
    return retrieve(query, items, top_k=top_k, min_score=min_score, filters=None)


def retrieve(
    query: str,
    items: list[Any],
    *,
    top_k: int = DEFAULT_TOP_K,
    min_score: float = 0.0,
    filters: RetrievalFilters | None = None,
) -> RetrievalResult:
    """Filter candidates, then rank them by similarity (items [67], [68]).

    Filtering happens *before* scoring so a quarantined or high-risk plan can
    never be presented as a reuse candidate, whatever its similarity.
    """
    active = filters or RetrievalFilters()
    rejections: dict[str, int] = {}
    kept: list[Any] = []
    for item in items:
        reason = active.rejects(item)
        if reason:
            key = reason.split(" ")[0]
            rejections[key] = rejections.get(key, 0) + 1
            continue
        kept.append(item)
    ranked = _score(query, kept, top_k=top_k, min_score=min_score)
    return RetrievalResult(
        query=query,
        hits=ranked.hits,
        considered=len(items),
        filtered=len(items) - len(kept),
        rejections=rejections,
    )


def _score(
    query: str,
    items: list[Any],
    *,
    top_k: int = DEFAULT_TOP_K,
    min_score: float = 0.0,
) -> RetrievalResult:
    """Similarity ranking of already-filtered candidates."""
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

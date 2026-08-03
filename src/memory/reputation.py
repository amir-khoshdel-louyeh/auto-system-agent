"""Phase 7 item [69]: the explainable reputation formula (FR-15).

Proposal §10.7:

    reputation = 100 * (0.7 * successful_reuses / total_reuses
                        + 0.3 * normalized_rating)

New plans start at :data:`START_REPUTATION` (20) and are quarantined. A plan
that has never been reused keeps its starting score — the formula only speaks
about *observed* reuse. Ratings are mean-aggregated and clamped to 0..1, and
the weights are configuration, not magic numbers scattered through the code.
"""

from __future__ import annotations

from dataclasses import dataclass

#: Where a new, unproven plan starts (quarantined).
START_REPUTATION = 20.0
#: Highest rating a user can give.
RATING_MAX = 5.0
#: Formula weights (§10.7) — they must sum to 1.0.
WEIGHT_SUCCESS = 0.7
WEIGHT_RATING = 0.3


def normalized_rating(rating_sum: float, rating_count: int) -> float:
    """Mean rating mapped to 0..1 (0.0 when nobody rated the plan)."""
    if rating_count <= 0:
        return 0.0
    mean = float(rating_sum) / float(rating_count)
    return max(0.0, min(1.0, mean / RATING_MAX))


def success_rate(reuse_count: int, success_count: int) -> float:
    """Successful reuses over total reuses (0.0 when never reused)."""
    if reuse_count <= 0:
        return 0.0
    return max(0.0, min(1.0, float(success_count) / float(reuse_count)))


def reputation_value(
    *,
    reuse_count: int,
    success_count: int,
    rating_sum: float = 0.0,
    rating_count: int = 0,
) -> float:
    """Reputation on a 0..100 scale (START_REPUTATION before any reuse)."""
    if reuse_count <= 0:
        return START_REPUTATION
    score = 100.0 * (
        WEIGHT_SUCCESS * success_rate(reuse_count, success_count)
        + WEIGHT_RATING * normalized_rating(rating_sum, rating_count)
    )
    return round(max(0.0, min(100.0, score)), 2)


@dataclass(frozen=True)
class ReputationState:
    """The counters a memory item keeps, plus its derived reputation."""

    reuse_count: int = 0
    success_count: int = 0
    rating_sum: float = 0.0
    rating_count: int = 0

    @property
    def reputation(self) -> float:
        return reputation_value(
            reuse_count=self.reuse_count,
            success_count=self.success_count,
            rating_sum=self.rating_sum,
            rating_count=self.rating_count,
        )

    def apply(self, *, success: bool, rating: float | None = None) -> ReputationState:
        """Record one reuse episode (optionally with a 0..5 rating)."""
        reuse = self.reuse_count + 1
        successes = self.success_count + (1 if success else 0)
        rating_sum, rating_count = self.rating_sum, self.rating_count
        if rating is not None:
            value = float(rating)
            if 0.0 <= value <= RATING_MAX:
                rating_sum += value
                rating_count += 1
        return ReputationState(
            reuse_count=reuse,
            success_count=successes,
            rating_sum=rating_sum,
            rating_count=rating_count,
        )

    def explain(self) -> str:
        """Human-readable derivation of the current score."""
        return (
            f"{self.reputation:.2f} = 100 * ({WEIGHT_SUCCESS} * "
            f"{self.success_count}/{max(self.reuse_count, 0)} + {WEIGHT_RATING} * "
            f"{normalized_rating(self.rating_sum, self.rating_count):.3f})"
        )


def is_publication_ready(reputation: float, sandbox_proofs: int) -> bool:
    """True when proofs and reputation allow a promotion to PUBLIC."""
    from .states import PUBLICATION_REPUTATION, SANDBOX_PROOFS_REQUIRED

    return sandbox_proofs >= SANDBOX_PROOFS_REQUIRED and reputation >= PUBLICATION_REPUTATION

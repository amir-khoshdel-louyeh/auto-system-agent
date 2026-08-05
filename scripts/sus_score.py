#!/usr/bin/env python3
"""Phase 9 item [87]: score a System Usability Scale (SUS) study.

The client screens are only "usable" if five people say so, and the scoring
has to be arithmetic rather than a feeling. This tool implements the standard
instrument: ten items on a 1–5 scale, odd items are positive (score =
response − 1), even items are negative (score = 5 − response), and the ten
item scores are summed and multiplied by 2.5 to give a 0–100 score per
participant. The study mean is what gets quoted, next to the per-participant
spread — a mean without its spread hides a split study.

SUS is not a percentage: 68 is the accepted "above average" reference, 80.3
is an A (Bangor et al.'s adjective scale).

Usage::

    python scripts/sus_score.py responses.json      # [[4,2,5,1,...], ...]
    python scripts/sus_score.py --template          # print a blank form
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from pathlib import Path
from typing import Any

ITEMS = 10
MIN_RESPONSE = 1
MAX_RESPONSE = 5

#: Benchmark reference points (Bangor et al., 2009).
BENCHMARKS = ((80.3, "A — users like it"), (68.0, "above average"), (0.0, "below average"))


class SusError(ValueError):
    """The response set is not a valid SUS study."""


def validate_responses(responses: list[int]) -> list[int]:
    """Check one participant's ten answers and return them as ints."""
    if len(responses) != ITEMS:
        raise SusError(f"expected {ITEMS} responses, got {len(responses)}")
    cleaned: list[int] = []
    for index, value in enumerate(responses, start=1):
        try:
            numeric = int(value)
        except (TypeError, ValueError) as exc:
            raise SusError(f"item {index}: {value!r} is not a number") from exc
        if not MIN_RESPONSE <= numeric <= MAX_RESPONSE:
            raise SusError(
                f"item {index}: {numeric} outside {MIN_RESPONSE}–{MAX_RESPONSE} "
                "(1 = strongly disagree, 5 = strongly agree)"
            )
        cleaned.append(numeric)
    return cleaned


def participant_score(responses: list[int]) -> float:
    """SUS score (0–100) for one participant."""
    answers = validate_responses(responses)
    positive = sum(value - 1 for value in answers[0::2])  # items 1,3,5,7,9
    negative = sum(MAX_RESPONSE - value for value in answers[1::2])  # 2,4,6,8,10
    return round((positive + negative) * 2.5, 2)


def interpret(mean: float) -> str:
    """Plain-language reading of a study mean."""
    for threshold, label in BENCHMARKS:
        if mean >= threshold:
            return label
    return BENCHMARKS[-1][1]


def summarise(participants: list[list[int]]) -> dict[str, Any]:
    """Per-participant scores plus the study statistics."""
    if not participants:
        raise SusError("no participants in the study")
    scores = [participant_score(responses) for responses in participants]
    mean = round(statistics.fmean(scores), 2)
    spread = round(statistics.stdev(scores), 2) if len(scores) > 1 else 0.0
    return {
        "participants": len(scores),
        "scores": scores,
        "mean": mean,
        "median": round(statistics.median(scores), 2),
        "stdev": spread,
        "min": min(scores),
        "max": max(scores),
        "interpretation": interpret(mean),
        "meets_target": mean >= 68.0,
    }


def load_responses(path: str | Path) -> list[list[int]]:
    """Read a JSON list of per-participant response lists."""
    try:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise SusError(f"cannot read {path}: {exc}") from exc
    if not isinstance(raw, list) or not raw:
        raise SusError("expected a non-empty JSON list of response lists")
    participants: list[list[int]] = []
    for index, entry in enumerate(raw, start=1):
        if not isinstance(entry, list):
            raise SusError(f"participant {index}: expected a list of responses")
        participants.append(validate_responses(entry))
    return participants


def template() -> str:
    """A blank instrument to print for the sessions."""
    lines = ["SUS response template — one list per participant, in this order:"]
    for index, positive in enumerate([True, False] * 5, start=1):
        wording = (
            "I thought the system was easy to use."
            if positive
            else "I found the system unnecessarily complex."
        )
        lines.append(f"  {index:>2}. {wording}    # {'positive' if positive else 'negative'}")
    lines += [
        "",
        "Scale: 1 = strongly disagree, 5 = strongly agree.",
        "",
        "responses.json:",
        "[[5, 1, 4, 2, 5, 1, 4, 2, 5, 1],",
        " [4, 2, 4, 2, 4, 2, 4, 2, 4, 2]]",
    ]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="score a SUS usability study")
    parser.add_argument("responses", nargs="?", help="JSON file with one list per participant")
    parser.add_argument("--template", action="store_true", help="print the blank instrument")
    args = parser.parse_args(argv)

    if args.template:
        print(template())
        return 0
    if not args.responses:
        parser.error("provide a responses file (or --template)")

    try:
        summary = summarise(load_responses(args.responses))
    except SusError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    print(f"participants: {summary['participants']}")
    for index, score in enumerate(summary["scores"], start=1):
        print(f"  P{index}: {score}")
    print(
        f"mean: {summary['mean']} (+/-{summary['stdev']} sd), median {summary['median']},"
        f" range {summary['min']}–{summary['max']}"
    )
    print(
        f"reading: {summary['interpretation']}"
        f" -> {'TARGET MET' if summary['meets_target'] else 'below the 68 target'}"
    )
    return 0 if summary["meets_target"] else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

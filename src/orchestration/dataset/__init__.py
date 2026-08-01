"""Phase 6 item [56]: versioned labeled dataset for the offline planner.

The dataset is data, not code: one JSON object per line with the utterance,
its frozen intent, the slots the extractor should produce and an optional
``split`` (``train`` by default, ``test`` for held-out measurement). The
loader validates every line against the frozen vocabulary, rejects unknown
intents and duplicate utterances, and reports the split sizes — so the
evaluation numbers of item [63] can never be computed from garbage.

A new file ``intents-v<n>.jsonl`` plus a bump of :data:`DATASET_VERSION` is
the only way to change labels, which keeps old measurements reproducible.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..intents import INTENTS, SLOT_TYPES

DATASET_VERSION = "intents-v1"

DATA_DIR = Path(__file__).resolve().parent

TRAIN = "train"
TEST = "test"
SPLITS: tuple[str, ...] = (TRAIN, TEST)


class DatasetError(ValueError):
    """The dataset file is missing, unparseable or outside the vocabulary."""


@dataclass(frozen=True)
class DatasetExample:
    """One labeled utterance."""

    text: str
    intent: str
    slots: dict[str, Any] = field(default_factory=dict)
    split: str = TRAIN
    source: str = DATASET_VERSION


def dataset_path(version: str = DATASET_VERSION) -> Path:
    """Path of the JSONL file backing ``version``."""
    return DATA_DIR / f"{version}.jsonl"


def load_examples(
    version: str = DATASET_VERSION, *, split: str | None = None
) -> list[DatasetExample]:
    """Load and validate the dataset (optionally one split only)."""
    if split is not None and split not in SPLITS:
        raise ValueError(f"split must be one of {SPLITS}, got {split!r}")
    path = dataset_path(version)
    if not path.is_file():
        raise FileNotFoundError(f"unknown dataset version {version!r}: {path} is missing")

    examples: list[DatasetExample] = []
    seen: dict[str, int] = {}
    for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        example = _parse_line(line, path.name, lineno)
        if example.text in seen:
            raise DatasetError(
                f"{path.name}:{lineno}: duplicate utterance also on line {seen[example.text]}"
            )
        seen[example.text] = lineno
        if split is None or example.split == split:
            examples.append(example)
    return examples


def _parse_line(line: str, name: str, lineno: int) -> DatasetExample:
    try:
        raw = json.loads(line)
    except json.JSONDecodeError as exc:
        raise DatasetError(f"{name}:{lineno}: not valid JSON ({exc.msg})") from exc
    if not isinstance(raw, dict):
        raise DatasetError(f"{name}:{lineno}: each line must be a JSON object")

    text = str(raw.get("text", "")).strip()
    intent = str(raw.get("intent", "")).strip()
    slots = raw.get("slots", {})
    split = str(raw.get("split", TRAIN)).strip() or TRAIN

    if not text:
        raise DatasetError(f"{name}:{lineno}: 'text' is required")
    if intent not in INTENTS:
        raise DatasetError(f"{name}:{lineno}: unknown intent {intent!r}")
    if not isinstance(slots, dict):
        raise DatasetError(f"{name}:{lineno}: 'slots' must be an object")
    unknown_slots = set(slots) - set(SLOT_TYPES)
    if unknown_slots:
        raise DatasetError(f"{name}:{lineno}: unknown slot(s) {sorted(unknown_slots)}")
    if split not in SPLITS:
        raise DatasetError(f"{name}:{lineno}: unknown split {split!r}")
    return DatasetExample(text=text, intent=intent, slots=dict(slots), split=split)


def counts(examples: list[DatasetExample]) -> dict[str, int]:
    """Utterances per intent, including intents that never appear."""
    tally = {intent: 0 for intent in INTENTS}
    for example in examples:
        tally[example.intent] += 1
    return tally


def summary(version: str = DATASET_VERSION) -> dict[str, Any]:
    """Dataset version, size, split sizes and per-intent counts."""
    examples = load_examples(version)
    return {
        "version": version,
        "total": len(examples),
        "splits": {split: len(load_examples(version, split=split)) for split in SPLITS},
        "intents": counts(examples),
    }

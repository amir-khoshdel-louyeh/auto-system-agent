# Planner evaluation report

**Item:** Phase 6 — [63] measure precision, recall, F1, latency and fallback rate
**Planner:** `orchestration.offline_planner.OfflinePlanner` (rules + slots, no model)
**Dataset:** `orchestration/dataset/intents-v1.jsonl` (99 labeled utterances, `train`/`test` split)
**Measured:** 2026-10-17, in-process on the development machine (`python -m pytest tests/unit/test_planner_eval.py`)

## How to regenerate

```bash
python - <<'EOF'
import json, sys
sys.path.insert(0, "src")
from orchestration.offline_planner import OfflinePlanner
from orchestration.planner_eval import evaluate
for split in ("train", "test"):
    print(split, json.dumps(evaluate(OfflinePlanner(), split=split).as_dict(), indent=2))
EOF
```

The same numbers back the regression floors in `tests/unit/test_planner_eval.py`
(accuracy ≥ 0.95, macro-F1 ≥ 0.85, slot accuracy ≥ 0.85, fallback rate ≤ 0.25,
average latency < 20 ms), so a rule change that lowers quality fails the suite.

## Aggregate results

| Metric | train (81) | test (18, held out) |
|---|---:|---:|
| Intent accuracy | 1.000 | 1.000 |
| Macro F1 | 1.000 | 1.000 |
| Slot accuracy (labeled slots) | 1.000 (78 labels) | 1.000 (17 labels) |
| Average latency | 0.037 ms | 0.020 ms |
| p95 latency | 0.140 ms | 0.030 ms |
| Fallback rate (clarification/LLM) | 0.099 | 0.111 |
| LLM consultation rate | 0.000 | 0.000 |

## Per-intent results (held-out split)

| Intent | Precision | Recall | F1 | Support |
|---|---:|---:|---:|---:|
| create_folder | 1.00 | 1.00 | 1.00 | 2 |
| write_file | 1.00 | 1.00 | 1.00 | 2 |
| create_workspace | 1.00 | 1.00 | 1.00 | 2 |
| install_app | 1.00 | 1.00 | 1.00 | 2 |
| run_command | 1.00 | 1.00 | 1.00 | 2 |
| system_info | 1.00 | 1.00 | 1.00 | 3 |
| list_directory | 1.00 | 1.00 | 1.00 | 2 |
| help | 1.00 | 1.00 | 1.00 | 1 |
| unknown | 1.00 | 1.00 | 1.00 | 2 |

## Reading the numbers honestly

1. **The dataset is small and project-authored.** A held-out split exists and
   the rules were written from generic verb/object patterns rather than from
   the test lines, but 18 held-out utterances cannot prove generalization to
   arbitrary phrasing. Every unsupported request is answered with a
   clarification question instead of a guessed command, which is why the
   fallback rate is non-zero (the two held-out `unknown` utterances).
2. **No model is in the loop for these numbers.** `llm_rate` is 0.000 because
   the adapter is optional and not configured in CI; when it is enabled it is
   consulted only for requests the offline planner already marked as unclear
   (`PlannerService`), and its answer passes the same schema, validation and
   policy path.
3. **Latency is single-call, in-process planner latency** (normalize → rule →
   slots → steps). It excludes API, database and worker time; the API p95
   measurement belongs to Phase 9 (item [86]).

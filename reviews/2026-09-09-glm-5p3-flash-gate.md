# glm-5p3-flash — live multi-step gate through the Fireworks transport (2026-09-09)

**Command:** `HARNESS_JOB=harness-gate python3 -m harness --gate glm-5p3-flash --provider fireworks --trials 3`
(the `--provider` flag and `gate(transport=)` seam were added the same day — before this, the gate could only reach the .21 node.)

**Verdict: PASS 3/3.** Scored from filesystem state only (`harness/gate.py:score`), never from the model's prose.

| trial | wall | turns | tools used | data.txt | count.txt faithful to wc -l |
|---|---|---|---|---|---|
| 1 | 10.0 s | 4 | write_file, run_bash, read_file, read_file | ✅ | ✅ (3) |
| 2 | 8.1 s | 5 | write_file, list_dir, run_bash, write_file, run_bash | ✅ | ✅ (3) |
| 3 | 14.9 s | 5 | write_file, run_bash, run_bash, write_file, run_bash | ✅ | ✅ (3) |

**Ledger (the other half of the proof):** `observability/data/fireworks_usage.jsonl` went from 12 to 26 lines — 14 lines, all `job=harness-gate`, all `model_served=glm-5p3-flash`. Totals: 11,150 in / 1,620 out / 925 reasoning tokens ≈ **$0.0025** at $0.15/$0.50. Before this change a call through `harness/fireworks_transport.py` wrote nothing here (open-models panel, GPT arm's finding).

**What it does not prove:** the gate is the 3-step write→derive→write chain, not `shootout.sh`'s 5-step chain; three trials bound the reliability claim loosely (Wilson-LB for 3/3 is ~0.44). It is enough to promote the *Fireworks default* (a pin inside a propose-only cash rung — nothing auto-routes here); it is not evidence for `merit_policy` adoption, which needs `tier-eval/merit_eval.py` trials against the Claude incumbent.

**Data class:** `internal` (synthetic task, third-party lane). **Raw verdict JSON follows.**

```json
{
  "model": "glm-5p3-flash",
  "passes": 3,
  "trials": 3,
  "verdict": "PASS",
  "results": [
    {
      "trial": 1,
      "pass": true,
      "wall_s": 10.0,
      "turns": 4,
      "tools": [
        "write_file",
        "run_bash",
        "read_file",
        "read_file"
      ],
      "checks": {
        "data.txt content": true,
        "count.txt faithful to wc -l": true,
        "detail": {
          "count_raw": "3",
          "wc_expected": "3"
        }
      },
      "answer": "All steps are complete:\n\n1. **`data.txt`** created containing exactly:\n   ```\n  "
    },
    {
      "trial": 2,
      "pass": true,
      "wall_s": 8.1,
      "turns": 5,
      "tools": [
        "write_file",
        "list_dir",
        "run_bash",
        "write_file",
        "run_bash"
      ],
      "checks": {
        "data.txt content": true,
        "count.txt faithful to wc -l": true,
        "detail": {
          "count_raw": "3",
          "wc_expected": "3"
        }
      },
      "answer": "All steps completed successfully:\n\n1. **`data.txt`** created containing exactly "
    },
    {
      "trial": 3,
      "pass": true,
      "wall_s": 14.9,
      "turns": 5,
      "tools": [
        "write_file",
        "run_bash",
        "run_bash",
        "write_file",
        "run_bash"
      ],
      "checks": {
        "data.txt content": true,
        "count.txt faithful to wc -l": true,
        "detail": {
          "count_raw": "3",
          "wc_expected": "3"
        }
      },
      "answer": "All steps are complete:\n\n1. \u2705 **data.txt** created with exactly three lines:\n   "
    }
  ],
  "provider": "fireworks"
}
```

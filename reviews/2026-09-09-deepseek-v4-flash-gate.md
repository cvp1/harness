# deepseek-v4-flash — live multi-step gate through the DeepSeek-direct transport (2026-09-09)

**Command:** `HARNESS_JOB=harness-gate python3 -m harness --gate deepseek-v4-flash --provider deepseek --trials 3`
(`harness/deepseek_transport.py`, the `deepseek` ACP provider and Corral lane were built the same day — open-models panel step 5, Gemini's arm.)

**Verdict: PASS 3/3**, scored from filesystem state only.

| trial | wall | turns | tools used |
|---|---|---|---|
| 1 | 9.1 s | 6 | write_file, run_bash, write_file, run_bash, write_file, run_bash |
| 2 | 5.9 s | 5 | write_file, run_bash, run_bash, write_file, read_file |
| 3 | 11.5 s | 8 | write_file, run_bash ×5, read_file ×2 |

**Ledger:** `observability/data/deepseek_usage.jsonl` 782 → 801 lines — 19 lines, all `job=harness-gate`, `model_served=deepseek-v4-flash`; 17,740 in / 1,786 out ≈ **$0.0030** at DeepSeek-direct $0.14/$0.28 (the same tokens via Fireworks' $0.22/$0.66 row: ≈$0.0051). Thinking was disabled on every turn (`think=False` → `{"thinking":{"type":"disabled"}}`); the one-call ping answered "OK" in exactly 1 output token, so no CoT leaked into the bill.

**What it does not prove:** same limits as the Flash gate — 3-step chain, three trials, a pin inside a propose-only cash rung; not `merit_policy` adoption evidence. DeepSeek's `/models` publishes no tool flag, so the picker's `tools: True` rests on this gate, not the vendor's word.

**Data class:** `internal` (third party, PRC-hosted). Raw verdict JSON follows.

```json
{
  "model": "deepseek-v4-flash",
  "passes": 3,
  "trials": 3,
  "verdict": "PASS",
  "results": [
    {
      "trial": 1,
      "pass": true,
      "wall_s": 9.1,
      "turns": 6,
      "tools": [
        "write_file",
        "run_bash",
        "write_file",
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
      "answer": "All steps are complete:\n\n1. **data.txt** created containing exactly these three "
    },
    {
      "trial": 2,
      "pass": true,
      "wall_s": 5.9,
      "turns": 5,
      "tools": [
        "write_file",
        "run_bash",
        "run_bash",
        "write_file",
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
      "answer": "All steps completed successfully:\n\n1. **data.txt** created with exactly three li"
    },
    {
      "trial": 3,
      "pass": true,
      "wall_s": 11.5,
      "turns": 8,
      "tools": [
        "write_file",
        "run_bash",
        "run_bash",
        "run_bash",
        "run_bash",
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
      "answer": "All steps are complete:\n\n1. **data.txt** created with exactly three lines: `alph"
    }
  ],
  "provider": "deepseek"
}
```

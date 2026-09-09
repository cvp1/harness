# Fireworks open-model shootout — same gate, same day, four models (2026-09-09)

**Why:** Craig asked why GLM-5.3-flash was picked over the other open models the panel named (`ollama-tools/reviews/2026-09-09-open-models-panel.md`). The panel's picks were web-read benchmarks; this is the fleet's own measurement. Craig: "Do it."

**Command (x4, in parallel):** `HARNESS_JOB=harness-gate python3 -m harness --gate <model> --provider fireworks --trials 3 --json`  — 3-step write→derive→write chain, scored from filesystem state only (`harness/gate.py:score`). Data class `internal` (synthetic task, third-party lane).

## Result: all four PASS 3/3. Flash is the cheapest by 3x and the second fastest.

| model | verdict | wall (3 trials) | turns | reasoning tokens | cached in | cost (rate card 2026-09-09) | $/gate vs Flash |
|---|---|---|---|---|---|---|---|
| glm-5p3-flash | PASS 3/3 | 33.2 s | 5/4/5 | 798 of 1,444 out | 0 | $0.0023 @ $0.15/$0.50 | 1.0x |
| minimax-m3 | PASS 3/3 | 18.4 s | 4/6/5 | 697 of 1,558 out | 8,640 | $0.0063 @ $0.30/$1.20 | 2.7x |
| kimi-k3 | PASS 3/3 | 75.8 s | 3/3/5 | 370 of 1,231 out | 4,837 | $0.0406 @ $3/$15 | 18x |
| glm-5p3 | PASS 3/3 | 67.8 s | 5/4/3 | 4,376 of 5,113 out | 8,354 | $0.0499 @ $1.40/$4.40 | 22x |

## Read-outs

- **Capability does not separate them at this task's difficulty.** Twelve trials, twelve passes. The gate is fitness evidence, not a ranking — Wilson-LB for 3/3 is ~0.44 each. A harder gate (the 5-step `shootout.sh` chain, or an error-recovery trial) is what would separate them.
- **GLM-5.3 full spends 86% of its output on reasoning** (4,376 of 5,113 tokens) for the same outcome Flash reaches with 798. Consistent with Z.ai's card: omitted reasoning effort defaults to `max`. Neither GLM run was probed at `low`; that lever is still untested.
- **MiniMax M3 is the fastest** (18 s for three trials) and the ledger caught two calls that billed 41–50 output tokens for ~1 visible — unreported chain-of-thought, paid for but not counted. Its cost above is the biller's count, so it is right; the warning means its *reasoning share* is understated.
- **Kimi K3 is the slowest and dearest per token**; on a 3-step task its 1M context and reasoning depth buy nothing. Stays 'rare, on a demonstrated win' as the panel said.
- **Prompt cache hit for three of four** (cached-in tokens above) — Flash reported zero cached tokens on this run and on this morning's; unknown whether Flash is served without cache or the field is not populated. Worth one probe before the cache-discount assumption in any Flash cost projection is trusted.

## Verdict for the blend

Flash keeps the Fireworks default: same pass rate, one third the cost of the next-cheapest, and countable reasoning. MiniMax M3 is the fastest and is the natural second (long-context, 500k). GLM-5.3 full and Kimi K3 are escalation rungs that this gate cannot justify — a caller names them on purpose. Nothing here is merit-adoption evidence; that needs `tier-eval/merit_eval.py` trials against the Claude incumbent.

**Ledger:** `observability/data/fireworks_usage.jsonl` 26 → 78 lines, all `job=harness-gate`. Total spend for the shootout ≈ $0.10.

## Raw verdict JSON

### glm-5p3-flash
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
   "wall_s": 13.3,
   "turns": 5,
   "tools": [
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
   "answer": "All steps are complete:\n\n1. **`data.txt`** created with exactly three lines: `al"
  },
  {
   "trial": 2,
   "pass": true,
   "wall_s": 10.6,
   "turns": 4,
   "tools": [
    "write_file",
    "run_bash",
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
   "answer": "All steps completed:\n\n1. **data.txt** created with exactly three lines:\n   ```\n "
  },
  {
   "trial": 3,
   "pass": true,
   "wall_s": 9.3,
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
   "answer": "All steps are complete:\n\n1. **data.txt** was created with exactly the three line"
  }
 ],
 "provider": "fireworks"
}
```

### glm-5p3
```json
{
 "model": "glm-5p3",
 "passes": 3,
 "trials": 3,
 "verdict": "PASS",
 "results": [
  {
   "trial": 1,
   "pass": true,
   "wall_s": 7.7,
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
   "answer": "All steps are complete:\n\n1. \u2705 Created `data.txt` containing exactly the three li"
  },
  {
   "trial": 2,
   "pass": true,
   "wall_s": 55.2,
   "turns": 4,
   "tools": [
    "write_file",
    "list_dir",
    "run_bash",
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
   "answer": "All steps are complete:\n\n1. **`data.txt`** \u2014 created with exactly the three line"
  },
  {
   "trial": 3,
   "pass": true,
   "wall_s": 4.9,
   "turns": 3,
   "tools": [
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
   "answer": "**Step 3: Final answer**\n\ndata.txt contains **3 lines** (derived via `wc -l`, wh"
  }
 ],
 "provider": "fireworks"
}
```

### kimi-k3
```json
{
 "model": "kimi-k3",
 "passes": 3,
 "trials": 3,
 "verdict": "PASS",
 "results": [
  {
   "trial": 1,
   "pass": true,
   "wall_s": 14.3,
   "turns": 3,
   "tools": [
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
   "answer": "**Step 3: Final answer**\n\nAll steps completed:\n1. \u2705 Created `data.txt` with the "
  },
  {
   "trial": 2,
   "pass": true,
   "wall_s": 21.1,
   "turns": 3,
   "tools": [
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
   "answer": "All steps completed:\n\n1. **Created `data.txt`** with exactly three lines: `alpha"
  },
  {
   "trial": 3,
   "pass": true,
   "wall_s": 40.4,
   "turns": 5,
   "tools": [
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
   "answer": "All steps completed successfully:\n\n1. \u2705 Created **data.txt** containing exactly:"
  }
 ],
 "provider": "fireworks"
}
```

### minimax-m3
```json
{
 "model": "minimax-m3",
 "passes": 3,
 "trials": 3,
 "verdict": "PASS",
 "results": [
  {
   "trial": 1,
   "pass": true,
   "wall_s": 4.6,
   "turns": 4,
   "tools": [
    "write_file",
    "run_bash",
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
   "answer": "The line count is **3**."
  },
  {
   "trial": 2,
   "pass": true,
   "wall_s": 7.3,
   "turns": 6,
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
   "answer": "**Step 3: Final answer**\n\nAll three steps are complete:\n\n- \u2705 `data.txt` contains"
  },
  {
   "trial": 3,
   "pass": true,
   "wall_s": 6.7,
   "turns": 5,
   "tools": [
    "write_file",
    "run_bash",
    "run_bash",
    "read_file",
    "write_file",
    "read_file",
    "list_dir"
   ],
   "checks": {
    "data.txt content": true,
    "count.txt faithful to wc -l": true,
    "detail": {
     "count_raw": "3",
     "wc_expected": "3"
    }
   },
   "answer": "**Step 3: Final answer**\n\nThe file `data.txt` contains **3 lines** (derived via "
  }
 ],
 "provider": "fireworks"
}
```

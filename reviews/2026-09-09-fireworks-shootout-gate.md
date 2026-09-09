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

## Round 2 — harder gate: 5 steps with an injected failure (same day, Craig: "Run that test")

`hard_gate.py` (scratch, reproduced below): read a seeded input file → derive a sum with bash → write a CSV → run a seeded `check.py` that FAILS the first time (missing manifest, with the fix stated in its error text) → fix and re-run until `CHECK=PASS` → final answer. Scored from disk: sum, CSV rows, manifest recovered, `CHECK=PASS` written by the checker itself. `max_turns=14`, `job=hard-gate`.

| model | verdict | wall (3 trials) | turns | reasoning tokens | cost |
|---|---|---|---|---|---|
| minimax-m3 | PASS 3/3 | 22.5 s | 6/7/6 | 488 of 1,638 out | $0.0087 |
| glm-5p3-flash | PASS 3/3 | 44.1 s | 6/7/5 | 894 of 2,424 out | $0.0040 |
| glm-5p3 | PASS 3/3 | 104.6 s | 6/8/6 | 1,158 of 2,857 out | $0.0458 |
| kimi-k3 | PASS 3/3 | 64.5 s | 7/7/7 | 195 of 2,150 out | $0.0909 |

**All four recovered from the injected failure 3/3.** Twenty-four trials across both gates, twenty-four passes: this task family does not separate these models on capability; it separates them on cost and latency only. MiniMax M3 is fastest on both gates; Flash is cheapest on both.

**Cache probe (job=cache-probe):** an identical ~2.9K-token prompt sent twice — Flash reported 2,048 cached of 2,903 on both calls (2K-block granularity), GLM-5.3 0 then 2,902, MiniMax 0 then 3,010. The field IS populated for Flash; this morning's zeros were prompts under the 2K block. Fireworks' $0.03/M cached-input rate for Flash therefore applies to real traffic.

### hard_gate.py
```python
"""Hard gate: 5 steps + an injected failure the model must recover from. Scored from disk only."""
import json, os, shutil, sys, tempfile, time
sys.path.insert(0, os.path.expanduser('~/Github/CC'))
from harness import loop, tools_local, fireworks_transport

CHECK = r'''#!/usr/bin/env python3
import json, os, sys
d = os.path.dirname(os.path.abspath(__file__)); out = os.path.join(d, "out")
def fail(m): print("CHECK=FAIL: " + m); sys.exit(1)
if not os.path.isfile(os.path.join(out, "sum.txt")): fail("out/sum.txt missing")
if not os.path.isfile(os.path.join(out, "data.csv")): fail("out/data.csv missing")
mf = os.path.join(out, "manifest.json")
if not os.path.isfile(mf): fail("out/manifest.json missing — it must be a JSON object {\"files\": [...]} listing every file in out/ except itself, then re-run this check")
try: files = set(json.load(open(mf))["files"])
except Exception as e: fail("manifest unreadable: %s" % e)
want = {f for f in os.listdir(out) if f not in ("manifest.json", "CHECK")}
if files != want: fail("manifest lists %s but out/ has %s" % (sorted(files), sorted(want)))
open(os.path.join(out, "CHECK"), "w").write("PASS\n"); print("CHECK=PASS")
'''
TASK = """\
Complete ALL of these steps using the tools, in order:
1. Read notes/input.txt (one integer per line).
2. Create out/sum.txt containing only the sum of those integers. Derive it with run_bash (awk or python), do not guess.
3. Create out/data.csv with the header line `n,sq` and then one line per input integer with the integer and its square.
4. Run `python3 check.py` with run_bash. If it prints CHECK=FAIL, fix whatever it says and run it again until it prints CHECK=PASS.
5. Reply with a final answer stating the sum.
"""

def seed(wd):
    os.makedirs(os.path.join(wd, "notes")); open(os.path.join(wd, "notes/input.txt"), "w").write("10\n20\n30\n40\n")
    open(os.path.join(wd, "check.py"), "w").write(CHECK)

def score(wd):
    o = os.path.join(wd, "out"); c = {}
    try: c["sum.txt == 100"] = open(os.path.join(o, "sum.txt")).read().strip() == "100"
    except Exception: c["sum.txt == 100"] = False
    try:
        rows = [l.strip() for l in open(os.path.join(o, "data.csv")) if l.strip()]
        c["data.csv correct"] = rows[0].replace(" ", "") == "n,sq" and [r.replace(" ", "") for r in rows[1:]] == ["10,100", "20,400", "30,900", "40,1600"]
    except Exception: c["data.csv correct"] = False
    try:
        c["manifest recovered"] = set(json.load(open(os.path.join(o, "manifest.json")))["files"]) == {"sum.txt", "data.csv"}
    except Exception: c["manifest recovered"] = False
    try: c["CHECK=PASS on disk"] = open(os.path.join(o, "CHECK")).read().strip() == "PASS"
    except Exception: c["CHECK=PASS on disk"] = False
    return all(c.values()), c

model = sys.argv[1]; trials = int(sys.argv[2]) if len(sys.argv) > 2 else 3
res = []
for i in range(trials):
    wd = tempfile.mkdtemp(prefix="hard-gate-"); seed(wd); t0 = time.monotonic()
    try:
        ans, meta = loop.run(TASK, tools_local.standard_tools(wd), model=model,
                             system="You are a capable agent working inside %s. Follow the task exactly." % wd,
                             max_turns=14, transport=fireworks_transport.transport)
        ok, checks = score(wd)
        res.append({"trial": i+1, "pass": ok, "wall_s": round(time.monotonic()-t0, 1), "turns": meta["turns"],
                    "tools": [c for c, _ in meta["tool_calls"]], "checks": checks, "answer": (ans or "")[:80]})
    except Exception as e:
        res.append({"trial": i+1, "pass": False, "wall_s": round(time.monotonic()-t0, 1), "error": str(e)[:160]})
    finally: shutil.rmtree(wd, ignore_errors=True)
p = sum(r["pass"] for r in res)
print(json.dumps({"model": model, "passes": p, "trials": trials, "verdict": "PASS" if p == trials else ("MARGINAL" if p else "FAIL"), "results": res}, indent=1))

```

### Raw round-2 JSON

#### glm-5p3-flash
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
   "wall_s": 14.4,
   "turns": 6,
   "tools": [
    "list_dir",
    "read_file",
    "read_file",
    "run_bash",
    "run_bash",
    "run_bash",
    "run_bash"
   ],
   "checks": {
    "sum.txt == 100": true,
    "data.csv correct": true,
    "manifest recovered": true,
    "CHECK=PASS on disk": true
   },
   "answer": "**Final answer: The sum of the integers in notes/input.txt (10 + 20 + 30 + 40) i"
  },
  {
   "trial": 2,
   "pass": true,
   "wall_s": 13.7,
   "turns": 7,
   "tools": [
    "read_file",
    "list_dir",
    "read_file",
    "run_bash",
    "run_bash",
    "write_file",
    "run_bash",
    "read_file"
   ],
   "checks": {
    "sum.txt == 100": true,
    "data.csv correct": true,
    "manifest recovered": true,
    "CHECK=PASS on disk": true
   },
   "answer": "All steps are complete and `check.py` prints **CHECK=PASS**.\n\n**Final answer: Th"
  },
  {
   "trial": 3,
   "pass": true,
   "wall_s": 16.0,
   "turns": 5,
   "tools": [
    "read_file",
    "list_dir",
    "read_file",
    "run_bash",
    "run_bash",
    "run_bash"
   ],
   "checks": {
    "sum.txt == 100": true,
    "data.csv correct": true,
    "manifest recovered": true,
    "CHECK=PASS on disk": true
   },
   "answer": "All steps completed successfully:\n\n1. \u2705 Read `notes/input.txt` \u2014 contained the i"
  }
 ]
}
```

#### glm-5p3
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
   "wall_s": 31.4,
   "turns": 6,
   "tools": [
    "read_file",
    "list_dir",
    "read_file",
    "run_bash",
    "run_bash",
    "run_bash",
    "run_bash"
   ],
   "checks": {
    "sum.txt == 100": true,
    "data.csv correct": true,
    "manifest recovered": true,
    "CHECK=PASS on disk": true
   },
   "answer": "CHECK=PASS \u2014 all steps completed successfully.\n\n**Final answer: the sum is 100.*"
  },
  {
   "trial": 2,
   "pass": true,
   "wall_s": 45.8,
   "turns": 8,
   "tools": [
    "read_file",
    "list_dir",
    "read_file",
    "run_bash",
    "run_bash",
    "run_bash",
    "run_bash",
    "run_bash",
    "run_bash"
   ],
   "checks": {
    "sum.txt == 100": true,
    "data.csv correct": true,
    "manifest recovered": true,
    "CHECK=PASS on disk": true
   },
   "answer": "All steps are complete. Here's a summary of what was done:\n\n1. **Read** `notes/i"
  },
  {
   "trial": 3,
   "pass": true,
   "wall_s": 27.4,
   "turns": 6,
   "tools": [
    "read_file",
    "list_dir",
    "read_file",
    "run_bash",
    "run_bash",
    "run_bash",
    "run_bash"
   ],
   "checks": {
    "sum.txt == 100": true,
    "data.csv correct": true,
    "manifest recovered": true,
    "CHECK=PASS on disk": true
   },
   "answer": "All steps completed successfully:\n\n1. **Read** `notes/input.txt` \u2014 integers: 10,"
  }
 ]
}
```

#### kimi-k3
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
   "wall_s": 25.4,
   "turns": 7,
   "tools": [
    "read_file",
    "list_dir",
    "run_bash",
    "run_bash",
    "run_bash",
    "run_bash",
    "run_bash"
   ],
   "checks": {
    "sum.txt == 100": true,
    "data.csv correct": true,
    "manifest recovered": true,
    "CHECK=PASS on disk": true
   },
   "answer": "All steps completed successfully:\n\n1. **Read** `notes/input.txt` \u2014 contained int"
  },
  {
   "trial": 2,
   "pass": true,
   "wall_s": 18.0,
   "turns": 7,
   "tools": [
    "read_file",
    "run_bash",
    "run_bash",
    "run_bash",
    "run_bash",
    "run_bash"
   ],
   "checks": {
    "sum.txt == 100": true,
    "data.csv correct": true,
    "manifest recovered": true,
    "CHECK=PASS on disk": true
   },
   "answer": "All steps completed successfully:\n\n1. **Read notes/input.txt** \u2014 contained integ"
  },
  {
   "trial": 3,
   "pass": true,
   "wall_s": 21.1,
   "turns": 7,
   "tools": [
    "read_file",
    "list_dir",
    "run_bash",
    "run_bash",
    "run_bash",
    "run_bash",
    "run_bash"
   ],
   "checks": {
    "sum.txt == 100": true,
    "data.csv correct": true,
    "manifest recovered": true,
    "CHECK=PASS on disk": true
   },
   "answer": "All steps are complete:\n\n1. **Read input**: `notes/input.txt` contained 10, 20, "
  }
 ]
}
```

#### minimax-m3
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
   "wall_s": 6.9,
   "turns": 6,
   "tools": [
    "read_file",
    "list_dir",
    "read_file",
    "run_bash",
    "run_bash",
    "run_bash",
    "run_bash"
   ],
   "checks": {
    "sum.txt == 100": true,
    "data.csv correct": true,
    "manifest recovered": true,
    "CHECK=PASS on disk": true
   },
   "answer": "The sum of the integers in `notes/input.txt` (10, 20, 30, 40) is **100**."
  },
  {
   "trial": 2,
   "pass": true,
   "wall_s": 8.0,
   "turns": 7,
   "tools": [
    "list_dir",
    "read_file",
    "run_bash",
    "run_bash",
    "run_bash",
    "write_file",
    "run_bash",
    "run_bash",
    "run_bash"
   ],
   "checks": {
    "sum.txt == 100": true,
    "data.csv correct": true,
    "manifest recovered": true,
    "CHECK=PASS on disk": true
   },
   "answer": "The sum of the integers in `notes/input.txt` is **100**."
  },
  {
   "trial": 3,
   "pass": true,
   "wall_s": 7.6,
   "turns": 6,
   "tools": [
    "read_file",
    "list_dir",
    "read_file",
    "run_bash",
    "write_file",
    "write_file",
    "write_file",
    "run_bash"
   ],
   "checks": {
    "sum.txt == 100": true,
    "data.csv correct": true,
    "manifest recovered": true,
    "CHECK=PASS on disk": true
   },
   "answer": "The sum of the integers in `notes/input.txt` (10, 20, 30, 40) is **100**.\n\n- `ou"
  }
 ]
}
```

"""gate — multi-step agentic fitness gate run through the harness loop.

Scores a bounded write → mkdir+write → bash-derive → write-derived chain on
filesystem state only; the model's prose is never trusted as evidence.

Usage:  python3 -m harness --gate MODEL [--trials 3]
"""
import shutil
import tempfile
import time
from pathlib import Path

try:
    from . import loop, policy, tools_local
except ImportError:
    import loop, policy, tools_local  # noqa: F401

# The model under test is untrusted: bash is limited to the wc -l shapes the task needs.
_GATE = policy.gate({"run_bash": policy.bash_policy(
    [r"wc -l [\w./-]+", r"cat [\w./-]+ \| wc -l", r"wc -l < [\w./-]+"],
    hint="derive the count with wc -l on the file", shell_syntax=True)})

TRIALS = 3
TASK = """\
Complete ALL of these steps using the tools, in order:
1. Create a file named data.txt containing exactly these three lines:
alpha
bravo
charlie
2. Create a file named report/count.txt containing only the number of lines
in data.txt (use run_bash with wc -l to derive it — do not guess).
3. Reply with a final answer stating the line count.
"""


def score(workdir):
    """Score one trial's filesystem state → (passed, checks).

    count.txt must match ``wc -l`` of data.txt as written, since a file without
    a trailing newline legitimately counts 2.
    """
    base = Path(workdir)
    checks = {}
    data = base / "data.txt"
    data_ok = (data.is_file() and
               data.read_text().strip() == "alpha\nbravo\ncharlie")
    checks["data.txt content"] = data_ok
    count = base / "report" / "count.txt"
    if data_ok and count.is_file():
        expected = str(data.read_text().count("\n"))  # what wc -l truly says
        got = count.read_text().strip()
        # First token only: raw `wc -l data.txt` output ("2 data.txt") is acceptable.
        first = got.split()[0] if got.split() else ""
        checks["count.txt faithful to wc -l"] = first == expected
        detail = {"count_raw": got[:60], "wc_expected": expected}
    else:
        checks["count.txt faithful to wc -l"] = False
        detail = {"count_exists": count.is_file()}
    passed = all(v for k, v in checks.items())
    checks["detail"] = detail
    return passed, checks


def gate(model, trials=TRIALS, max_turns=10, on_event=None, transport=None):
    """Run ``trials`` trials, each in a fresh temp workdir; return the verdict dict.

    A HarnessError counts as a FAIL. ``transport`` is passed to loop.run (None = local node).
    """
    results = []
    for i in range(trials):
        wd = tempfile.mkdtemp(prefix="harness-gate-")
        t0 = time.monotonic()
        try:
            answer, meta = loop.run(
                TASK, tools_local.standard_tools(wd), model=model,
                system="You are a capable agent working inside %s. Follow the "
                       "task exactly." % wd,
                max_turns=max_turns, on_event=on_event, transport=transport,
                gate=_GATE)
            passed, checks = score(wd)
            results.append({"trial": i + 1, "pass": passed,
                            "wall_s": round(time.monotonic() - t0, 1),
                            "turns": meta["turns"],
                            "tools": [c for c, _ in meta["tool_calls"]],
                            "checks": checks, "answer": (answer or "")[:80]})
        except loop.HarnessError as e:
            results.append({"trial": i + 1, "pass": False,
                            "wall_s": round(time.monotonic() - t0, 1),
                            "error": str(e)[:120]})
        finally:
            shutil.rmtree(wd, ignore_errors=True)
    passes = sum(1 for r in results if r["pass"])
    return {"model": model, "passes": passes, "trials": trials,
            "verdict": "PASS" if passes == trials else
                       ("MARGINAL" if passes else "FAIL"),
            "results": results}

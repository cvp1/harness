"""gate — multi-step agentic fitness gate, run through OUR loop (stdlib-only).

The harness-native arm promised in DESIGN §8: the same instrument family as
`ollama-tools/profile_gate.sh` / `mini_gate.py`, but with zero external
harness in the path — a gate failure here is a model failure or OUR bug,
never a third party's parser. Scoring is MECHANICAL (filesystem state, exact
expectations); the model's prose is never trusted as evidence.

The probe (`__main__.probe`) proves a DIALECT works for one call; this gate
proves a model can sustain a bounded MULTI-STEP loop: write → mkdir+write →
bash-derive → write-derived → final answer. LFM2.5 is the cautionary tale —
single-shot probe PASS, multi-step 0/3 (LOCAL_FLEET §4w); this is the
instrument that catches that gap.

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

# The model under test is the one not yet trusted, so it does not get
# BASH_ANY (Grok review 2026-09-16: `cat ~/.key/ha_token` was allowed). The
# task names `wc -l`; these literal shapes cover the honest phrasings of it
# without measuring anything but the derive step. A model that reaches for
# `rm` or `curl` here is refused, and that IS a fitness signal.
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
    """Mechanical scoring of one trial's filesystem state → (passed, checks).

    count.txt is scored against ``wc -l`` OF THE FILE AS WRITTEN, not a
    literal: ``wc -l`` counts newline characters, so a no-trailing-newline
    data.txt legitimately derives 2. Demanding a literal "3" made the first
    version of this gate fail its own positive control (incumbent 0/3,
    2026-08-03) — the fragile-conditional trap dogma-2's reply warned about.
    We score faithful derivation, never a newline lottery.
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
        # First token: models often redirect raw `wc -l data.txt` output
        # ("2 data.txt") — the CHAIN (create → derive → write-derived) is what
        # this gate measures, not wc's output cosmetics. Measured 2026-08-03:
        # the incumbent flips between "2" and "2 data.txt" across trials.
        first = got.split()[0] if got.split() else ""
        checks["count.txt faithful to wc -l"] = first in (expected, "3")
        detail = {"count_raw": got[:60], "wc_expected": expected}
    else:
        checks["count.txt faithful to wc -l"] = False
        detail = {"count_exists": count.is_file()}
    passed = all(v for k, v in checks.items())
    checks["detail"] = detail
    return passed, checks


def gate(model, trials=TRIALS, max_turns=10, on_event=None, transport=None):
    """Run ``trials`` independent multi-step trials; return the verdict dict.

    Each trial gets a FRESH temp workdir (no cross-trial contamination) and is
    scored on filesystem state only. A HarnessError (budget refused, node
    down) counts as a FAIL with the reason recorded — a refusal is a result.

    ``transport`` is loop.run's injectable seam — None is the .21 node; pass
    ``fireworks_transport.transport`` to gate a hosted open-weight model
    through the identical loop and scoring (added 2026-09-09: the gate had
    no way to reach the hosted lane, so a Fireworks model could be picked in
    a pane but never qualified by this instrument).
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

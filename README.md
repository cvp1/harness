# harness — we ARE the harness

The fleet's own agentic loop + model translation layer. Any local model —
Gemma, Qwen, Mistral, next month's tag — drives local tools through one
canonical, bounded, stdlib-only loop that we own end to end. No hermes, no
opencode, no Codex CLI in the path.

```sh
cd ~/Github/CC
/usr/bin/python3 -m harness.selftest                 # offline, no network
/usr/bin/python3 -m harness --probe <model-tag>      # which dialect passes?
/usr/bin/python3 -m harness "task…" --workdir DIR    # run the agentic floor
```

```python
from harness import Tool, run, run_agentic
answer, meta = run_agentic("summarize the logs in this dir", workdir)
```

Design, contract (the seam the dogma-2/gpt shims target), bounds, and the
live-validation record: `DESIGN.md`. Model→dialect assignments: `registry.json`
(data, not code — probe a model to earn a `proven` row).

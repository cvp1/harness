# harness — we ARE the harness

The fleet's own agentic loop + model translation layer. Any local model —
Gemma, Qwen, Mistral, next month's tag — drives local tools through one
canonical, bounded, stdlib-only loop that we own end to end. No hermes, no
opencode, no Codex CLI in the path.

```sh
cd ~/Github/CC
/usr/bin/python3 -m harness.selftest                 # offline, no network
/usr/bin/python3 -m harness --probe <model-tag>      # which dialect passes?
/usr/bin/python3 -m harness --gate  <model-tag>      # multi-step fitness, 3 trials
/usr/bin/python3 -m harness "task…" --workdir DIR    # run the agentic floor
```

## Standalone — the artifact travels

Zero dependencies beyond python3 stdlib + any Ollama-speaking node. Inside
the CC workspace the node fact comes from `_lib/local_llm` (one home);
anywhere else, environment takes over:

```sh
git clone https://github.com/cvp1/harness.git && cd ..   # run from the parent dir
python3 -m harness.selftest                              # green, no network, no _lib
HARNESS_NODE=192.168.86.21 python3 -m harness --probe gemma4-e4b-agent-64k
HARNESS_NODE=host[:port]   # default 127.0.0.1:11434
HARNESS_MODEL=tag          # default: first non-embedding tag on the node
```

```python
from harness import Tool, run, run_agentic, policy
answer, meta = run_agentic("summarize the logs in this dir", workdir)
# bash is DENIED by default; an unattended job declares its allowlist:
gate = policy.gate({"run_bash": policy.bash_policy([r"^wc -l \S+$"])})
answer, meta = run_agentic("count the lines", workdir, gate=gate)
```

Design, contract (the seam the dogma-2/gpt shims target), bounds, and the
live-validation record: `DESIGN.md`. Model→dialect assignments: `registry.json`
(data, not code — probe a model to earn a `proven` row).

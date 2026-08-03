# harness — the fleet's own agentic loop + model translation layer

**Status:** core BUILT + LIVE-VALIDATED 2026-08-02 (see §7); **seam CONVERGED
with dogma-2 2026-08-03** (cc-handoff `2026-08-03T011239Z…-6876` reply — §3a).
**Owner:** Craig. **Consumers:** the dogma-2 / gpt shims replacing hermes and
opencode — §3 is the contract; dogma-2's `model_shim` adapts to it as an
injectable transport.

## 1. Why we own this layer now

Until 2026-08-02 every agentic local turn rented someone else's loop: hermes
(HA/tool path), Codex CLI (`_lib/codex_local`), opencode
(`_lib/opencode_headless`), headless Claude. Our own history is the argument
for owning it — **every hard local-model "failure" that mattered was a
harness-layer defect** (LOCAL_FLEET.md §4n/§4o: hermes `/v1` tool-format
parsing, num_ctx truncation garble, CoT eating `num_predict`), so severe we
built a *neutral comparator* (`ollama-tools/mini_gate.py`) whose only job is
deciding "bad model, or bad harness?" That question dissolves when the harness
is ours: the layer between us and any model — Gemma, Qwen, Mistral, whatever
ships next — is code we can read, test, and fix.

Tool-less local was already native (`_lib/local_llm`). This closes the agentic
lane.

## 2. The core design rule: encode per-model, decode universally

* **ENCODE is dialect-driven** (`dialects.encode`): present tools the way the
  model's template wants them.
  | dialect | tools travel as | for |
  |---|---|---|
  | `native` | Ollama `tools=` param, `role:tool` results | templates with a tool role (gemma4-agent, qwen, mistral, llama3, hermes3) |
  | `prompted` | system-message protocol, one fenced ```` ```tool_call ```` block per turn, results as user messages | template-less models (r1 distills, gemma3) — the mini-swe-agent-proven shape |
* **DECODE is one tolerant ladder** (`dialects.decode`) applied to EVERY
  response regardless of dialect: native `tool_calls` → hermes `<tool_call>`
  XML → fenced block → bare JSON object — with `<think>` stripping first and a
  conservative JSON repair (`loads_relaxed`: strict → trailing-comma/smart-quote
  fix → `ast.literal_eval`; punctuation repaired, never structure). Models
  drift off their dialect under load; the decoder catches the drift instead of
  failing the turn. Prose containing JSON is never stolen (rung 4 fires only
  when the whole reply IS the object).
* **The assignment is data, not code** (`registry.json`, Principle 10):
  fnmatch on model tag → dialect + options, each entry `proven` (cites the
  measurement) or `assumed` (probe before trust). New model → run
  `python3 -m harness --probe <tag>` and it earns its row.

## 3. Contract (the shim seam — keep stable)

```python
from harness import Tool, run, run_agentic, HarnessError

run(task, tools, *, model=None, system=None, dialect=None, options=None,
    max_turns=12, call_timeout=180, deadline=None, gate=None,
    keep_alive=None, transport=None, on_event=None) -> (answer, meta)

run_agentic(task, workdir, *, timeout=600, model=None, max_turns=12,
            gate=None, on_event=None) -> (answer, meta)
    # mirrors _lib/codex_local.run_agentic — drop-in agentic floor
```

* `Tool(name, description, json_schema_params, fn)` where `fn(args: dict) -> str`.
* `meta`: model, host, dialect, turns, tool_calls `[(name, ok|error|unknown|denied)]`,
  denied, wall_s, eval_count, prompt_eval_count.
* `transport(host, payload, timeout)` is injectable — the selftest drives the
  whole loop offline through scripted bodies.
* Node fact has ONE home: `_lib/local_llm.NODES` — the harness imports it,
  never re-declares the chain.
* CLI: `cd ~/Github/CC && python3 -m harness "task" --workdir DIR`
  (`--probe MODEL`, `--selftest`, `--dialect`, `--no-bash`, `--json`).

### 3a. Seam convergence (agreed with dogma-2, 2026-08-03)

Division of labor, per their reply and accepted here:

* **Ranch (this repo)** owns the agent loop, universal decode/repair, the
  dialect registry, `Tool`, and the gate hook.
* **dogma-2's `~/ai-os/tools/local-model/model_shim.py`** (Ollama-native +
  LM Studio/OpenAI-compatible backends, one-turn transport, normalized
  output) plugs in BEHIND this seam as an injectable transport — it is not a
  second loop. Their host layer (residency attribution, memory preflight,
  local safety policy) rides as injectable gate hooks, not dialect code.
* **A transport may return either body shape** — Ollama-native
  (`body.message`) or OpenAI-compatible (`body.choices[0].message`);
  `dialects.message_of()` normalizes, the loop never branches. Usage is read
  from Ollama counters or OpenAI `usage` equally.
* **Transport evidence survives:** `backend` / `resident` / `attributed` /
  `latency_s` / `error` / `provider_error` on a body are preserved into
  `meta["transport"]` (fixed key set, last turn wins) — promotion evidence
  is never discarded by the decoder. OpenAI-style call `id`s round-trip as
  `tool_call_id` on the result message.
* **Parallel tool calls:** normalized by decode, EXECUTED SEQUENTIALLY in
  reply order; result messages follow in the same order. That is the
  contract, not an implementation detail.
* **Streaming stays out of the seam** until a consumer needs it (their
  shim is one-complete-response-per-call; so is ours).

## 4. Bounds (Principle 8 — a bound REFUSES, it never narrates)

| bound | mechanism |
|---|---|
| turns | `max_turns` (12) → `HarnessError` carrying meta |
| wall clock | `deadline` checked before every model call → `HarnessError` |
| per model call | transport timeout (180s; covers ~20s cold load) |
| tool result size | truncated at 8,000 chars with explicit marker |
| bash | 60s timeout, 8,000-char output cap, exit code always reported |
| read_file | 20,000-char cap with marker |

Tool *failures* (exception, unknown name) are fed back to the model and
recorded in meta — recoverable, not fatal. Bound *exhaustion* raises.

## 5. Enforcement seam

`gate(tool_name, args) -> (bool, reason)` runs OUTSIDE the model before every
execution (the Progent pattern; `_lib/policy_gate.check` composes directly).
A denial is enforced (tool does not run), recorded in `meta["denied"]`, and
reported to the model — never silent. A gate that *crashes* fails CLOSED.

## 6. Honest scope

* **Local models, local tools.** No cloud MCP reach — same lane boundary as
  AGENTIC_FAILOVER_SPEC §2. Cloud-tool agentic turns remain un-failoverable.
* **fs tools are jailed** to the workdir (resolve-inside-or-refuse; selftested
  against `../` and absolute escapes). **`run_bash` is bounded, not jailed** —
  bash can address anything the user can. That is deliberate for the trusted
  cron lane; callers wanting hard limits pass a `gate` or `--no-bash`.
* **No scheduling, no routing.** The scheduler stays outside (systemd timers);
  provider choice stays in `_lib/route`/`model_router`. This is the executor.
* **stdlib-only** (urllib + json + subprocess), runs under bare
  `/usr/bin/python3` like `_lib`.
* Parallel tool calls in one reply are executed sequentially, in order.
* Streaming is out of scope for v1 (cron consumers; nothing reads partials).

## 7. Validation record (2026-08-02, ranch-server → .21)

* **Offline selftest** — `python3 -m harness.selftest`: PASS, all checks green
  (every decode rung, string-args, multi-call XML, think-strip incl. unclosed,
  repair rungs, prose-not-stolen, both encoders, registry, loop bounds refuse,
  gate deny + gate-crash-fails-closed, prompted end-to-end, truncation, jail).
* **Dialect probe** — `--probe gemma4-e4b-agent-64k`: native PASS (16.5s,
  cold), prompted PASS (2.5s, warm). Registry `proven` row confirmed live.
* **End-to-end agentic run** — write/read/`wc -l` task, workdir-jailed:
  4 turns, 3 tool calls, correct file on disk, 15.7s wall. Notable: the model
  hit a real discrepancy (`wc -l`=2, no trailing newline) and reasoned about
  it correctly in the final answer — a multi-step run with real error
  handling, zero external harness in the loop.

## 8. Next (not in this build)

* dogma-2 adapts `model_shim.call_once` to the §3 transport interface (their
  proposal, their side); hermes' HA lane and `codex_local`'s floor migrate
  behind `run_agentic` once the shims land and gate green.
* Probe + registry rows for qwen/mistral/llama3/granite tags when next pulled
  (`assumed` → `proven`; granite carries dogma-2 suite evidence already).
* `profile_gate.sh` / `mini_gate.py` grow a harness-native arm so model gating
  and production share one loop.

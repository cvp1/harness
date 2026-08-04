# Seam conformance re-run — dogma-2 `model_shim` against the ranch harness

**Date:** 2026-08-04 · **Host:** ranch-server · **Trigger:** Craig, "re-run" — dogma-2
claimed the seam integration complete (cc-handoff `…171835Z_dogma-2-harness-seam-integration-complete`,
artifact commit `ea0d7d0` per `…172013Z_harness-seam-commit-reference`) with a Grok
verdict of SHIP, no findings. Those results were **relayed**; this is the re-derivation
on our own instruments.

**Verdict: the seam works — with one real defect their suite and ours both miss.
Their transport does not deliver our payload; it re-derives it, and silently drops
`payload["options"]`.** Latent today (no registry row or in-repo caller sets a
non-`think` option), so nothing is currently broken in production.

## 1. Ranch-side suites (our instruments, offline)

```
cd ~/Github/CC && /usr/bin/python3 -m harness.selftest       → SELFTEST PASS, 53 PASS / 0 FAIL
cd ~/Github/CC && /usr/bin/python3 -m harness.selftest_acp   → ACP SELFTEST PASS, 17 PASS / 0 FAIL
```

Includes the six seam checks added in `de6a8b9`: OpenAI-shape usage accounting,
transport evidence preserved in `meta`, OpenAI call-id pairing, dual-shape decode.

## 2. Their suite, re-run by us (not relayed)

```
ssh st21 'cd ~/ai-os && git log --oneline -1'   → ea0d7d0 Add Ranch harness transport seam
ssh st21 'cd ~/ai-os/tools/local-model && python3 test_model_shim.py'
  → Ran 6 tests … OK
```

Their claimed commit is HEAD on dogma-2 and their 6 offline contract tests pass on
their host. Note `pytest` is absent there (python 3.14 via homebrew); the file runs
standalone under `unittest`.

## 3. The check neither side had: our loop driven by THEIR transport

Driver: `harness/reviews/conform_seam.py` (also kept in the session scratchpad).
Their `model_shim.py` is imported at `ea0d7d0`; `model_shim.http_json` is stubbed so
no provider is dialed. Their `transport` is injected into `loop.run(transport=…)`.

14 checks, **11 PASS / 3 FAIL**:

| result | check |
|---|---|
| PASS | e2e answer returned through their transport |
| PASS | two turns, one tool call executed |
| PASS | token accounting survives their body reshaping |
| PASS | `backend` / `resident` / `attributed` / `latency_s` preserved into `meta["transport"]` |
| PASS | tool specs, `think=false`, `keep_alive` survive the shim |
| PASS | provider failure fails closed as `HarnessError` (their `TransportError` subclasses `OSError`) |
| **FAIL** | `options.num_predict` delivered |
| **FAIL** | `options.num_ctx` delivered |
| **FAIL** | `options.temperature` delivered |

Measured:

```
harness asked for: options={'num_predict': 64, 'num_ctx': 4096, 'temperature': 0.7}
provider received: {'temperature': 0, 'num_ctx': 32768, 'num_predict': 2048}
```

Cause — `model_shim.ollama_call` rebuilds the request instead of forwarding it:

```python
"options": {
    "temperature": request.get("temperature", 0),
    "num_ctx":     request.get("context", 32768),
    "num_predict": request.get("max_tokens", 2048),
},
```

It reads *top-level* `temperature` / `context` / `max_tokens` — AI-OS's own request
shape. Our loop puts these under `payload["options"]` (Ollama's shape), so every one
falls through to the shim's hardcoded default. `think`, `keep_alive` and `tools` are
read from the right places, which is why the gap escaped both suites.

## 4. Why this matters more than its current blast radius

DESIGN §1 names the three defects that justified owning this layer: hermes `/v1`
tool-format parsing, **num_ctx truncation garble**, and **CoT eating `num_predict`**.
A transport that pins `num_ctx=32768` and `num_predict=2048` regardless of what the
harness asked re-introduces exactly that class of bug, one layer lower — and it
would present as a *model* failure, which is the confusion `mini_gate.py` exists to
resolve.

Partly ours: §3/§3a never states that a transport must deliver the payload as given.
The contract says what a transport may *return* (either body shape, evidence keys) and
says nothing about payload fidelity. A conforming-looking implementation was reachable
from the spec as written.

## 5. Actions

1. **Ours (ranch, done in this pass):** keep `conform_seam.py` as the cross-side
   conformance instrument so a future shim revision is checked, not trusted.
2. **Ours (spec):** add payload fidelity to DESIGN §3a — a transport MUST forward
   `model`, `messages`, `tools`, `think`, `keep_alive`, `options` unchanged; it may add,
   it may not drop or override. *(not yet written — pending Craig's OK to edit DESIGN)*
3. **Theirs (dogma-2):** in `ollama_call`, merge `request.get("options")` over the
   derived defaults, and add a contract test asserting a caller-supplied
   `options.num_predict` reaches the wire. Draft handoff staged, unsent.

## 6. What remains UNVERIFIED

- **Live**, on-node behavior through their shim against a real Ollama model on `.21`:
  not run here. All of §3 is offline with a stubbed HTTP layer.
- Their **LM Studio** path: exercised by neither their 6 tests nor these 14 beyond
  backend selection.
- Their Grok "SHIP, no findings" verdict: still relayed. I have not seen the prompt it
  was given, so per Principle 18 it carries no weight in this verdict either way.

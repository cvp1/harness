"""Fireworks.ai transport for ``harness.loop.run`` (hosted open-weight models).

Rewrites the loop's Ollama-shaped request into an OpenAI-compatible body and
returns the response verbatim; ``dialects`` already decodes that shape.

    from harness import fireworks_transport, loop
    answer, meta = loop.run(task, tools, model="glm-5p3",
                            transport=fireworks_transport.transport)

Self-test (no spend):  python3 -m harness.fireworks_transport
Live check (1 call):   python3 -m harness.fireworks_transport ping
"""
import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# Without the _lib spine this lane is unavailable rather than an import error.
try:
    from _lib import fireworks_llm
except ImportError:            # standalone clone — no spine, no lane
    fireworks_llm = None

_NO_SPINE = "Fireworks lane needs the _lib spine (standalone harness has none)"


def _spine():
    if fireworks_llm is None:
        raise FireworksTransportError(_NO_SPINE)
    return fireworks_llm

# Ollama request keys with no OpenAI equivalent (Fireworks has no "reasoning off" value).
_DROP = ("think", "keep_alive", "options")

# Ollama option -> OpenAI parameter; only exact equivalents are carried.
_OPTION_MAP = {"num_predict": "max_tokens", "temperature": "temperature",
               "top_p": "top_p", "seed": "seed", "stop": "stop"}
# num_predict -1 means "unbounded" to Ollama; OpenAI-shaped APIs reject it.
_UNBOUNDED = (-1, -2)
# Ceiling sized for reasoning models, which can spend most of the budget thinking.
DEFAULT_MAX_TOKENS = 32768
# Usage-ledger job name; process-scoped because the transport signature carries no job.
JOB = os.environ.get("HARNESS_JOB", "harness-fireworks")


class FireworksTransportError(RuntimeError):
    """The Fireworks call could not be made."""


def to_openai_body(payload, qualify=None):
    """Rewrite one loop payload as an OpenAI-compatible request body (pure).

    ``qualify`` maps the model id to the wire id; defaults to Fireworks namespacing.
    """
    body = {k: v for k, v in payload.items() if k not in _DROP}
    body["model"] = (qualify or _spine().qualify)(payload.get("model"))
    body["stream"] = False

    opts = payload.get("options") or {}
    for src, dst in _OPTION_MAP.items():
        if src in opts and opts[src] not in _UNBOUNDED:
            body[dst] = opts[src]
    body.setdefault("max_tokens", DEFAULT_MAX_TOKENS)

    # Drop Ollama's `tool_name`; OpenAI pairs results by `tool_call_id`.
    msgs = []
    for m in body.get("messages") or []:
        if "tool_name" in m:
            m = {k: v for k, v in m.items() if k != "tool_name"}
        msgs.append(m)
    body["messages"] = msgs
    return body


def transport(host, payload, timeout):
    """``loop.run`` transport; ``host`` is ignored. Returns the response verbatim."""
    del host
    body = to_openai_body(payload)
    data = json.dumps(body).encode("utf-8")
    try:
        key = _spine()._key()
    except Exception as e:         # noqa: BLE001 — locked vault, missing key
        raise FireworksTransportError("no Fireworks key: %s" % e)
    req = urllib.request.Request(
        fireworks_llm.API_URL, data=data, method="POST",
        headers={"Content-Type": "application/json",
                 "Authorization": "Bearer %s" % key})
    t0 = time.monotonic()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            b = json.loads(resp.read().decode("utf-8"))
        # Meter via the client's ledger; failure never blocks the turn.
        try:
            fireworks_llm.meter(b, body["model"], time.monotonic() - t0, job=JOB)
        except Exception as e:     # noqa: BLE001 — metering must not break a turn
            print("fireworks_transport: meter skipped: %s" % e, file=sys.stderr)
        return b
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")[:400]
        # ValueError becomes a HarnessError in loop.run.
        raise ValueError("Fireworks %s on %s: %s"
                         % (e.code, fireworks_llm.short(body["model"]), detail))


def available():
    """Is this lane usable at all (key present, vault unlocked)? No spend."""
    return fireworks_llm is not None and fireworks_llm.available()


def unavailable_reason():
    """``None`` when the lane can run, else the reason phrased as the fix.

    A locked vault is reported separately from a missing key.
    """
    if fireworks_llm is None:
        return _NO_SPINE
    from _lib import secrets
    key_file = Path.home() / ".key" / "fireworks.key"
    if os.environ.get("FIREWORKS_API_KEY"):
        return None
    if secrets.vault_locked():
        return "~/.key is locked — run keyvault/unlock.sh"
    if not key_file.exists():
        return "no Fireworks key — put one at ~/.key/fireworks.key"
    if not fireworks_llm.available():
        return "~/.key/fireworks.key unreadable — is the vault unlocked? " \
               "(keyvault/unlock.sh)"
    return None


def list_models(**kw):
    """Live model list for the pane's picker — never a hardcoded list."""
    return _spine().list_models(**kw)


def _selftest():
    failures = []

    def check(name, cond):
        print("  %-58s %s" % (name, "ok" if cond else "FAIL"))
        if not cond:
            failures.append(name)

    payload = {
        "model": "glm-5p3", "stream": False, "think": True,
        "keep_alive": "5m",
        "options": {"num_predict": 512, "temperature": 0.2, "num_ctx": 8192},
        "messages": [{"role": "system", "content": "sys"},
                     {"role": "user", "content": "hi"},
                     {"role": "tool", "tool_name": "read_file",
                      "tool_call_id": "call_1", "content": "result"}],
        "tools": [{"type": "function",
                   "function": {"name": "read_file", "description": "d",
                                "parameters": {"type": "object"}}}],
    }
    b = to_openai_body(payload)

    check("model is namespaced for the wire",
          b["model"] == fireworks_llm.NS + "glm-5p3")
    check("think dropped (no accepted 'off' value exists)", "think" not in b)
    check("keep_alive dropped (node-only concept)", "keep_alive" not in b)
    check("options bag dropped", "options" not in b)
    check("num_predict -> max_tokens", b["max_tokens"] == 512)
    check("temperature carried", b["temperature"] == 0.2)
    check("unmappable option not invented", "num_ctx" not in b)
    check("tools passed through unchanged", b["tools"] == payload["tools"])
    check("tool_name stripped from tool result",
          "tool_name" not in b["messages"][2])
    check("tool_call_id preserved (the id OpenAI pairs on)",
          b["messages"][2]["tool_call_id"] == "call_1")
    check("caller's messages not mutated",
          "tool_name" in payload["messages"][2])
    check("stream forced false", b["stream"] is False)

    # An unbounded num_predict must not travel as a literal -1.
    b2 = to_openai_body({"model": "m", "messages": [],
                         "options": {"num_predict": -1}})
    check("num_predict -1 -> a real bound, not -1",
          b2["max_tokens"] == DEFAULT_MAX_TOKENS)
    b3 = to_openai_body({"model": "m", "messages": []})
    check("absent num_predict still bounded (PRINCIPLES 8)",
          b3["max_tokens"] == DEFAULT_MAX_TOKENS)

    import inspect
    try:
        inspect.signature(transport).bind("host", {}, 30)
        ok = True
    except TypeError:
        ok = False
    check("transport binds loop.run's (host, payload, timeout)", ok)

    # The verbatim response relies on dialects decoding the OpenAI shape.
    if __package__:
        from . import dialects
    else:
        from harness import dialects
    fake = {"choices": [{"message": {"role": "assistant", "content": "",
                                     "tool_calls": [{"id": "c1", "type": "function",
                                                     "function": {"name": "read_file",
                                                                  "arguments": "{\"p\": \"x\"}"}}]}}]}
    dec = dialects.decode(fake)
    check("dialects decodes an OpenAI tool_call verbatim",
          len(dec.calls) == 1 and dec.calls[0]["name"] == "read_file")
    check("dialects pairs the call id", dec.calls[0].get("id") == "c1")

    print("\n%s (%d failed)" % ("PASS" if not failures else "FAIL", len(failures)))
    return 1 if failures else 0


def _ping():
    if not available():
        print("no Fireworks key — set ~/.key/fireworks.key")
        return 1
    if __package__:
        from . import loop as hloop
    else:
        from harness import loop as hloop
    try:
        answer, meta = hloop.run(
            "Reply with exactly: OK", [], model=fireworks_llm.MODEL,
            transport=transport, max_turns=1)
    except Exception as e:  # noqa: BLE001
        print("live run FAILED: %s" % e)
        return 1
    print("live ok  model=%s  reply=%r" % (meta["model"], (answer or "").strip()[:60]))
    print("         turns=%d  in=%d out=%d  %.2fs"
          % (meta["turns"], meta["prompt_eval_count"], meta["eval_count"],
             meta["wall_s"]))
    return 0


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "selftest"
    raise SystemExit(_ping() if mode == "ping" else _selftest())

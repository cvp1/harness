"""Fireworks.ai as a harness transport — the open-weight lane for Corral.

``harness.loop.run`` takes an injectable ``transport(host, payload, timeout)``
seam. This module is one: it accepts the loop's Ollama-shaped request, calls
Fireworks' OpenAI-shaped endpoint, and hands the raw response straight back.
That gets the WHOLE harness — the tool loop, the enforcement gate, the ACP
permission rail with its exact-bytes diff — pointed at models the fleet has no
other route to (GLM, Kimi, MiniMax, gpt-oss, Nemotron), instead of only at the
.21 Ollama node.

WHY THIS IS THIN, which is the point. Two layers already speak OpenAI:

  * ``dialects.message_of`` reads ``choices[0].message`` before falling back to
    Ollama's ``body.message``, and ``dialects.decode`` reads native
    ``tool_calls`` off whichever it found.
  * ``loop.run`` already counts tokens from an OpenAI ``usage`` block (it was
    taught to for LM Studio / model_shim backends).

So the response needs NO translation at all — it is returned verbatim. Only the
REQUEST is rewritten, and only to drop Ollama-isms the OpenAI schema rejects.
Resisting the urge to "normalize" the response is deliberate: a second decoder
here would be a third place that has to agree with dialects.py about tool-call
shape, and shapes drift.

DATA CLASS: Fireworks is THIRD-PARTY (``_lib.merit_policy.CANDIDATES`` is the
authority — no promotion record exists). A pane on this lane is capped at
``internal``; sensitive work belongs on the Claude, Codex, or sovereign local
lanes. Corral surfaces that on the lane itself so the choice is visible at the
moment it is made, not buried here.

REASONING SPEND: measured 2026-08-31, these models think and bill for it, and
some report no ``reasoning_tokens`` at all (nemotron spent 106 output tokens to
answer "OK"). ``reasoning_effort: "none"`` is rejected by every model probed, so
there is no switch to flip — the loop's ``think`` flag is dropped rather than
translated into a parameter that would 400 the call. Cost visibility lives in
``_lib.fireworks_llm``'s ledger for direct calls; a pane's spend shows up in the
same place when the ACP server passes a ``job``.

    from harness import fireworks_transport, loop
    answer, meta = loop.run(task, tools, model="glm-5p3",
                            transport=fireworks_transport.transport)

Self-test (no spend):  /usr/bin/python3 -m harness.fireworks_transport
Live check (1 call):   /usr/bin/python3 -m harness.fireworks_transport ping
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

from _lib import fireworks_llm

# Ollama request keys with no OpenAI equivalent. Dropped, never guessed at:
#   think       — Fireworks has no accepted "off" value (reasoning_effort:"none"
#                 400s on every model probed), so there is nothing to map it to.
#   keep_alive  — a node-residency hint; meaningless to a hosted endpoint.
#   options     — Ollama's nested knob bag; the two that transfer are lifted out
#                 explicitly below and the rest deliberately do not travel.
_DROP = ("think", "keep_alive", "options")

# Ollama option -> OpenAI parameter. Only knobs with an exact equivalent are
# carried; a knob translated on vibes silently changes generation behaviour.
_OPTION_MAP = {"num_predict": "max_tokens", "temperature": "temperature",
               "top_p": "top_p", "seed": "seed", "stop": "stop"}
# num_predict -1 means "unbounded" to Ollama; OpenAI-shaped APIs reject it.
_UNBOUNDED = (-1, -2)
DEFAULT_MAX_TOKENS = 4096
# The ledger's `job` column for calls made through this seam. The seam
# signature is fixed by loop.run (host, payload, timeout) and carries no job,
# so it is process-scoped: the ACP server / gate runner set $HARNESS_JOB.
JOB = os.environ.get("HARNESS_JOB", "harness-fireworks")


class FireworksTransportError(RuntimeError):
    """The Fireworks call could not be made."""


def to_openai_body(payload):
    """Rewrite one loop payload as a Fireworks/OpenAI request body.

    Pure and side-effect free (no network, no key) so the self-test can assert
    the whole mapping without spending anything.
    """
    body = {k: v for k, v in payload.items() if k not in _DROP}
    body["model"] = fireworks_llm.qualify(payload.get("model"))
    body["stream"] = False

    opts = payload.get("options") or {}
    for src, dst in _OPTION_MAP.items():
        if src in opts and opts[src] not in _UNBOUNDED:
            body[dst] = opts[src]
    body.setdefault("max_tokens", DEFAULT_MAX_TOKENS)

    # `tool_name` is Ollama's spelling on a tool-result message; the OpenAI
    # schema pairs results by `tool_call_id` and rejects unknown keys on some
    # backends. dialects.tool_result_message emits both, so dropping this one
    # loses nothing — the id it actually needs is already there.
    msgs = []
    for m in body.get("messages") or []:
        if "tool_name" in m:
            m = {k: v for k, v in m.items() if k != "tool_name"}
        msgs.append(m)
    body["messages"] = msgs
    return body


def transport(host, payload, timeout):
    """The ``loop.run(transport=...)`` seam. ``host`` is ignored — Fireworks is
    one hosted endpoint, not a node the fleet picks between; the parameter stays
    to satisfy the seam's signature.

    Returns the provider's response VERBATIM: ``dialects`` already decodes the
    OpenAI shape, so translating here would only add a second decoder to keep in
    agreement with it.
    """
    del host                       # seam signature; no node to address
    body = to_openai_body(payload)
    data = json.dumps(body).encode("utf-8")
    try:
        key = fireworks_llm._key()
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
        # Meter through the client's OWN path (LAST_META, CoT warning, the
        # fireworks_usage.jsonl ledger) so a pane's spend is visible to the
        # same ceiling a direct call is. Until 2026-09-09 this returned the
        # body without accounting, and a Corral pane on this lane was
        # invisible to observability. Never load-bearing: the body is
        # returned verbatim whether or not the meter succeeds.
        try:
            fireworks_llm.meter(b, body["model"], time.monotonic() - t0, job=JOB)
        except Exception as e:     # noqa: BLE001 — metering must not break a turn
            print("fireworks_transport: meter skipped: %s" % e, file=sys.stderr)
        return b
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")[:400]
        # ValueError is in loop.run's caught set, so this surfaces as a clean
        # HarnessError naming the model rather than a raw traceback in the pane.
        raise ValueError("Fireworks %s on %s: %s"
                         % (e.code, fireworks_llm.short(body["model"]), detail))


def available():
    """Is this lane usable at all (key present, vault unlocked)? No spend."""
    return fireworks_llm.available()


def unavailable_reason():
    """``None`` when the lane can run, else the REASON, phrased as the fix.

    Mirrors ``codex_launcher.unavailable_reason``. Corral's picker calls this so
    an unusable lane is greyed out with something actionable, instead of opening
    a pane that looks alive and dies on the first prompt (the gemini lesson).
    The two failures are worth distinguishing because the fixes are different
    and one of them is a one-liner Craig runs constantly: an fscrypt vault that
    is simply locked after a reboot is not a missing credential.
    """
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
    return fireworks_llm.list_models(**kw)


# --------------------------------------------------------------------------- #
# Self-test:  /usr/bin/python3 -m harness.fireworks_transport        (no spend)
#             /usr/bin/python3 -m harness.fireworks_transport ping   (1 call)
# --------------------------------------------------------------------------- #
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

    # The seam contract: loop.run binds transport(host, payload, timeout).
    import inspect
    try:
        inspect.signature(transport).bind("host", {}, 30)
        ok = True
    except TypeError:
        ok = False
    check("transport binds loop.run's (host, payload, timeout)", ok)

    # The decode half is dialects', not ours — assert it really does read the
    # OpenAI shape, since this module returns the body verbatim on that belief.
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

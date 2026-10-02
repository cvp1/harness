"""DeepSeek-direct transport for ``harness.loop.run``.

Posts the loop's Ollama-shaped request to DeepSeek's OpenAI-compatible endpoint
via the shared ``fireworks_transport.to_openai_body`` rewrite. ``think=False``
disables thinking on hybrid models, which otherwise reason by default. Every
turn is metered through ``deepseek_llm.meter()`` under ``$HARNESS_JOB``.

    from harness import deepseek_transport, loop
    answer, meta = loop.run(task, tools, model="deepseek-v4-flash",
                            transport=deepseek_transport.transport)

Self-test (no spend):  python3 -m harness.deepseek_transport
Live check (1 call):   python3 -m harness.deepseek_transport ping
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

from _lib import deepseek_llm

if __package__:
    from .fireworks_transport import to_openai_body
else:
    from harness.fireworks_transport import to_openai_body  # noqa: E402

# Usage-ledger job name for calls through this transport.
JOB = os.environ.get("HARNESS_JOB", "harness-deepseek")


class DeepSeekTransportError(RuntimeError):
    """The DeepSeek call could not be made."""


def to_deepseek_body(payload):
    """Shared OpenAI rewrite plus DeepSeek's thinking toggle."""
    body = to_openai_body(payload, qualify=lambda m: m)   # DeepSeek ids are bare
    model = body.get("model")
    if model in deepseek_llm.HYBRID_THINKING_MODELS and not payload.get("think"):
        body["thinking"] = {"type": "disabled"}
    return body


def transport(host, payload, timeout):
    """``loop.run`` transport; ``host`` is ignored (single hosted endpoint)."""
    del host
    body = to_deepseek_body(payload)
    data = json.dumps(body).encode("utf-8")
    try:
        key = deepseek_llm._key()
    except Exception as e:         # noqa: BLE001 — locked vault, missing key
        raise DeepSeekTransportError("no DeepSeek key: %s" % e)
    req = urllib.request.Request(
        deepseek_llm.API_URL, data=data, method="POST",
        headers={"Content-Type": "application/json",
                 "Authorization": "Bearer %s" % key})
    t0 = time.monotonic()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            b = json.loads(resp.read().decode("utf-8"))
        try:
            deepseek_llm.meter(b, body["model"], time.monotonic() - t0, job=JOB,
                               thinking=bool(payload.get("think")))
        except Exception as e:     # noqa: BLE001 — metering must not break a turn
            print("deepseek_transport: meter skipped: %s" % e, file=sys.stderr)
        return b
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")[:400]
        # ValueError becomes a HarnessError in loop.run.
        raise ValueError("DeepSeek %s on %s: %s" % (e.code, body["model"], detail))


def available():
    """Key present and vault unlocked? No spend."""
    return deepseek_llm.available()


def unavailable_reason():
    """``None`` when the lane can run, else the reason phrased as the fix."""
    from _lib import secrets
    key_file = Path.home() / ".key" / "deepseek_key"
    if os.environ.get("DEEPSEEK_API_KEY"):
        return None
    if secrets.vault_locked():
        return "~/.key is locked — run keyvault/unlock.sh"
    if not key_file.exists():
        return "no DeepSeek key — put one at ~/.key/deepseek_key"
    if not deepseek_llm.available():
        return "~/.key/deepseek_key unreadable — is the vault unlocked? " \
               "(keyvault/unlock.sh)"
    return None


def list_models(**kw):
    """Live model catalog as ``[{"id", "tools"}]``; DeepSeek publishes no tools flag."""
    return [{"id": m["id"], "tools": True} for m in deepseek_llm.list_models(**kw)]


def _selftest():
    failures = []

    def check(name, cond):
        print("  %-58s %s" % (name, "ok" if cond else "FAIL"))
        if not cond:
            failures.append(name)

    payload = {"model": "deepseek-v4-flash", "stream": False, "think": False,
               "keep_alive": "5m", "options": {"num_predict": 256},
               "messages": [{"role": "user", "content": "hi"},
                            {"role": "tool", "tool_name": "read_file",
                             "tool_call_id": "call_1", "content": "r"}]}
    b = to_deepseek_body(payload)
    check("model id is bare (no Fireworks namespace)", b["model"] == "deepseek-v4-flash")
    check("think=False -> thinking disabled on a hybrid model",
          b.get("thinking") == {"type": "disabled"})
    check("think key itself does not travel", "think" not in b)
    b2 = to_deepseek_body(dict(payload, think=True))
    check("think=True leaves the model's thinking on", "thinking" not in b2)
    b3 = to_deepseek_body(dict(payload, model="deepseek-chat"))
    check("non-hybrid model gets no thinking knob", "thinking" not in b3)
    check("shared rewrite applied (num_predict -> max_tokens)", b["max_tokens"] == 256)
    check("tool_name stripped, tool_call_id kept",
          "tool_name" not in b["messages"][1] and b["messages"][1]["tool_call_id"] == "call_1")
    import inspect
    try:
        inspect.signature(transport).bind("host", {}, 30)
        ok = True
    except TypeError:
        ok = False
    check("transport binds loop.run's (host, payload, timeout)", ok)
    check("JOB defaults to harness-deepseek", JOB == os.environ.get("HARNESS_JOB", "harness-deepseek"))
    print("\n%s (%d failed)" % ("PASS" if not failures else "FAIL", len(failures)))
    return 1 if failures else 0


def _ping():
    if not available():
        print("no DeepSeek key — set ~/.key/deepseek_key")
        return 1
    if __package__:
        from . import loop as hloop
    else:
        from harness import loop as hloop
    try:
        answer, meta = hloop.run("Reply with exactly: OK", [], model=deepseek_llm.MODEL,
                                 transport=transport, max_turns=1)
    except Exception as e:  # noqa: BLE001
        print("live run FAILED: %s" % e)
        return 1
    print("live ok  model=%s  reply=%r" % (meta["model"], (answer or "").strip()[:60]))
    print("         turns=%d  in=%d out=%d  %.2fs"
          % (meta["turns"], meta["prompt_eval_count"], meta["eval_count"], meta["wall_s"]))
    return 0


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "selftest"
    raise SystemExit(_ping() if mode == "ping" else _selftest())

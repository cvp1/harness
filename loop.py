"""loop — the native agentic loop (stdlib-only). We ARE the harness.

Until 2026-08-02 every agentic local turn in this fleet rented someone else's
loop — hermes, Codex CLI, opencode, headless Claude — and every hard-won
false negative in LOCAL_FLEET.md §4n/§4o was a harness-layer defect we could
not fix because we did not own the layer. This module is that layer, owned:
canonical messages in, ``dialects`` translating per model, tools executed
here, bounded everywhere.

Contract (the seam the dogma-2 / gpt shims target — keep it stable):

    run(task, tools, model=None, ...)        -> (answer, meta)   # the loop
    run_agentic(task, workdir, ...)          -> (answer, meta)   # the floor,
        mirrors _lib/codex_local.run_agentic so the two are interchangeable

Bounds (Principle 8 — a bound REFUSES, it never just narrates):
  * ``max_turns``   — model calls per run; exceeding raises HarnessError.
  * ``deadline``    — wall-clock budget checked before every model call.
  * tool results    — truncated to MAX_RESULT_CHARS with an explicit marker.
  * per-call        — transport timeout on every /api/chat POST.

Enforcement seam: ``gate`` is a callable ``(tool_name, args) -> (bool, reason)``
run OUTSIDE the model before every execution (the Progent pattern —
_lib/policy_gate composes here). A denial is enforced (the tool does not run)
and reported back to the model so it can adapt; it is never silent.
"""
import json
import os
import sys
import time
import urllib.error
import urllib.request

# Node resolution, two worlds:
#   * Inside the CC workspace, `_lib.local_llm` stays the ONE home for the
#     node fact (host, resident model, keep_alive policy) — never a second
#     copy here.
#   * Standalone (the artifact cloned anywhere), `_lib` is absent by design:
#     HARNESS_NODE ("host" or "host:port", default 127.0.0.1) and
#     HARNESS_MODEL (default: first non-embedding tag on the node) take over.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
try:
    from _lib import local_llm
except ImportError:
    local_llm = None

try:
    from . import dialects, registry
except ImportError:  # run as a loose script rather than a package
    import dialects, registry  # noqa: F401

DEFAULT_PORT = 11434

MAX_RESULT_CHARS = 8000       # cap on any single tool result fed back
DEFAULT_MAX_TURNS = 12        # model calls per run
DEFAULT_CALL_TIMEOUT = 180    # seconds per /api/chat POST (cold load ~20s)
DEFAULT_WALL_BUDGET = 600     # seconds per run_agentic run, mirrors codex_local


class HarnessError(RuntimeError):
    """The loop refused: budget exhausted, no node, or an unusable model reply.

    Carries ``meta`` (the run's telemetry so far) for the caller's post-mortem.
    """

    def __init__(self, message, meta=None):
        super().__init__(message)
        self.meta = meta or {}


class Tool:
    """One callable tool: name + description + JSON-schema params + fn(args)->str."""

    def __init__(self, name, description, parameters, fn):
        self.name = name
        self.description = description
        self.parameters = parameters or {"type": "object", "properties": {}}
        self.fn = fn

    def spec(self):
        return {"name": self.name, "description": self.description,
                "parameters": self.parameters}


def _endpoint(host):
    """``host`` may be bare ("192.168.86.21") or carry a port ("...:11435").

    A transport treats it as an opaque endpoint id — seam §3 unchanged.
    """
    return host if ":" in host else "%s:%d" % (host, DEFAULT_PORT)


def _default_transport(host, payload, timeout):
    """One non-streaming /api/chat POST. Kept tiny so tests inject a fake."""
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        "http://%s/api/chat" % _endpoint(host), data=data,
        headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def node_available(host=None, timeout=4):
    """Cheap liveness probe of the resolved node (GET /api/version)."""
    if host is None:
        host = _resolve_host()
    try:
        req = urllib.request.Request("http://%s/api/version" % _endpoint(host))
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status == 200
    except (urllib.error.URLError, OSError):
        return False


def _first_tag(host, timeout=6):
    """Standalone default model: the node's first non-embedding tag."""
    req = urllib.request.Request("http://%s/api/tags" % _endpoint(host))
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        tags = json.loads(resp.read().decode("utf-8")).get("models") or []
    for m in tags:
        if "embed" not in m.get("name", ""):
            return m["name"]
    raise HarnessError("no usable model tag on node %s" % host)


def _resolve_host():
    """Cheap, zero-network: _lib's node fact when present, else HARNESS_NODE."""
    if local_llm is not None:
        return local_llm.NODES[0]["host"]
    return os.environ.get("HARNESS_NODE", "127.0.0.1")


def _resolve_model(host, model):
    """Lazy: only the standalone-no-env path costs a network tags call."""
    if model:
        return model
    if local_llm is not None:
        return local_llm.NODES[0]["model"]
    return os.environ.get("HARNESS_MODEL") or _first_tag(host)


def _resolve_keep_alive(keep_alive):
    if keep_alive:
        return keep_alive
    return local_llm.DEFAULT_KEEP_ALIVE if local_llm is not None else "10m"


def _truncate(text, cap=MAX_RESULT_CHARS):
    if len(text) <= cap:
        return text
    return text[:cap] + "\n…[truncated %d chars]" % (len(text) - cap)


def run(task, tools, *, model=None, system=None, dialect=None, options=None,
        max_turns=DEFAULT_MAX_TURNS, call_timeout=DEFAULT_CALL_TIMEOUT,
        deadline=None, gate=None, keep_alive=None, transport=None,
        on_event=None, history=None, should_stop=None):
    """Drive one bounded agentic run against the local node chain.

    ``tools`` is a list of :class:`Tool`. ``dialect``/``options`` override the
    registry profile for ``model``. ``transport(host, payload, timeout)`` is
    injectable for tests. ``on_event(kind, detail)`` is an optional progress
    callback (never load-bearing). Returns ``(answer, meta)``; raises
    :class:`HarnessError` when a bound refuses or every node fails.
    """
    transport = transport or _default_transport
    host = _resolve_host()
    mdl = _resolve_model(host, model)
    prof = registry.profile_for(mdl)
    dia = dialect or prof["dialect"]
    opts = dict(prof.get("options") or {})
    opts.update(options or {})
    think = opts.pop("think", False)
    keep_alive = _resolve_keep_alive(keep_alive)
    toolmap = {t.name: t for t in tools}
    if len(toolmap) != len(tools):
        raise HarnessError("duplicate tool names in toolset")

    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    # Prior conversation turns (multi-turn consumers: the ACP pane). Caller-
    # owned plain chat messages; the loop neither trims nor rewrites them.
    messages.extend(history or [])
    messages.append({"role": "user", "content": task})

    meta = {"model": mdl, "host": host, "dialect": dia,
            "turns": 0, "tool_calls": [], "denied": [], "wall_s": 0.0,
            "eval_count": 0, "prompt_eval_count": 0}
    t0 = time.monotonic()

    def emit(kind, detail):
        if on_event:
            try:
                on_event(kind, detail)
            except Exception:  # noqa: BLE001 — progress must never kill the run
                pass

    for _turn in range(max_turns):
        if should_stop is not None and should_stop():
            meta["wall_s"] = time.monotonic() - t0
            raise HarnessError("cancelled by caller", meta)
        if deadline is not None and time.monotonic() - t0 > deadline:
            meta["wall_s"] = time.monotonic() - t0
            raise HarnessError(
                "wall budget %ss exhausted after %d turns" % (deadline, meta["turns"]),
                meta)
        payload_msgs, payload_tools = dialects.encode(
            messages, [t.spec() for t in tools], dia)
        payload = {"model": mdl, "messages": payload_msgs, "stream": False,
                   "think": think, "keep_alive": keep_alive,
                   "options": opts or {}}
        if payload_tools:
            payload["tools"] = payload_tools
        try:
            body = transport(host, payload, call_timeout)
        except (urllib.error.URLError, OSError, ValueError) as e:
            meta["wall_s"] = time.monotonic() - t0
            raise HarnessError("node %s (%s) failed: %s"
                               % (host, mdl, e), meta)
        meta["turns"] += 1
        # Token accounting from either body shape (Ollama-native counters, or
        # OpenAI-compatible `usage` from LM Studio / model_shim backends).
        usage = body.get("usage") or {}
        meta["eval_count"] += (body.get("eval_count")
                               or usage.get("completion_tokens") or 0)
        meta["prompt_eval_count"] += (body.get("prompt_eval_count")
                                      or usage.get("prompt_tokens") or 0)
        # Preserve transport evidence (seam agreement with dogma-2,
        # cc-handoff 2026-08-03T011239Z): residency/attribution/provider
        # details feed promotion decisions and must survive the decoder.
        # Fixed key set (bounded), last turn wins.
        for k in ("backend", "resident", "attributed", "latency_s",
                  "error", "provider_error"):
            if k in body:
                meta.setdefault("transport", {})[k] = body[k]

        decoded = dialects.decode(body)
        if not decoded.calls:
            meta["wall_s"] = time.monotonic() - t0
            # An empty final answer is ALWAYS a failure here, never a result.
            # A reasoning model that burns its whole token budget thinking
            # returns empty content with a normal stop reason, which is
            # indistinguishable from a real answer unless we check (measured
            # 2026-09-13: glm-5p3 spent 19,651 reasoning tokens and returned
            # 0 chars, and the caller got "" as success). Degrade toward
            # safety — say what happened instead of handing back silence.
            if not decoded.text.strip():
                raise HarnessError(_empty_answer_why(decoded, usage), meta)
            return decoded.text, meta

        # Record the assistant turn as the model produced it, then execute.
        # Tool calls in one reply run SEQUENTIALLY, in reply order; each
        # result message follows in the same order (seam contract §3).
        messages.append(dialects.message_of(body)
                        or {"role": "assistant", "content": decoded.text})
        for call in decoded.calls:
            name, args = call["name"], call["args"]
            emit("tool_call", {"name": name, "args_keys": sorted(args)})
            tool = toolmap.get(name)
            if tool is None:
                result = ("ERROR: unknown tool %r. Available: %s"
                          % (name, ", ".join(sorted(toolmap))))
                meta["tool_calls"].append((name, "unknown"))
            elif gate is not None and not _gate_ok(gate, name, args, meta):
                result = "DENIED by policy: %s" % meta["denied"][-1][1]
            else:
                try:
                    result = _truncate(str(tool.fn(args)))
                    meta["tool_calls"].append((name, "ok"))
                except Exception as e:  # noqa: BLE001 — fed back, not fatal
                    result = "ERROR: %s: %s" % (type(e).__name__, e)
                    meta["tool_calls"].append((name, "error"))
            messages.append(dialects.tool_result_message(
                name, result, dia, call_id=call.get("id")))

    meta["wall_s"] = time.monotonic() - t0
    raise HarnessError("turn budget %d exhausted without a final answer"
                       % max_turns, meta)


def _empty_answer_why(decoded, usage):
    """Diagnose an empty final answer so the caller sees the cause, not silence.

    The common cause is a reasoning model whose chain-of-thought consumed the
    completion budget, leaving no room for the answer. That is a budget
    problem with a concrete fix (raise ``num_predict``/``max_tokens``), so say
    so rather than reporting a generic empty reply.
    """
    detail = usage.get("completion_tokens_details") or {}
    reasoning_tokens = detail.get("reasoning_tokens") or 0
    if reasoning_tokens or decoded.thinking:
        return ("model returned no answer — it spent the completion budget "
                "reasoning (%s reasoning tokens, %d chars of reasoning kept). "
                "Raise num_predict/max_tokens for this model."
                % (reasoning_tokens or "unreported", len(decoded.thinking)))
    return "model returned an empty answer with no tool call and no reasoning"


def _gate_ok(gate, name, args, meta):
    """Run the enforcement gate; record and refuse on denial (never silent)."""
    try:
        verdict = gate(name, args)
    except Exception as e:  # a broken gate fails CLOSED
        meta["denied"].append((name, "gate error: %s" % e))
        meta["tool_calls"].append((name, "denied"))
        return False
    allowed, reason = (verdict if isinstance(verdict, tuple)
                       else (bool(verdict), "denied by policy"))
    if not allowed:
        meta["denied"].append((name, reason))
        meta["tool_calls"].append((name, "denied"))
        return False
    return True


def run_agentic(task, workdir, *, timeout=DEFAULT_WALL_BUDGET, model=None,
                max_turns=DEFAULT_MAX_TURNS, gate=None, on_event=None):
    """The agentic local floor — same shape as ``_lib/codex_local.run_agentic``.

    Multi-step local-tool work (fs + bounded bash) confined to ``workdir``,
    against the .21 node. Returns ``(answer, meta)``.
    """
    try:
        from . import tools_local
    except ImportError:
        import tools_local
    if not node_available():
        raise HarnessError("no node reachable at %s — is Ollama up?"
                           % _resolve_host())
    system = ("You are a capable agent working inside the directory %s. "
              "Use the tools to complete the task, then give a short final "
              "answer stating what you did." % workdir)
    return run(task, tools_local.standard_tools(workdir), model=model,
               system=system, max_turns=max_turns, deadline=timeout,
               gate=gate, on_event=on_event)


def with_agentic_fallback(primary, task, workdir, *, probe_first=True,
                          on_fallback=None, **kw):
    """Run ``primary()`` (a thunk); on any failure, degrade to the local floor.

    The agentic sibling of ``_lib/local_llm.with_fallback`` — same shape as
    the ``codex_local`` version it supersedes (2026-08-03), so consumers swap
    by import. Returns ``(result, source)`` with source ``"primary"`` or
    ``"local"``. With ``probe_first`` and no reachable node, the primary's
    exception re-raises unwrapped.
    """
    try:
        return primary(), "primary"
    except Exception as primary_exc:  # noqa: BLE001 — primary can fail any way
        if on_fallback:
            on_fallback(primary_exc)
        if probe_first and not node_available():
            raise
        return run_agentic(task, workdir, **kw), "local"

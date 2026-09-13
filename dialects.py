"""dialects — the model translation layer (stdlib-only).

The one job of this module: make ANY local model's tool-calling work through
ONE canonical interface, so the agentic loop (``loop.py``) never knows or cares
which model family it is driving.

Design rule (the core insight, earned the hard way — LOCAL_FLEET.md §4n/§4o,
mini_gate.py's whole reason to exist):

    **Encode per-model, decode universally.**

* ENCODE is dialect-driven: we present tools the way the model's template wants
  them — the ``native`` Ollama ``tools=`` param when the template supports it,
  or a ``prompted`` system-message protocol when it doesn't.
* DECODE is one tolerant ladder applied to EVERY response regardless of
  dialect: native ``tool_calls`` → hermes-style ``<tool_call>`` XML → fenced
  ```` ```tool_call ```` blocks → a bare JSON object. Models drift off their
  assigned dialect under load; the decoder catches them instead of failing the
  turn. Every harness-layer false negative we have recorded (hermes /v1
  parsing, CoT eating num_predict, truncation garble) was a rigid decoder
  meeting a drifting model.

Canonical shapes (plain dicts — no classes to serialize):

    ToolSpec  {"name": str, "description": str, "parameters": {json-schema}}
    ToolCall  {"name": str, "args": dict}
    decode(body) -> Decoded(text, calls, thinking)

Repair ladder for JSON-ish payloads (``loads_relaxed``): strict json →
trailing-comma strip + smart-quote normalization → ``ast.literal_eval`` (safe;
accepts single-quoted python-dict spellings). Conservative on purpose — we
repair punctuation, never structure.
"""
import ast
import json
import re
from collections import namedtuple

DIALECTS = ("native", "prompted")

Decoded = namedtuple("Decoded", "text calls thinking")

# Keys under which models spell "arguments" — normalized to a dict.
_ARG_KEYS = ("arguments", "args", "parameters", "input")

_THINK_RE = re.compile(r"<think>.*?</think>\s*", re.DOTALL)
_THINK_OPEN_RE = re.compile(r"^\s*<think>.*\Z", re.DOTALL)  # unclosed = all CoT
_HERMES_RE = re.compile(r"<tool_call>\s*(.*?)\s*</tool_call>", re.DOTALL)
_FENCE_RE = re.compile(
    r"```(?:tool_call|tool_code|json)?\s*\n(.*?)```", re.DOTALL)
_TRAILING_COMMA_RE = re.compile(r",\s*([}\]])")
_SMART_QUOTES = {"\u201c": '"', "\u201d": '"', "\u2018": "'", "\u2019": "'"}


class DialectError(ValueError):
    """A payload we recognized as a tool call but could not repair."""


# ------------------------------------------------------------------ repairs ---
def loads_relaxed(raw):
    """Parse JSON-ish text into a python object, repairing punctuation only.

    Rungs: strict json.loads → trailing-comma strip + smart-quote fix →
    ast.literal_eval (safe, handles single-quoted dicts). Raises DialectError
    when nothing parses — the caller decides whether that is fatal.
    """
    raw = raw.strip()
    try:
        return json.loads(raw)
    except (ValueError, TypeError):
        pass
    fixed = raw
    for bad, good in _SMART_QUOTES.items():
        fixed = fixed.replace(bad, good)
    fixed = _TRAILING_COMMA_RE.sub(r"\1", fixed)
    try:
        return json.loads(fixed)
    except (ValueError, TypeError):
        pass
    try:
        return ast.literal_eval(fixed)
    except (ValueError, SyntaxError):
        raise DialectError("unparseable payload: %.120r" % raw)


def _normalize_call(obj):
    """Coerce one parsed object into a canonical ToolCall dict, or None.

    Accepts native shapes ({"function": {"name": ..., "arguments": ...}}) and
    flat shapes ({"name": ..., "args"/"arguments"/"parameters": ...}). Args
    given as a JSON string are parsed; args absent means {}.
    """
    if not isinstance(obj, dict):
        return None
    call_id = obj.get("id")  # OpenAI-compatible shapes carry it on the outer dict
    if "function" in obj and isinstance(obj["function"], dict):
        obj = obj["function"]
    name = obj.get("name")
    if not isinstance(name, str) or not name:
        return None
    args = {}
    for key in _ARG_KEYS:
        if key in obj:
            args = obj[key]
            break
    if isinstance(args, str):
        try:
            args = loads_relaxed(args)
        except DialectError:
            return None
    if args is None:
        args = {}
    if not isinstance(args, dict):
        return None
    call = {"name": name, "args": args}
    if call_id:
        call["id"] = call_id
    return call


# ------------------------------------------------------------------- decode ---
def message_of(body):
    """Lift the assistant message from an Ollama-native OR OpenAI-compatible
    body (LM Studio / model_shim backends return ``choices[0].message``).

    Seam agreement with dogma-2 (cc-handoff 2026-08-03T011239Z): a transport
    may return either shape; the decoder normalizes, the loop never branches.
    """
    if "choices" in body:
        choices = body.get("choices") or []
        if choices and isinstance(choices[0], dict):
            return choices[0].get("message") or {}
        return {}
    return body.get("message") or {}


def strip_think(content):
    """Remove <think>…</think> spans; an unclosed leading <think> is all CoT."""
    if not content:
        return content or ""
    stripped = _THINK_RE.sub("", content)
    if _THINK_OPEN_RE.match(stripped):
        return ""
    return stripped


def decode(body):
    """Universal decoder: one Ollama ``/api/chat`` body → Decoded(text, calls).

    ``calls == []`` means the model gave a final answer. Applied identically to
    every dialect — see the module docstring for why.
    """
    msg = message_of(body)
    # ``thinking`` is Ollama's spelling; ``reasoning_content`` is the
    # OpenAI-compatible one that hosted reasoning models use (Fireworks GLM /
    # DeepSeek / Kimi, measured 2026-09-13). Reading only the first made a
    # model that spent its entire token budget reasoning look like it had
    # returned a legitimate empty answer — the reasoning was invisible to the
    # loop, so nothing could tell the two apart. Surfaced, never salvaged:
    # we do not mine an answer out of chain-of-thought.
    thinking = msg.get("thinking") or msg.get("reasoning_content") or ""
    content = strip_think(msg.get("content") or "")

    # Rung 1 — native tool_calls field.
    calls = []
    for tc in msg.get("tool_calls") or []:
        norm = _normalize_call(tc)
        if norm:
            calls.append(norm)
    if calls:
        return Decoded(content.strip(), calls, thinking)

    # Rung 2 — hermes-style <tool_call> XML in content.
    for m in _HERMES_RE.finditer(content):
        try:
            norm = _normalize_call(loads_relaxed(m.group(1)))
        except DialectError:
            continue
        if norm:
            calls.append(norm)
    if calls:
        return Decoded(_HERMES_RE.sub("", content).strip(), calls, thinking)

    # Rung 3 — fenced block whose payload is a call-shaped object.
    for m in _FENCE_RE.finditer(content):
        try:
            norm = _normalize_call(loads_relaxed(m.group(1)))
        except DialectError:
            continue
        if norm:
            calls.append(norm)
    if calls:
        return Decoded(_FENCE_RE.sub("", content).strip(), calls, thinking)

    # Rung 4 — the whole reply is one bare JSON object shaped like a call.
    # Only when the content IS the object: never steal prose containing JSON.
    body_text = content.strip()
    if body_text.startswith("{") and body_text.endswith("}"):
        try:
            norm = _normalize_call(loads_relaxed(body_text))
        except DialectError:
            norm = None
        if norm:
            return Decoded("", [norm], thinking)

    return Decoded(body_text, [], thinking)


# ------------------------------------------------------------------- encode ---
_PROMPTED_PROTOCOL = """\
You have access to these tools:

%s

To call a tool, reply with ONLY one fenced block, nothing else:

```tool_call
{"name": "<tool name>", "args": {<arguments matching the schema>}}
```

One tool call per reply. After each call you will receive the result. When you
have completed the task, reply with your final answer as plain text and NO
tool_call block."""


def _tool_lines(tools):
    lines = []
    for t in tools:
        lines.append("- %s: %s" % (t["name"], t["description"]))
        params = t.get("parameters") or {}
        if params.get("properties"):
            lines.append("  parameters (JSON schema): %s"
                         % json.dumps(params, sort_keys=True))
    return "\n".join(lines)


def encode(messages, tools, dialect):
    """Encode canonical (messages, tools) for one dialect.

    Returns ``(payload_messages, payload_tools)`` where ``payload_tools`` is
    the Ollama ``tools=`` value or None. ``messages`` is not mutated.
    """
    if dialect not in DIALECTS:
        raise DialectError("unknown dialect %r (have %s)" % (dialect, DIALECTS))
    if dialect == "native":
        payload_tools = [{"type": "function", "function": t} for t in tools]
        return list(messages), payload_tools

    # prompted — tools ride in the system message, model replies in fences.
    protocol = _PROMPTED_PROTOCOL % _tool_lines(tools)
    out = list(messages)
    if out and out[0].get("role") == "system":
        head = dict(out[0])
        head["content"] = head.get("content", "") + "\n\n" + protocol
        out[0] = head
    else:
        out.insert(0, {"role": "system", "content": protocol})
    return out, None


def tool_result_message(name, content, dialect, call_id=None):
    """Encode one tool result as the message the model sees next turn.

    ``call_id`` (when the call carried one — OpenAI-compatible backends) is
    echoed as ``tool_call_id`` so those backends can pair result to call.
    """
    if dialect == "native":
        # role=tool is the native shape; tool_name is honoured by current
        # Ollama and harmlessly ignored by older templates.
        msg = {"role": "tool", "tool_name": name, "content": content}
        if call_id:
            msg["tool_call_id"] = call_id
        return msg
    return {"role": "user",
            "content": "Tool result for %s:\n%s\n\nContinue the task. Call "
                       "another tool or give the final answer." % (name, content)}

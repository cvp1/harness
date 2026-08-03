"""Offline selftest for the harness — every dialect rung, every bound, no network.

Run:  cd ~/Github/CC && /usr/bin/python3 -m harness.selftest
"""
import json
import sys
import tempfile
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from harness import dialects, registry, loop, tools_local
else:
    from . import dialects, registry, loop, tools_local

FAILS = []


def ok(cond, label):
    print(("PASS  " if cond else "FAIL  ") + label)
    if not cond:
        FAILS.append(label)


def body(content=None, tool_calls=None, thinking=None, **extra):
    msg = {"role": "assistant", "content": content or ""}
    if tool_calls is not None:
        msg["tool_calls"] = tool_calls
    if thinking is not None:
        msg["thinking"] = thinking
    return dict({"message": msg}, **extra)


# ----------------------------------------------------------------- decoder ---
def test_decoder():
    # Rung 1: native tool_calls, args as dict.
    d = dialects.decode(body(tool_calls=[
        {"function": {"name": "read_file", "arguments": {"path": "a.txt"}}}]))
    ok(d.calls == [{"name": "read_file", "args": {"path": "a.txt"}}],
       "native tool_calls decode")
    # Native with args as a JSON STRING (seen from several templates).
    d = dialects.decode(body(tool_calls=[
        {"function": {"name": "f", "arguments": '{"x": 1}'}}]))
    ok(d.calls == [{"name": "f", "args": {"x": 1}}], "native string-args decode")
    # Rung 2: hermes XML, two calls.
    d = dialects.decode(body(
        'ok <tool_call>{"name": "a", "args": {}}</tool_call>'
        '<tool_call>{"name": "b", "arguments": {"k": "v"}}</tool_call>'))
    ok([c["name"] for c in d.calls] == ["a", "b"] and d.text == "ok",
       "hermes XML decode (multi + text preserved)")
    # Rung 3: fenced tool_call block.
    d = dialects.decode(body(
        '```tool_call\n{"name": "write_file", "args": {"path": "x", "content": "y"}}\n```'))
    ok(d.calls and d.calls[0]["name"] == "write_file", "fenced block decode")
    # Fenced json block that is NOT a call (no name) stays text.
    d = dialects.decode(body('```json\n{"lines": 3}\n```'))
    ok(d.calls == [] and "lines" in d.text, "non-call fence left as text")
    # Rung 4: bare JSON object reply.
    d = dialects.decode(body('{"name": "list_dir", "parameters": {"path": "."}}'))
    ok(d.calls == [{"name": "list_dir", "args": {"path": "."}}],
       "bare JSON decode (parameters key)")
    # Prose containing JSON is NOT stolen.
    d = dialects.decode(body('The config is {"name": "x", "args": {}} as shown.'))
    ok(d.calls == [], "prose with embedded JSON not stolen")
    # Think-strip: closed span, and unclosed leading span = all CoT.
    d = dialects.decode(body('<think>hmm</think>done'))
    ok(d.text == "done", "closed think stripped")
    d = dialects.decode(body('<think>hmm forever'))
    ok(d.text == "", "unclosed think = all CoT")
    # think + tool call together (the §4n regime).
    d = dialects.decode(body(
        '<think>x</think><tool_call>{"name": "a", "args": {}}</tool_call>'))
    ok(d.calls and d.calls[0]["name"] == "a", "think + tool_call decode")
    # Repairs: trailing comma, single quotes, smart quotes.
    ok(dialects.loads_relaxed('{"a": 1,}') == {"a": 1}, "repair trailing comma")
    ok(dialects.loads_relaxed("{'a': True}") == {"a": True}, "repair single quotes")
    ok(dialects.loads_relaxed('{“a”: “b”}') == {"a": "b"},
       "repair smart quotes")
    try:
        dialects.loads_relaxed("not json at all {{{")
        ok(False, "unparseable raises DialectError")
    except dialects.DialectError:
        ok(True, "unparseable raises DialectError")
    # Final answer path.
    d = dialects.decode(body("All done: 3 lines."))
    ok(d.calls == [] and d.text.startswith("All done"), "plain final answer")


# ----------------------------------------------------------------- encoder ---
def test_encoder():
    tools = [{"name": "mark", "description": "record a token",
              "parameters": {"type": "object",
                             "properties": {"token": {"type": "string"}},
                             "required": ["token"]}}]
    msgs = [{"role": "user", "content": "hi"}]
    pm, pt = dialects.encode(msgs, tools, "native")
    ok(pt == [{"type": "function", "function": tools[0]}] and pm == msgs,
       "native encode passes tools through")
    pm, pt = dialects.encode(msgs, tools, "prompted")
    ok(pt is None and pm[0]["role"] == "system" and "mark" in pm[0]["content"]
       and "```tool_call" in pm[0]["content"], "prompted encode injects protocol")
    ok(msgs[0]["role"] == "user", "encode does not mutate input messages")
    # prompted merges into an existing system message.
    pm, _ = dialects.encode([{"role": "system", "content": "S."}] + msgs,
                            tools, "prompted")
    ok(pm[0]["content"].startswith("S.") and "mark" in pm[0]["content"],
       "prompted encode merges existing system")
    r = dialects.tool_result_message("mark", "done", "native")
    ok(r["role"] == "tool" and r["tool_name"] == "mark", "native tool result shape")
    r = dialects.tool_result_message("mark", "done", "prompted")
    ok(r["role"] == "user" and "mark" in r["content"], "prompted tool result shape")


# ---------------------------------------------------------------- registry ---
def test_registry():
    p = registry.profile_for("gemma4-e4b-agent-64k")
    ok(p["dialect"] == "native" and p["status"] == "proven",
       "registry: incumbent proven native")
    ok(registry.profile_for("deepseek-r1:8b")["dialect"] == "prompted",
       "registry: r1 prompted")
    ok(registry.profile_for("totally-new-model")["status"] == "default",
       "registry: unknown model gets default")


# -------------------------------------------------------------------- loop ---
def _scripted_transport(script):
    """Return a transport that replays canned bodies and logs payloads."""
    calls = []

    def transport(host, payload, timeout):
        calls.append(payload)
        return script[min(len(calls) - 1, len(script) - 1)]
    return transport, calls


def test_loop():
    echo = loop.Tool("echo", "echo text back",
                     {"type": "object", "properties": {"text": {"type": "string"}}},
                     lambda a: "echo:" + a.get("text", ""))
    # Two-turn run: tool call, then final answer.
    transport, sent = _scripted_transport([
        body(tool_calls=[{"function": {"name": "echo",
                                       "arguments": {"text": "hi"}}}]),
        body("final: hi")])
    answer, meta = loop.run("say hi via echo", [echo], model="gemma4-e4b-agent-64k",
                            transport=transport)
    ok(answer == "final: hi" and meta["turns"] == 2
       and meta["tool_calls"] == [("echo", "ok")], "loop: tool turn then answer")
    ok(sent[1]["messages"][-1]["role"] == "tool"
       and "echo:hi" in sent[1]["messages"][-1]["content"],
       "loop: tool result fed back natively")
    ok(sent[0].get("tools") and sent[0]["think"] is False,
       "loop: registry profile applied (native tools, think=false)")
    # Unknown tool is reported to the model, not fatal.
    transport, _ = _scripted_transport([
        body(tool_calls=[{"function": {"name": "nope", "arguments": {}}}]),
        body("gave up")])
    answer, meta = loop.run("t", [echo], model="m", transport=transport)
    ok(meta["tool_calls"] == [("nope", "unknown")], "loop: unknown tool survives")
    # Tool exception is fed back, not fatal.
    bomb = loop.Tool("bomb", "always fails", {}, lambda a: 1 / 0)
    transport, sent = _scripted_transport([
        body(tool_calls=[{"function": {"name": "bomb", "arguments": {}}}]),
        body("ok")])
    _, meta = loop.run("t", [bomb], model="m", transport=transport)
    ok(meta["tool_calls"] == [("bomb", "error")]
       and "ZeroDivisionError" in sent[1]["messages"][-1]["content"],
       "loop: tool error fed back")
    # Turn budget REFUSES.
    transport, _ = _scripted_transport([
        body(tool_calls=[{"function": {"name": "echo",
                                       "arguments": {"text": "again"}}}])])
    try:
        loop.run("t", [echo], model="m", transport=transport, max_turns=3)
        ok(False, "loop: turn budget refuses")
    except loop.HarnessError as e:
        ok(e.meta.get("turns") == 3, "loop: turn budget refuses")
    # Gate denial enforced + reported.
    transport, sent = _scripted_transport([
        body(tool_calls=[{"function": {"name": "echo",
                                       "arguments": {"text": "x"}}}]),
        body("done")])
    hits = []
    fenced_echo = loop.Tool("echo", "e", {}, lambda a: hits.append(1) or "ran")
    _, meta = loop.run("t", [fenced_echo], model="m", transport=transport,
                       gate=lambda n, a: (False, "echo is forbidden"))
    ok(not hits and meta["denied"] == [("echo", "echo is forbidden")]
       and "DENIED" in sent[1]["messages"][-1]["content"],
       "loop: gate denial enforced, loud, fed back")
    # A crashing gate fails CLOSED.
    def bad_gate(n, a):
        raise RuntimeError("gate exploded")
    transport, _ = _scripted_transport([
        body(tool_calls=[{"function": {"name": "echo",
                                       "arguments": {}}}]), body("d")])
    hits.clear()
    _, meta = loop.run("t", [fenced_echo], model="m", transport=transport,
                       gate=bad_gate)
    ok(not hits and meta["tool_calls"] == [("echo", "denied")],
       "loop: broken gate fails closed")
    # Prompted dialect end-to-end: fenced call in, user-role result out.
    transport, sent = _scripted_transport([
        body('```tool_call\n{"name": "echo", "args": {"text": "p"}}\n```'),
        body("prompted done")])
    answer, meta = loop.run("t", [echo], model="deepseek-r1:8b",
                            transport=transport)
    ok(meta["dialect"] == "prompted" and sent[0].get("tools") is None
       and sent[1]["messages"][-1]["role"] == "user" and answer == "prompted done",
       "loop: prompted dialect end-to-end")
    # Result truncation cap.
    big = loop.Tool("big", "b", {}, lambda a: "x" * (loop.MAX_RESULT_CHARS + 500))
    transport, sent = _scripted_transport([
        body(tool_calls=[{"function": {"name": "big", "arguments": {}}}]),
        body("d")])
    loop.run("t", [big], model="m", transport=transport)
    ok("…[truncated 500 chars]" in sent[1]["messages"][-1]["content"],
       "loop: tool result truncated with marker")


# ------------------------------------------------------------- tools_local ---
def test_tools_local():
    with tempfile.TemporaryDirectory() as td:
        tools = {t.name: t for t in tools_local.standard_tools(td)}
        ok(set(tools) == {"read_file", "write_file", "list_dir", "run_bash"},
           "tools_local: standard set")
        tools["write_file"].fn({"path": "a/b.txt", "content": "hello"})
        ok(tools["read_file"].fn({"path": "a/b.txt"}) == "hello",
           "tools_local: write then read")
        ok("a/" in tools["list_dir"].fn({}), "tools_local: list_dir")
        try:
            tools["read_file"].fn({"path": "../../etc/passwd"})
            ok(False, "tools_local: jail refuses escape")
        except tools_local.ToolRefused:
            ok(True, "tools_local: jail refuses escape")
        try:
            tools["write_file"].fn({"path": "/etc/motd", "content": "x"})
            ok(False, "tools_local: jail refuses absolute-ish escape")
        except tools_local.ToolRefused:
            ok(True, "tools_local: jail refuses absolute-ish escape")
        out = tools["run_bash"].fn({"command": "echo hi; pwd"})
        ok(out.startswith("exit=0") and "hi" in out and td in out,
           "tools_local: bash runs in workdir")
        nb = {t.name for t in tools_local.standard_tools(td, bash=False)}
        ok("run_bash" not in nb, "tools_local: bash=False drops bash")


def main():
    for fn in (test_decoder, test_encoder, test_registry, test_loop,
               test_tools_local):
        print("--- %s ---" % fn.__name__)
        fn()
    print()
    if FAILS:
        print("SELFTEST FAIL — %d failing:" % len(FAILS))
        for f in FAILS:
            print("  - " + f)
        return 1
    print("SELFTEST PASS — all checks green")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

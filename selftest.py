"""Offline selftest for the harness — dialect rungs, loop bounds, policy; no network.

Run:  python3 -m harness.selftest
"""
import json
import sys
import tempfile
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from harness import (dialects, registry, loop, policy, tools_local,
                         fireworks_transport)
else:
    from . import (dialects, registry, loop, policy, tools_local,
                   fireworks_transport)

FAILS = []

# Permissive gate for loop tests whose synthetic tools no policy knows.
_ALLOW = lambda n, a: (True, "ok")  # noqa: E731


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
    # think + tool call together.
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
    # OpenAI-compatible body: choices[0], outer id, string arguments.
    oai = {"choices": [{"message": {
        "role": "assistant", "content": "",
        "tool_calls": [{"id": "call_9", "type": "function",
                        "function": {"name": "read_file",
                                     "arguments": '{"path": "a.txt"}'}}]}}],
        "usage": {"prompt_tokens": 10, "completion_tokens": 5}}
    d = dialects.decode(oai)
    ok(d.calls == [{"name": "read_file", "args": {"path": "a.txt"},
                    "id": "call_9"}], "OpenAI-shape decode with call id")
    ok(dialects.message_of({"choices": []}) == {}, "message_of empty choices safe")


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
    r = dialects.tool_result_message("mark", "done", "native", call_id="call_9")
    ok(r["tool_call_id"] == "call_9", "native tool result echoes call id")
    r = dialects.tool_result_message("mark", "done", "native")
    ok("tool_call_id" not in r, "no phantom call id when none given")


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
                            transport=transport, gate=_ALLOW)
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
    answer, meta = loop.run("t", [echo], model="m", transport=transport, gate=_ALLOW)
    ok(meta["tool_calls"] == [("nope", "unknown")], "loop: unknown tool survives")
    # Tool exception is fed back, not fatal.
    bomb = loop.Tool("bomb", "always fails", {}, lambda a: 1 / 0)
    transport, sent = _scripted_transport([
        body(tool_calls=[{"function": {"name": "bomb", "arguments": {}}}]),
        body("ok")])
    _, meta = loop.run("t", [bomb], model="m", transport=transport, gate=_ALLOW)
    ok(meta["tool_calls"] == [("bomb", "error")]
       and "ZeroDivisionError" in sent[1]["messages"][-1]["content"],
       "loop: tool error fed back")
    # Turn budget REFUSES.
    transport, _ = _scripted_transport([
        body(tool_calls=[{"function": {"name": "echo",
                                       "arguments": {"text": "again"}}}])])
    try:
        loop.run("t", [echo], model="m", transport=transport, max_turns=3, gate=_ALLOW)
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
                            transport=transport, gate=_ALLOW)
    ok(meta["dialect"] == "prompted" and sent[0].get("tools") is None
       and sent[1]["messages"][-1]["role"] == "user" and answer == "prompted done",
       "loop: prompted dialect end-to-end")
    # Result truncation cap.
    big = loop.Tool("big", "b", {}, lambda a: "x" * (loop.MAX_RESULT_CHARS + 500))
    transport, sent = _scripted_transport([
        body(tool_calls=[{"function": {"name": "big", "arguments": {}}}]),
        body("d")])
    loop.run("t", [big], model="m", transport=transport, gate=_ALLOW)
    ok("…[truncated 500 chars]" in sent[1]["messages"][-1]["content"],
       "loop: tool result truncated with marker")
    # OpenAI-compatible transport end-to-end: usage counted, evidence kept,
    # call id paired back on the result message.
    oai_call = {"choices": [{"message": {
        "role": "assistant", "content": "",
        "tool_calls": [{"id": "call_1", "type": "function",
                        "function": {"name": "echo",
                                     "arguments": '{"text": "via-shim"}'}}]}}],
        "usage": {"prompt_tokens": 40, "completion_tokens": 7},
        "backend": "lmstudio", "resident": True, "attributed": "exact",
        "latency_s": 1.2}
    oai_final = {"choices": [{"message": {"role": "assistant",
                                          "content": "shim done"}}],
                 "usage": {"prompt_tokens": 60, "completion_tokens": 4}}
    transport, sent = _scripted_transport([oai_call, oai_final])
    answer, meta = loop.run("t", [echo], model="m", transport=transport, gate=_ALLOW)
    ok(answer == "shim done" and meta["eval_count"] == 11
       and meta["prompt_eval_count"] == 100,
       "loop: OpenAI-shape usage accounted")
    ok(meta["transport"] == {"backend": "lmstudio", "resident": True,
                             "attributed": "exact", "latency_s": 1.2},
       "loop: transport evidence preserved in meta")
    ok(sent[1]["messages"][-1].get("tool_call_id") == "call_1"
       and sent[1]["messages"][-2].get("tool_calls"),
       "loop: OpenAI call id paired, assistant turn recorded from choices")


# -------------------------------------------------- empty-answer / reasoning ---
def test_empty_answer_fails_loud():
    """A reasoning model that spends its budget must not look like success."""
    echo = loop.Tool("echo", "echo", {"type": "object", "properties": {}},
                     lambda a: "x")

    # 1. decode() must SEE OpenAI-style reasoning, not just Ollama's spelling.
    d = dialects.decode({"choices": [{"message": {
        "role": "assistant", "content": "",
        "reasoning_content": "thinking hard about it"}}]})
    ok(d.thinking == "thinking hard about it" and d.text == ""
       and not d.calls, "empty: decode surfaces reasoning_content")

    # 2. Budget spent reasoning -> loud error naming the cause and the fix.
    transport, _ = _scripted_transport([
        {"choices": [{"message": {"role": "assistant", "content": "",
                                  "reasoning_content": "a" * 40}}],
         "usage": {"completion_tokens": 19651,
                   "completion_tokens_details": {"reasoning_tokens": 19651}}}])
    try:
        loop.run("q", [echo], model="glm-5p3", transport=transport, gate=_ALLOW)
        ok(False, "empty: reasoning-starved run raises")
    except loop.HarnessError as e:
        msg = str(e)
        ok("19651" in msg and "max_tokens" in msg,
           "empty: reasoning-starved run raises, naming tokens and the fix")

    # 3. Plain empty answer (no reasoning at all) still refuses, different why.
    transport, _ = _scripted_transport([body("")])
    try:
        loop.run("q", [echo], model="gemma4-e4b-agent-64k", transport=transport, gate=_ALLOW)
        ok(False, "empty: bare empty answer raises")
    except loop.HarnessError as e:
        ok("empty answer" in str(e), "empty: bare empty answer raises")

    # 4. Whitespace-only is empty too — the guard must not be fooled by "\n".
    transport, _ = _scripted_transport([body("   \n  ")])
    try:
        loop.run("q", [echo], model="gemma4-e4b-agent-64k", transport=transport, gate=_ALLOW)
        ok(False, "empty: whitespace-only answer raises")
    except loop.HarnessError:
        ok(True, "empty: whitespace-only answer raises")

    # 5. A REAL answer still returns normally — the guard must not overreach.
    transport, _ = _scripted_transport([body("the actual answer")])
    answer, _meta = loop.run("q", [echo], model="gemma4-e4b-agent-64k",
                             transport=transport, gate=_ALLOW)
    ok(answer == "the actual answer", "empty: a real answer still returns")

    # 6. Fireworks default ceiling clears a long reasoning run (identity qualify).
    b = fireworks_transport.to_openai_body({"model": "glm-5p3", "messages": []},
                                           qualify=lambda m: m)
    ok(b["max_tokens"] >= 22740,
       "empty: fireworks default clears glm-5p3's measured 22,740-token run")
    ok(fireworks_transport.fireworks_llm is not None
       or not fireworks_transport.available(),
       "fireworks: standalone (no _lib) reports unavailable, never tracebacks")


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


def test_gate_scoring():
    if __package__ in (None, ""):
        from harness import gate
    else:
        from . import gate
    with tempfile.TemporaryDirectory() as td:
        passed, checks = gate.score(td)
        ok(not passed and not any(v for k, v in checks.items() if k != "detail"),
           "gate: empty dir scores FAIL")
        (Path(td) / "data.txt").write_text("alpha\nbravo\ncharlie\n")
        (Path(td) / "report").mkdir()
        (Path(td) / "report" / "count.txt").write_text("3\n")
        passed, _ = gate.score(td)
        ok(passed, "gate: exact state scores PASS")
        # No trailing newline + faithful wc -l derivation (2) also PASSES.
        (Path(td) / "data.txt").write_text("alpha\nbravo\ncharlie")
        (Path(td) / "report" / "count.txt").write_text("2")
        passed, _ = gate.score(td)
        ok(passed, "gate: faithful no-trailing-newline derivation PASSES")
        (Path(td) / "report" / "count.txt").write_text("2 data.txt\n")
        passed, _ = gate.score(td)
        ok(passed, "gate: raw wc output form PASSES (chain over cosmetics)")
        # A guessed "3" fails where wc -l says 2.
        (Path(td) / "report" / "count.txt").write_text("3")
        passed, checks = gate.score(td)
        ok(not passed, "gate: a guessed 3 FAILs when wc -l says 2")
        (Path(td) / "report" / "count.txt").write_text("7")
        passed, checks = gate.score(td)
        ok(not passed and checks["data.txt content"],
           "gate: unfaithful count FAILs that check only")


# ------------------------------------------------------------------ policy ---
def test_policy():
    # loop.run with no gate argument denies a tool nobody declared.
    hits = []
    stray = loop.Tool("stray", "undeclared", {}, lambda a: hits.append(1) or "ran")
    transport, sent = _scripted_transport([
        body(tool_calls=[{"function": {"name": "stray", "arguments": {}}}]),
        body("d")])
    _, meta = loop.run("t", [stray], model="m", transport=transport)
    ok(not hits and meta["tool_calls"] == [("stray", "denied")]
       and "DENIED" in sent[1]["messages"][-1]["content"],
       "policy: SEAM — no gate argument still denies an undeclared tool")

    g = policy.DEFAULT
    # Built-in fs policy: clean relative paths pass; escapes and control
    # characters are refused BEFORE tools_local._confine ever sees them.
    ok(g("read_file", {"path": "a/b.txt"})[0], "policy: read_file relative ok")
    ok(g("list_dir", {})[0], "policy: list_dir default path ok")
    ok(not g("read_file", {"path": "/etc/passwd"})[0], "policy: absolute denied")
    ok(not g("read_file", {"path": "../x"})[0], "policy: .. segment denied")
    ok(not g("read_file", {"path": "a\x00b"})[0], "policy: NUL denied")
    ok(not g("read_file", {})[0], "policy: read_file path required")
    ok(g("write_file", {"path": "o.txt", "content": "x"})[0], "policy: write ok")
    ok(not g("write_file", {"path": "o.txt", "content": 5})[0],
       "policy: write non-str content denied")
    ok(not g("write_file", {"path": "o.txt",
                            "content": "x" * (policy.MAX_WRITE_CHARS + 1)})[0],
       "policy: write content cap")
    # run_bash: denied by DEFAULT, allowed only through bash_policy.
    ok(not g("run_bash", {"command": "ls"})[0], "policy: bash denied by default")
    ok(not g("no_such_tool", {})[0], "policy: unknown tool denied")

    bp = policy.bash_policy([r"^wc -l \S+$"], hint="count lines only")
    gb = policy.gate({"run_bash": bp})
    ok(gb("run_bash", {"command": "wc  -l data.txt"})[0], "policy: allowlist hit")
    r = gb("run_bash", {"command": "rm -rf /"})
    ok(not r[0] and "count lines only" in r[1], "policy: allowlist miss carries hint")
    ok(not gb("run_bash", {"command": "wc -l a\nrm -rf /"})[0],
       "policy: multi-line bash denied")
    ok(not gb("run_bash", {"command": "x" * (policy.MAX_BASH_CHARS + 1)})[0],
       "policy: bash length cap")
    ok(not gb("run_bash", {"command": "   "})[0], "policy: empty bash denied")
    ga = policy.gate({"run_bash": policy.bash_policy(policy.BASH_ANY)})
    ok(ga("run_bash", {"command": "ls -la"})[0], "policy: BASH_ANY allows")
    ok(not ga("run_bash", {"command": "ls\nrm x"})[0],
       "policy: BASH_ANY still shape-checks")
    # Allowlist uses fullmatch and refuses shell syntax unless opted in.
    gnoanchor = policy.gate({"run_bash": policy.bash_policy([r"wc -l \S+"])})
    for cmd in ("wc -l data.txt; rm -rf /", "wc -l data.txt;id", "wc -l data.txt|bash",
                "wc -l data.txt&&id", "wc -l $(id)", "wc -l `id`", "wc -l data.txt`id`",
                "wc -l a > b", "wc -l < a"):
        ok(not gnoanchor("run_bash", {"command": cmd})[0],
           "policy: allowlist-shaped injection denied: %s" % cmd)
    ok(gnoanchor("run_bash", {"command": "wc -l data.txt"})[0],
       "policy: plain allowlist hit still allowed")
    gsyn = policy.gate({"run_bash": policy.bash_policy(
        [r"cat [\w./-]+ \| wc -l"], shell_syntax=True)})
    ok(gsyn("run_bash", {"command": "cat a.txt | wc -l"})[0],
       "policy: shell_syntax=True admits a spelled-out pipe")
    ok(not gsyn("run_bash", {"command": "cat a.txt | wc -l; id"})[0],
       "policy: shell_syntax=True is still a fullmatch")
    ok(not policy.DEFAULT("read_file", {"path": "a\\..\\b"})[0], "policy: backslash denied")
    ok(not policy.DEFAULT("read_file", {"path": "C:x"})[0], "policy: drive letter denied")
    ok(not policy.DEFAULT("read_file", {"path": " a.txt"})[0], "policy: padded path denied")
    ok(not policy.DEFAULT("read_file", {"path": "a b"})[0], "policy: U+2028 denied")
    ok(not policy.DEFAULT("read_file", {"path": "foo/./../../x"})[0],
       "policy: normalised climb denied")
    ok(not policy.gate({"n": policy.relative_path()})("n", {"path": "/etc/passwd"})[0],
       "policy: relative_path helper denies absolute")
    gm = policy.gate({"s": policy.string_args(required=("pattern",),
                                              match={"pattern": r"[a-z]+"})})
    ok(not gm("s", {"pattern": "-f/tmp/x"})[0], "policy: match= refuses option-shaped arg")
    ok(gm("s", {"pattern": "solar"})[0], "policy: match= admits the shape")
    try:
        policy._BUILTIN["run_bash"] = lambda a: (True, "")
        ok(False, "policy: built-in table is read-only")
    except TypeError:
        ok(True, "policy: built-in table is read-only")
    ok(not policy.DEFAULT("list_dir", {"path": "\n" * 5000})[0],
       "policy: control-only path is not 'no path'")
    for p in ("harness/policy.py", "sub/_lib/mail.py", ".git/hooks/pre-commit",
              "a/../harness/x"):
        ok(not policy.DEFAULT("write_file", {"path": p, "content": "x"})[0],
           "policy: write_file refuses the trust anchor: %s" % p)
    ok(policy.DEFAULT("write_file", {"path": "notes/harnessed.md", "content": "x"})[0],
       "policy: write_file segment rule is exact, not substring")
    ok(not gnoanchor("run_bash", {"command": "wc -l data.txt"})[0],
       "policy: NBSP in a command denied")
    ok(gnoanchor("run_bash", {"command": "wc   -l   data.txt"})[0],
       "policy: ASCII space runs normalised")
    ok(not gnoanchor("run_bash", {"command": "wc\t-l data.txt"})[0],
       "policy: tab is a control character, denied like any other")

    # Composition is checked when WRITTEN, not when the model calls.
    def raises(fn):
        try:
            fn()
            return False
        except ValueError:
            return True
    ok(raises(lambda: policy.gate({"read_file": lambda a: (True, "")})),
       "policy: built-in policy cannot be replaced")
    ok(raises(lambda: policy.gate({"run_bash": lambda a: (True, "")})),
       "policy: bash needs bash_policy()")
    ok(raises(lambda: policy.bash_policy([])), "policy: empty allowlist refused")
    ok(raises(lambda: policy.gate({"t": "not callable"})),
       "policy: non-callable declaration refused")

    # Declared tools: closed schema — an extra key is denied.
    gd = policy.gate({"mark": policy.string_args(required=("token",))})
    ok(gd("mark", {"token": "7A3"})[0], "policy: declared tool ok")
    ok(not gd("mark", {})[0], "policy: declared required arg")
    ok(not gd("mark", {"token": "x", "to": "+1"})[0],
       "policy: declared tool rejects smuggled key")
    # A crashing declared rule denies.
    def boom(a):
        raise RuntimeError("x")
    ok(not policy.gate({"b": boom})("b", {})[0], "policy: broken rule denies")

    # In-workspace, unknown tools fall through to the spine's policy.
    try:
        from _lib import policy_gate  # noqa: F401
    except ImportError:
        print("skip  policy: spine fall-through (standalone, no _lib)")
    else:
        ok(g("firealert_send", {"message": "RANCH FIRE ALERT - test"})[0],
           "policy: spine fall-through allows a clean estate call")
        ok(not g("firealert_send", {"message": "x", "to": "+1"})[0],
           "policy: spine fall-through denies recipient injection")
        test_connector_tools_fall_through(g)


def test_connector_tools_fall_through(g):
    """Connector predicates installed into _lib/policy_gate are enforced by the default gate."""
    try:
        import google_connector  # noqa: F401 — the import installs the policies
    except Exception:            # noqa: BLE001 — connector absent is not a fail
        ok(True, "policy: connector fall-through SKIPPED (no google_connector)")
        return
    ok(not g("google_mail_recent", {"count": 41})[0],
       "policy: a local model asking for 41 messages is denied by the "
       "connector's own bound, through the harness's default gate")
    ok(g("google_mail_recent", {"count": 5})[0],
       "policy: count=5 passes the same gate and reaches the dispatcher")
    ok(not g("google_mail_send", {"to": "x@y.io"})[0],
       "policy: an omitted connector tool is denied by name in the harness too")
    ok(not g("google_calendar_update", {"id": "a"})[0],
       "policy: a connector write rule (etag required) holds in the harness")


def main():
    for fn in (test_decoder, test_encoder, test_registry, test_loop,
               test_empty_answer_fails_loud,
               test_tools_local, test_gate_scoring, test_policy):
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

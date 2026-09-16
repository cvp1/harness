"""Wire-level selftest for acp_server — offline (HARNESS_ACP_FAKE), no node.

Drives the server exactly as Corral's client does: JSON-RPC over the
subprocess's stdio. The permission checks meet the spike's bar: refusal is
verified on the FILESYSTEM, never trusted from the transcript.

Run:  cd ~/Github/CC && /usr/bin/python3 -m harness.selftest_acp
"""
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FAILS = []


def ok(cond, label):
    print(("PASS  " if cond else "FAIL  ") + label)
    if not cond:
        FAILS.append(label)


class Client:
    """Minimal mirror of corral/acp.py — enough to prove the server's wire."""

    def __init__(self, fake, state_dir, answer=None):
        env = {**os.environ, "HARNESS_ACP_FAKE": fake,
               "HARNESS_ACP_STATE": state_dir}
        self.p = subprocess.Popen(
            [sys.executable, "-m", "harness.acp_server"], cwd=str(ROOT),
            stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, text=True, bufsize=1, env=env)
        self.events, self.perms = [], []
        self.answer = answer          # optionId to give permissions, or None
        self._id = 0
        self._slots = {}
        threading.Thread(target=self._read, daemon=True).start()

    def _read(self):
        for line in self.p.stdout:
            try:
                msg = json.loads(line)
            except ValueError:
                continue
            if msg.get("method") == "session/update":
                self.events.append(msg["params"]["update"])
            elif msg.get("method") == "session/request_permission":
                self.perms.append(msg)
                out = ({"outcome": {"outcome": "selected",
                                    "optionId": self.answer}}
                       if self.answer else {"outcome": {"outcome": "cancelled"}})
                self._send({"jsonrpc": "2.0", "id": msg["id"], "result": out})
            elif "id" in msg and "method" not in msg:
                slot = self._slots.get(msg["id"])
                if slot:
                    slot["msg"] = msg
                    slot["ev"].set()

    def _send(self, obj):
        self.p.stdin.write(json.dumps(obj) + "\n")
        self.p.stdin.flush()

    def request(self, method, params=None, timeout=30):
        self._id += 1
        rid = self._id
        slot = {"ev": threading.Event(), "msg": None}
        self._slots[rid] = slot
        self._send({"jsonrpc": "2.0", "id": rid, "method": method,
                    "params": params or {}})
        assert slot["ev"].wait(timeout), "%s timed out" % method
        m = slot["msg"]
        if "error" in m:
            raise RuntimeError("%s: %s" % (method, m["error"]))
        return m.get("result")

    def close(self):
        self.p.terminate()
        self.p.wait(timeout=10)


def main():
    with tempfile.TemporaryDirectory() as state, \
         tempfile.TemporaryDirectory() as cwd:

        # ── plain answer path ────────────────────────────────────────────
        c = Client("answer", state)
        info = c.request("initialize")
        ok(info.get("protocolVersion") == 1 and info.get("authMethods") == [],
           "initialize shape")
        new = c.request("session/new", {"cwd": cwd, "mcpServers": []})
        sid = new.get("sessionId")
        cfg = {o["id"]: o for o in new.get("configOptions") or []}
        ok(sid and cfg.get("model", {}).get("currentValue"),
           "session/new: id + model configOption")
        r = c.request("session/prompt", {
            "sessionId": sid, "prompt": [{"type": "text", "text": "hello"}]})
        ok(r.get("stopReason") == "end_turn", "prompt: end_turn")
        chunks = [e for e in c.events
                  if e.get("sessionUpdate") == "agent_message_chunk"]
        ok(chunks and chunks[-1]["content"]["text"] == "echo: hello",
           "prompt: agent_message_chunk carries the answer")
        # multi-turn history reaches the model (fake echoes last user msg only,
        # so prove history via a second turn still working + persistence file)
        c.request("session/prompt", {
            "sessionId": sid, "prompt": [{"type": "text", "text": "again"}]})
        saved = json.loads((Path(state) / ("%s.json" % sid)).read_text())
        ok([m["content"] for m in saved["history"]] ==
           ["hello", "echo: hello", "again", "echo: again"],
           "history persisted across turns")
        # set_config: opencode-era namespaced ids normalize; junk refuses loud
        r = c.request("session/set_config_option",
                      {"sessionId": sid, "configId": "model",
                       "value": "ollama/fake-model"})
        ok(r["configOptions"][0]["currentValue"] == "fake-model",
           "set_config: 'ollama/' namespace stripped (the corral 404 bug)")
        try:
            c.request("session/set_config_option",
                      {"sessionId": sid, "configId": "model",
                       "value": "no-such-model"})
            ok(False, "set_config: unknown model refused loudly")
        except RuntimeError as e:
            ok("no-such-model" in str(e) and "have:" in str(e),
               "set_config: unknown model refused loudly")
        # session/list + load from disk
        lst = c.request("session/list", {})["sessions"]
        ok(any(s["sessionId"] == sid for s in lst), "session/list finds it")
        c.close()
        c2 = Client("answer", state)
        c2.request("initialize")
        r = c2.request("session/load", {"sessionId": sid, "cwd": cwd})
        ok("configOptions" in r and not c2.events,
           "session/load: restores without replaying")
        c2.close()

        # ── permission: ALLOW → file really written ──────────────────────
        c = Client("tool", state, answer="allow")
        c.request("initialize")
        sid = c.request("session/new", {"cwd": cwd})["sessionId"]
        r = c.request("session/prompt", {
            "sessionId": sid, "prompt": [{"type": "text", "text": "make it"}]})
        ok(r.get("stopReason") == "end_turn" and len(c.perms) == 1,
           "risky tool raised exactly one permission request")
        pc = c.perms[0]["params"]["toolCall"]
        ok(pc.get("rawInput") == {"path": "fake.txt", "content": "hi"}
           and pc.get("content", [{}])[0].get("type") == "diff",
           "permission carries exact bytes + structured diff")
        ok((Path(cwd) / "fake.txt").read_text() == "hi",
           "ALLOW: file actually written")
        tcs = [e for e in c.events if e.get("sessionUpdate") == "tool_call_update"]
        ok(tcs and tcs[-1]["status"] == "completed", "tool_call_update completed")
        c.close()

        # ── permission: CANCELLED → fail closed, file NOT written ────────
        (Path(cwd) / "fake.txt").unlink()
        c = Client("tool", state, answer=None)   # client cancels every request
        c.request("initialize")
        sid = c.request("session/new", {"cwd": cwd})["sessionId"]
        r = c.request("session/prompt", {
            "sessionId": sid, "prompt": [{"type": "text", "text": "make it"}]})
        ok(r.get("stopReason") == "end_turn", "denied turn still ends cleanly")
        ok(not (Path(cwd) / "fake.txt").exists(),
           "CANCEL: file actually NOT written (fail closed)")
        denied = [e for e in c.events
                  if e.get("sessionUpdate") == "tool_call_update"
                  and e.get("status") == "failed"]
        ok(bool(denied), "denial rendered as failed tool_call_update")
        c.close()

        # ── allow_always remembered within the session ───────────────────
        c = Client("tool", state, answer="allow_always")
        c.request("initialize")
        sid = c.request("session/new", {"cwd": cwd})["sessionId"]
        c.request("session/prompt", {
            "sessionId": sid, "prompt": [{"type": "text", "text": "t1"}]})
        c.request("session/prompt", {
            "sessionId": sid, "prompt": [{"type": "text", "text": "t2"}]})
        ok(len(c.perms) == 1, "allow_always: second call skips the prompt")
        c.close()

        # ── run_bash allow_always is per command PREFIX, not per tool ────
        # Grok 2026-09-16 #9: "Always allow run_bash" meant any command for
        # the session. Now it grants the program(s) of THAT command; a
        # different program raises the card again; a wrapper/substitution/
        # redirection is never offered a standing grant at all.
        def _bash_session(answer):
            cl = Client("bash", state, answer=answer)
            cl.request("initialize")
            s = cl.request("session/new", {"cwd": cwd})["sessionId"]

            def run(cmd):
                before = len(cl.perms)
                cl.request("session/prompt", {
                    "sessionId": s, "prompt": [{"type": "text", "text": cmd}]})
                return cl.perms[before:]
            return cl, run

        c, run = _bash_session("allow_always")
        p = run("wc -l selftest_acp.py")
        ok(len(p) == 1, "prefix: first `wc` raises the card")
        names = [o["name"] for o in p[0]["params"]["options"]]
        ok(any("`wc`" in n for n in names),
           "prefix: the always option names the program, not the tool")
        ok(not run("wc -c selftest_acp.py"),
           "prefix: a second `wc` with other args skips the card")
        ok(len(run("ls")) == 1, "prefix: a different program raises the card")
        ok(not run("ls -la"), "prefix: … and is then granted on its own")
        ok(not run("wc -l x | ls"),
           "prefix: a pipe of two granted programs is auto-allowed")
        p = run("wc -l x | cat")
        ok(len(p) == 1, "prefix: a pipe with one ungranted segment asks")
        for cmd in ("sudo wc -l x", "bash -c 'wc -l x'", "wc -l $(ls)",
                    "wc -l x > out", "python3 -c 'print(1)'"):
            p = run(cmd)
            kinds = [o["kind"] for o in p[0]["params"]["options"]] if p else []
            ok(p and "allow_always" not in kinds,
               "prefix: no standing grant offered for %r" % cmd)
        ok(len(run("sudo wc -l x")) == 1,
           "prefix: a wrapper asks EVERY time even after 'always'")
        c.close()

        # a client that answers allow_always where it was never offered gets a no
        c, run = _bash_session("allow_always")
        run("sudo true")
        denied = [e for e in c.events
                  if e.get("sessionUpdate") == "tool_call_update"
                  and e.get("status") == "failed"]
        ok(bool(denied), "prefix: a forged 'always' on an unoffered card = deny")
        c.close()


        # ── the vault is gated by the lane's data class, not just labelled ──
        # DATA_CLASS was computed and reported in serverInfo and then never
        # consulted, so a Fireworks/DeepSeek pane carried search_notes and
        # read_note over ~/notes -- and neither is in RISKY, so neither ever
        # raised a card. Found by the 2026-09-11 bug bash (grok, CONFIRMED).
        # Under it sat a second defect: _data_class_for passed has_tools=True
        # to merit_policy.eligible, which is a routing-QUALITY opinion, and it
        # demoted the SOVEREIGN local lane to `internal`. Both are asserted
        # here, because fixing either alone gives a wrong answer.
        import acp_server as _srv
        ok(_srv._data_class_for("local") == "sensitive",
           "the sovereign local lane keeps a sensitive ceiling (a tool-quality "
           "opinion must not demote it)")
        for _third in ("fireworks", "deepseek"):
            ok(_srv._data_class_for(_third) == "internal",
               f"{_third} is capped at internal — third party (P11)")
        ok(_srv._data_class_for("no-such-provider") == "internal",
           "an unknown provider fails closed to internal")

        # The gate itself, without standing a server up: _vault_tools consults
        # the module-level DATA_CLASS, so assert on both settings of it.
        _real = _srv.DATA_CLASS
        try:
            _srv.DATA_CLASS = "internal"
            ok(_srv.Server._vault_tools(None) == [],
               "an internal-ceiling lane gets NO vault reader")
            _srv.DATA_CLASS = "sensitive"
            ok(isinstance(_srv.Server._vault_tools(None), list),
               "a sensitive-ceiling lane can build its vault tools")
        finally:
            _srv.DATA_CLASS = _real

    print()
    if FAILS:
        print("ACP SELFTEST FAIL — %d failing" % len(FAILS))
        return 1
    print("ACP SELFTEST PASS — all checks green")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

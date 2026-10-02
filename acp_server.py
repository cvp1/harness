"""acp_server — the harness as an Agent Client Protocol agent (JSON-RPC 2.0 over stdio).

Handles initialize and session/new|load|list|prompt|set_config_option|cancel,
streaming output as session/update notifications. write_file and run_bash
require a session/request_permission answer carrying the exact bytes and a
diff; no answer fails closed. Sessions persist as JSON under
``~/.local/state/cc/harness-acp/``.

Offline test mode: ``HARNESS_ACP_FAKE=answer|tool|bash`` swaps in a scripted model.

Launch:  python3 -m harness.acp_server
"""
import json
import os
import re
import sys
import threading
import time
import uuid
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from harness import loop as hloop
    from harness import policy as hpolicy
    from harness import tools_local
else:
    from . import loop as hloop
    from . import policy as hpolicy
    from . import tools_local

STATE_DIR = Path(os.environ.get(
    "HARNESS_ACP_STATE", str(Path.home() / ".local/state/cc/harness-acp")))
PERMISSION_TIMEOUT = 3600      # a human may be away; bounded, then fail closed
MAX_LINE = 16 * 1024 * 1024    # max bytes per JSON-RPC line
RISKY = {"write_file", "run_bash"}   # tools that need the human's yes

# "Always allow" on run_bash grants per program prefix; a compound command is
# auto-allowed only when every segment's program is granted. Wrappers that run
# a program named in their arguments, substitutions, and redirections get no grant.
_BASH_WRAPPERS = frozenset((
    "sudo", "doas", "env", "bash", "sh", "zsh", "dash", "ksh", "xargs",
    "nohup", "time", "timeout", "nice", "ionice", "command", "builtin",
    "exec", "eval", "source", ".", "watch", "find", "script", "su",
    "python", "python3", "perl", "ruby", "node",   # `-c`/`-e` run anything
    "stdbuf", "setsid", "busybox", "flock", "taskset", "unshare", "strace",
    "ltrace", "chroot", "nsenter", "chrt", "numactl", "fakeroot", "firejail",
    "systemd-run", "runuser", "pkexec", "sg", "newgrp", "unbuffer", "rlwrap",
    "parallel", "gdb", "valgrind", "faketime", "torsocks", "proxychains",
    "proxychains4", "caffeinate", "arch", "ssh",
))
# Global options that can name a program to run; no prefix grant when present.
_BASH_CONFIG_OPTS = {"git": ("-c", "--config-env", "--exec-path")}
_BASH_NO_GRANT = re.compile(r"[`<>\n]|\$\(|\$\{|\(")
_BASH_SEGMENT = re.compile(r"\|\|?|&&?|;")
_BASH_ASSIGN = re.compile(r"[A-Za-z_][A-Za-z0-9_]*=")


def bash_prefixes(cmd):
    """Return the program names a command runs, or ``None`` when no prefix grant is safe."""
    if not isinstance(cmd, str) or not cmd.strip():
        return None
    if _BASH_NO_GRANT.search(cmd):
        return None
    out = set()
    for seg in _BASH_SEGMENT.split(cmd):
        words = seg.split()
        while words and _BASH_ASSIGN.match(words[0]):
            words.pop(0)
        if not words:
            return None                      # empty segment: `a;` / `| b`
        prog = words[0]
        base = prog.rsplit("/", 1)[-1]
        if base in _BASH_WRAPPERS or base != prog and ".." in prog:
            return None
        if not re.fullmatch(r"[A-Za-z0-9_./+-]+", prog):
            return None
        risky = _BASH_CONFIG_OPTS.get(base)
        if risky:
            for w in words[1:]:
                if not w.startswith("-"):
                    break                    # the subcommand: globals end
                if w.split("=", 1)[0] in risky or w.startswith("-c"):
                    return None
        out.add(prog)
    return frozenset(out)
MAX_HISTORY_MSGS = 200         # per session; oldest turns dropped beyond this

SYSTEM = ("You are a capable local agent working inside the directory %s. "
          "Use the tools when they help; answer concisely. If a tool is "
          "DENIED by the operator, respect it and continue without it.")

# Model backend, selected by $HARNESS_ACP_PROVIDER (default: local Ollama).
# The data-class ceiling is read from _lib.merit_policy, never restated here.
PROVIDER = os.environ.get("HARNESS_ACP_PROVIDER", "local").strip().lower()


def _data_class_for(target):
    """Data-class ceiling for `target` from merit_policy; fails closed to `internal`."""
    try:
        from _lib import merit_policy
        # has_tools=False: only the trust axis sets a data-class ceiling, not
        # tool-quality routing, which would wrongly demote the local lane.
        return ("sensitive" if merit_policy.eligible(target, "sensitive", False)
                else "internal")
    except Exception:  # noqa: BLE001 — unreadable taxonomy => the closed answer
        return "internal"


def _local_models():
    """Live tags off the node the loop would call. Raises; caller degrades."""
    host = hloop._resolve_host()
    req = __import__("urllib.request", fromlist=["urlopen"])
    with req.urlopen("http://%s/api/tags" % hloop._endpoint(host),
                     timeout=6) as resp:
        tags = json.loads(resp.read().decode()).get("models") or []
    return [m["name"] for m in tags if "embed" not in m.get("name", "")][:20]


def _local_default():
    return hloop._resolve_model(hloop._resolve_host(), None)


def _fireworks_models():
    from harness import fireworks_transport as fw
    # Tool-capable models only, per the provider's published flag.
    return [m["id"] for m in fw.list_models() if m["tools"]][:20]


def _fireworks_default():
    from _lib import fireworks_llm
    return fireworks_llm.MODEL


def _fireworks_transport():
    from harness import fireworks_transport as fw
    return fw.transport


def _deepseek_models():
    from harness import deepseek_transport as ds
    return [m["id"] for m in ds.list_models() if m["tools"]][:20]


def _deepseek_default():
    from _lib import deepseek_llm
    return deepseek_llm.MODEL


def _deepseek_transport():
    from harness import deepseek_transport as ds
    return ds.transport


PROVIDERS = {
    "local": {
        "label": "Ollama .21",
        "models": _local_models,
        "default": _local_default,
        "transport": lambda: None,     # loop.run's own /api/chat default
        "target": "local",             # merit_policy key -> sovereign
        "where": "on the node",        # phrasing for the unknown-model refusal
        "call_timeout": None,          # loop.run's local default
    },
    "fireworks": {
        "label": "Fireworks.ai",
        "models": _fireworks_models,
        "default": _fireworks_default,
        "transport": _fireworks_transport,
        "target": "fireworks",         # merit_policy key -> THIRD party
        "where": "on Fireworks",
        "call_timeout": 900,           # hosted reasoning models are slow
    },
    "deepseek": {
        "label": "DeepSeek (direct)",
        "models": _deepseek_models,
        "default": _deepseek_default,
        "transport": _deepseek_transport,
        "target": "deepseek",          # merit_policy key -> THIRD party
        "where": "on DeepSeek",
        "call_timeout": 900,           # hosted reasoning models are slow
    },
}
if PROVIDER not in PROVIDERS:
    # Fatal rather than a silent fallback to a different vendor.
    raise SystemExit("harness-acp: unknown HARNESS_ACP_PROVIDER %r (have: %s)"
                     % (PROVIDER, ", ".join(sorted(PROVIDERS))))
SPEC = PROVIDERS[PROVIDER]
DATA_CLASS = _data_class_for(SPEC["target"])


def _now():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


# ---------------------------------------------------------------- fake mode --
def _fake_transport(kind):
    """Deterministic scripted 'model' for offline protocol tests."""
    state = {"n": 0}

    def transport(host, payload, timeout):
        state["n"] += 1
        last = payload["messages"][-1]
        if kind == "tool" and state["n"] == 1:
            return {"message": {"role": "assistant", "content": "", "tool_calls": [
                {"function": {"name": "write_file",
                              "arguments": {"path": "fake.txt", "content": "hi"}}}]},
                "eval_count": 1}
        # "bash": each user prompt is the command to run.
        if kind == "bash" and last.get("role") == "user":
            return {"message": {"role": "assistant", "content": "", "tool_calls": [
                {"function": {"name": "run_bash",
                              "arguments": {"command": last.get("content", "")}}}]},
                "eval_count": 1}
        if last.get("role") == "tool":
            return {"message": {"role": "assistant",
                                "content": "tool said: %s" % last.get("content", "")[:80]},
                    "eval_count": 1}
        return {"message": {"role": "assistant",
                            "content": "echo: %s" % last.get("content", "")},
                "eval_count": 1}
    return transport


# ------------------------------------------------------------------- server --
class Server:
    def __init__(self, stdin=None, stdout=None):
        self.stdin = stdin or sys.stdin
        self.stdout = stdout or sys.stdout
        self._wlock = threading.Lock()
        self._id = 0
        self._idlock = threading.Lock()
        self._pending = {}          # our outgoing requests (permissions)
        self.sessions = {}          # sessionId -> dict
        self.fake = os.environ.get("HARNESS_ACP_FAKE")
        STATE_DIR.mkdir(parents=True, exist_ok=True)

    # ── wire ──────────────────────────────────────────────────────────────
    def _send(self, obj):
        with self._wlock:
            self.stdout.write(json.dumps(obj) + "\n")
            self.stdout.flush()

    def _respond(self, rid, result=None, error=None):
        msg = {"jsonrpc": "2.0", "id": rid}
        if error is not None:
            msg["error"] = error
        else:
            msg["result"] = result if result is not None else {}
        self._send(msg)

    def _notify(self, method, params):
        self._send({"jsonrpc": "2.0", "method": method, "params": params})

    def _request(self, method, params, timeout):
        """Client-directed request (permissions). Blocks until answered."""
        with self._idlock:
            self._id += 1
            rid = "s%d" % self._id
            slot = {"ev": threading.Event(), "result": None}
            self._pending[rid] = slot
        self._send({"jsonrpc": "2.0", "id": rid, "method": method,
                    "params": params})
        ok = slot["ev"].wait(timeout)
        self._pending.pop(rid, None)
        return slot["result"] if ok else None

    def _update(self, session_id, update):
        self._notify("session/update",
                     {"sessionId": session_id, "update": update})

    # ── session state ─────────────────────────────────────────────────────
    def _spath(self, sid):
        return STATE_DIR / ("%s.json" % sid)

    def _persist(self, sess):
        data = {k: sess[k] for k in
                ("sessionId", "cwd", "model", "title", "history")}
        data["updatedAt"] = _now()
        data["history"] = data["history"][-MAX_HISTORY_MSGS:]
        self._spath(sess["sessionId"]).write_text(json.dumps(data))

    def _new_session(self, sid, cwd, model=None, history=None, title=""):
        sess = {"sessionId": sid, "cwd": cwd, "model": model,
                "history": history or [], "title": title,
                "cancel": threading.Event(), "busy": threading.Lock(),
                "allow_always": set(), "callno": 0}
        self.sessions[sid] = sess
        return sess

    def _config_options(self, sess):
        current = sess["model"]
        values = []
        if self.fake:
            values, current = ["fake-model"], current or "fake-model"
        else:
            try:
                if current is None:
                    current = SPEC["default"]()
                values = SPEC["models"]()
            except Exception:  # noqa: BLE001 — picker degrades, session works
                values = [current] if current else []
        sess["model"] = current
        return [{"id": "model", "name": "Model", "currentValue": current,
                 "options": [{"value": v, "name": v, "description": ""}
                             for v in values]}]

    # ── tools ─────────────────────────────────────────────────────────────
    def _vault_tools(self):
        """Vault reader tools, attached only when DATA_CLASS is `sensitive`.

        The vault holds private notes and its readers raise no permission card,
        so any lower ceiling gets no vault tools.
        """
        if DATA_CLASS != "sensitive":
            return []
        try:
            import importlib.util
            p = Path(__file__).resolve().parent.parent / "wiki" / "ask_local.py"
            spec = importlib.util.spec_from_file_location("wiki_ask_local", p)
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            self._vault_policies = mod.tool_policies()
            return mod._tools()
        except Exception:  # noqa: BLE001 — standalone installs have no vault
            self._vault_policies = {}
            return []

    _vault_policies = {}

    def _gate_for(self):
        """Gate: harness default + any-bash (human-approved) + the vault tools' declarations."""
        declare = {"run_bash": hpolicy.bash_policy(hpolicy.BASH_ANY)}
        declare.update(self._vault_policies)
        return hpolicy.gate(declare)

    @staticmethod
    def _title_for(name, args):
        if name == "write_file":
            return "Write %s" % args.get("path", "?")
        if name == "run_bash":
            return "Run: %s" % str(args.get("command", "?"))[:70]
        if name == "read_file":
            return "Read %s" % args.get("path", "?")
        if name == "list_dir":
            return "List %s" % (args.get("path") or ".")
        if name == "search_notes":
            return "Search notes: %s" % args.get("pattern", "?")
        if name == "read_note":
            return "Read note %s" % args.get("path", "?")
        return name

    _KINDS = {"write_file": "edit", "run_bash": "execute", "read_file": "read",
              "list_dir": "read", "search_notes": "search", "read_note": "read"}

    @staticmethod
    def _grant_keys(tool_name, args):
        """Keys an "always allow" would grant (per program prefix for run_bash), or ``None``."""
        if tool_name != "run_bash":
            return frozenset((tool_name,))
        prefixes = bash_prefixes((args or {}).get("command"))
        if prefixes is None:
            return None
        return frozenset(("run_bash", p) for p in prefixes)

    def _permission(self, sess, tool_name, args, tc):
        """True iff the human allows this call. Absence of a yes is a no."""
        keys = self._grant_keys(tool_name, args)
        if keys is not None and keys <= sess["allow_always"]:
            return True
        options = [
            {"kind": "allow_once", "name": "Allow", "optionId": "allow"}]
        if keys is not None:
            if tool_name == "run_bash":
                progs = sorted(k[1] for k in keys)
                label = "Always allow %s commands" % ", ".join(
                    "`%s`" % p for p in progs)
            else:
                label = "Always allow %s" % tool_name
            options.append({"kind": "allow_always", "name": label,
                            "optionId": "allow_always"})
        options.append(
            {"kind": "reject_once", "name": "Reject", "optionId": "reject"})
        params = {"sessionId": sess["sessionId"], "options": options,
                  "toolCall": tc}
        r = self._request("session/request_permission", params,
                          PERMISSION_TIMEOUT)
        outcome = ((r or {}).get("outcome") or {})
        if outcome.get("outcome") != "selected":
            return False                       # cancelled / timeout / malformed
        opt = outcome.get("optionId")
        if opt == "allow_always":
            if keys is None:
                return False   # option was never offered: a forged answer is a no
            sess["allow_always"] |= keys
            return True
        return opt in ("allow", "allow_once")

    def _wrap_tool(self, sess, tool):
        inner = tool.fn

        def fn(args):
            sess["callno"] += 1
            tcid = "hc_%d" % sess["callno"]
            title = self._title_for(tool.name, args)
            kind = self._KINDS.get(tool.name, "other")
            tc = {"toolCallId": tcid, "title": title, "kind": kind,
                  "rawInput": args, "status": "pending"}
            if tool.name == "write_file":
                target = Path(sess["cwd"]) / str(args.get("path", ""))
                old = None
                try:
                    if target.is_file():
                        old = target.read_text(errors="replace")[:20000]
                except OSError:
                    pass
                tc["content"] = [{"type": "diff", "path": str(target),
                                  "oldText": old,
                                  "newText": str(args.get("content", ""))}]
                tc["locations"] = [{"path": str(target)}]
            self._update(sess["sessionId"], dict(tc, sessionUpdate="tool_call"))
            if tool.name in RISKY and not self._permission(sess, tool.name,
                                                           args, tc):
                self._update(sess["sessionId"], {
                    "sessionUpdate": "tool_call_update", "toolCallId": tcid,
                    "status": "failed",
                    "content": [{"type": "content", "content":
                                 {"type": "text", "text": "denied by operator"}}]})
                return "DENIED by operator — do not retry this action."
            try:
                result = inner(args)
                status = "completed"
            except Exception as e:  # noqa: BLE001 — mirror loop semantics
                result = "ERROR: %s: %s" % (type(e).__name__, e)
                status = "failed"
            self._update(sess["sessionId"], {
                "sessionUpdate": "tool_call_update", "toolCallId": tcid,
                "status": status,
                "content": [{"type": "content", "content":
                             {"type": "text", "text": str(result)[:1000]}}]})
            return result

        return hloop.Tool(tool.name, tool.description, tool.parameters, fn)

    def _tools_for(self, sess):
        base = tools_local.standard_tools(sess["cwd"]) + self._vault_tools()
        return [self._wrap_tool(sess, t) for t in base]

    # ── handlers ──────────────────────────────────────────────────────────
    def _h_initialize(self, params):
        # Report the provider and data ceiling so the client can show them.
        return {"protocolVersion": 1, "authMethods": [],
                "agentCapabilities": {"loadSession": True},
                "serverInfo": {"name": "harness-acp", "version": "0.2",
                               "provider": PROVIDER,
                               "backend": SPEC["label"],
                               "dataClass": DATA_CLASS}}

    def _h_new(self, params):
        sid = "h-%s" % uuid.uuid4().hex[:12]
        cwd = params.get("cwd") or os.getcwd()
        sess = self._new_session(sid, cwd)
        opts = self._config_options(sess)
        self._persist(sess)
        return {"sessionId": sid, "configOptions": opts}

    def _h_load(self, params):
        sid = params.get("sessionId")
        path = self._spath(sid)
        if sid in self.sessions:
            sess = self.sessions[sid]
        elif path.is_file():
            data = json.loads(path.read_text())
            sess = self._new_session(sid, params.get("cwd") or data["cwd"],
                                     model=self._normalize_model(data.get("model")) or None,
                                     history=data.get("history") or [],
                                     title=data.get("title", ""))
        else:
            raise ValueError("unknown session %r" % sid)
        return {"configOptions": self._config_options(sess)}

    def _h_list(self, params):
        want_cwd = params.get("cwd")
        out = []
        for f in sorted(STATE_DIR.glob("h-*.json")):
            try:
                d = json.loads(f.read_text())
            except ValueError:
                continue
            if want_cwd and d.get("cwd") != want_cwd:
                continue
            out.append({"sessionId": d.get("sessionId"), "cwd": d.get("cwd"),
                        "title": d.get("title", ""),
                        "updatedAt": d.get("updatedAt")})
        return {"sessions": out[:100]}

    @staticmethod
    def _normalize_model(value):
        """Strip an ``ollama/`` or ``local/`` prefix, which Ollama's API rejects."""
        value = (value or "").strip()
        if "/" in value and value.split("/", 1)[0] in ("ollama", "local"):
            value = value.split("/", 1)[1]
        return value

    def _h_set_config(self, params):
        sess = self.sessions.get(params.get("sessionId"))
        if sess is None:
            raise ValueError("unknown session")
        if params.get("configId") != "model":
            raise ValueError("only 'model' is configurable")
        want = self._normalize_model(params.get("value"))
        opts = self._config_options(sess)
        known = [o["value"] for o in opts[0]["options"]]
        if known and want not in known:
            # Refuse with the available models; the current model is kept.
            raise ValueError("unknown model %r %s; have: %s"
                             % (want, SPEC["where"], ", ".join(known[:6])))
        sess["model"] = want
        self._persist(sess)
        return {"configOptions": self._config_options(sess)}

    def _h_prompt(self, rid, params):
        sess = self.sessions.get(params.get("sessionId"))
        if sess is None:
            return self._respond(rid, error={"code": -32602,
                                             "message": "unknown session"})
        if not sess["busy"].acquire(blocking=False):
            return self._respond(rid, error={"code": -32000,
                                             "message": "session is busy"})
        try:
            text = " ".join(b.get("text", "") for b in params.get("prompt") or []
                            if b.get("type") == "text").strip()
            sess["cancel"].clear()
            if not sess["title"]:
                sess["title"] = text[:60]
            transport = (_fake_transport(self.fake) if self.fake
                         else SPEC["transport"]())
            try:
                tools = self._tools_for(sess)   # sets _vault_policies first
                answer, meta = hloop.run(
                    text, tools, model=sess["model"],
                    system=SYSTEM % sess["cwd"], history=list(sess["history"]),
                    should_stop=sess["cancel"].is_set, transport=transport,
                    call_timeout=(SPEC.get("call_timeout")
                                  or hloop.DEFAULT_CALL_TIMEOUT),
                    gate=self._gate_for())
            except hloop.HarnessError as e:
                stop = ("cancelled" if sess["cancel"].is_set() else "refusal")
                if stop == "refusal":
                    self._update(sess["sessionId"], {
                        "sessionUpdate": "agent_message_chunk",
                        "content": {"type": "text",
                                    "text": "[harness refused: %s]" % e}})
                return self._respond(rid, {"stopReason": stop})
            self._update(sess["sessionId"], {
                "sessionUpdate": "agent_message_chunk",
                "content": {"type": "text", "text": answer}})
            sess["history"].append({"role": "user", "content": text})
            sess["history"].append({"role": "assistant", "content": answer})
            sess["model"] = meta.get("model") or sess["model"]
            self._persist(sess)
            self._respond(rid, {"stopReason": "end_turn"})
        finally:
            sess["busy"].release()

    # ── dispatch ──────────────────────────────────────────────────────────
    def _dispatch(self, msg):
        rid, method = msg.get("id"), msg.get("method")
        params = msg.get("params") or {}
        if method is None and rid is not None:          # response to us
            slot = self._pending.get(rid)
            if slot:
                slot["result"] = msg.get("result")
                slot["ev"].set()
            return
        if method == "session/cancel":                  # notification
            sess = self.sessions.get(params.get("sessionId"))
            if sess:
                sess["cancel"].set()
            return
        if rid is None:
            return                                      # unknown notification
        if method == "session/prompt":                  # long-running: thread
            threading.Thread(target=self._h_prompt, args=(rid, params),
                             daemon=True).start()
            return
        handlers = {"initialize": self._h_initialize,
                    "session/new": self._h_new,
                    "session/load": self._h_load,
                    "session/list": self._h_list,
                    "session/set_config_option": self._h_set_config}
        h = handlers.get(method)
        if h is None:
            return self._respond(rid, error={"code": -32601,
                                             "message": "unsupported: %s" % method})
        try:
            self._respond(rid, h(params))
        except Exception as e:  # noqa: BLE001 — a bad request must not kill the server
            self._respond(rid, error={"code": -32000, "message": str(e)})

    def serve(self):
        buf_total = 0
        for line in self.stdin:
            buf_total = 0  # line-oriented; python's readline bounds per line
            line = line.strip()
            if not line or len(line) > MAX_LINE:
                continue
            try:
                msg = json.loads(line)
            except ValueError:
                continue
            self._dispatch(msg)


def main():
    Server().serve()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

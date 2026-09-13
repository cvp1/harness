"""acp_server — the harness as an ACP agent (stdlib-only). Corral's sovereign pane.

Speaks Agent Client Protocol (JSON-RPC 2.0 over stdio) so Corral — or any ACP
client — can drive OUR loop as a first-class pane. This replaces the last
rented loop on this host: Corral's "Local (Ollama .21)" lane ran on opencode;
now the lane is harness end to end.

Wire contract implemented against ground truth, not the spec PDF: Corral's own
client (``corral/acp.py``) and the measured shapes in
``corral/spike/FINDINGS.md``:

  * ``initialize`` / ``session/new`` / ``session/load`` / ``session/list`` /
    ``session/prompt`` / ``session/set_config_option`` (requests),
    ``session/cancel`` (notification).
  * Turn output streams as ``session/update`` notifications
    (``agent_message_chunk``, ``tool_call``, ``tool_call_update``); the
    ``session/prompt`` response carries ``stopReason`` when the turn ends.
  * ``session/request_permission`` is a CLIENT-DIRECTED REQUEST: write_file
    and run_bash block until the human answers. PRINCIPLES 17: the request
    carries the exact bytes (``rawInput``) and a structured diff, so approval
    is consent, not presence. Unanswered/cancelled FAILS CLOSED (the tool
    does not run; the model is told "DENIED").
  * ``configOptions`` advertises the model picker (id ``model``), values from
    the node's live tags — never a hardcoded list.

Sessions persist under ``~/.local/state/cc/harness-acp/`` (one JSON per
session: cwd, model, history, title) so ``session/load`` survives the
one-process-per-pane lifecycle. History replay is deliberately NOT emitted on
load — the client holds the transcript (FINDINGS.md §3).

Offline test mode: ``HARNESS_ACP_FAKE=answer|tool`` swaps the model transport
for a deterministic script so ``selftest_acp.py`` exercises the whole wire —
including the permission round-trip — with no node and no model.

Launch (what Corral's AGENTS registry points at):
    /usr/bin/python3 -m harness.acp_server
"""
import json
import os
import sys
import threading
import time
import uuid
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from harness import loop as hloop
    from harness import tools_local
else:
    from . import loop as hloop
    from . import tools_local

STATE_DIR = Path(os.environ.get(
    "HARNESS_ACP_STATE", str(Path.home() / ".local/state/cc/harness-acp")))
PERMISSION_TIMEOUT = 3600      # a human may be away; bounded, then fail closed
MAX_LINE = 16 * 1024 * 1024    # mirror corral's own stdin bound
RISKY = {"write_file", "run_bash"}   # tools that need the human's yes
MAX_HISTORY_MSGS = 200         # per session; oldest turns dropped beyond this

SYSTEM = ("You are a capable local agent working inside the directory %s. "
          "Use the tools when they help; answer concisely. If a tool is "
          "DENIED by the operator, respect it and continue without it.")

# --------------------------------------------------------------------------- #
# Model BACKEND seam ($HARNESS_ACP_PROVIDER, default the sovereign .21 node).   #
#                                                                              #
# The harness is the loop, the tools, and the permission rail; WHICH model      #
# answers is a separate axis, and this server had the .21 node wired into two   #
# places (the picker's source and the transport). Naming the seam lets the      #
# identical rail — same exact-bytes diff, same fail-closed permission — front   #
# open-weight models the fleet cannot reach any other way, without a second     #
# copy of this file.                                                            #
#                                                                              #
# What a provider is: `models()` -> the picker's live values (never hardcoded), #
# `transport` -> the loop's injectable seam (None = loop's own Ollama default), #
# `data_class` -> the ceiling on what may be typed into a pane on this lane.    #
# The data class is a FACT ABOUT THE VENDOR, and its one authority is           #
# `_lib.merit_policy.CANDIDATES` — it is read from there, never restated here,  #
# because a second copy is exactly how the Grok ruling sat wrong for four days. #
# --------------------------------------------------------------------------- #
PROVIDER = os.environ.get("HARNESS_ACP_PROVIDER", "local").strip().lower()


def _data_class_for(target):
    """The ceiling for `target`, from merit_policy — the ONE authority.

    Fails CLOSED (PRINCIPLES 4): if the taxonomy cannot be read, or names a
    provider we do not know, the answer is the restrictive `internal`, never an
    optimistic `sensitive`.
    """
    try:
        from _lib import merit_policy
        # has_tools=False ON PURPOSE. eligible() answers two questions at once:
        # "may this data class reach this provider" (the trust axis) and "is
        # this provider any good at driving tools" (a routing-QUALITY opinion
        # about a raw API call). Only the first is a data-class ceiling's
        # business. Passing True silently demoted `local` -- the SOVEREIGN lane,
        # Ollama on Craig's own hardware, the one that still answers when the
        # WAN is down -- from sensitive to internal, killing it with a quality
        # opinion about a different code path. Measured 2026-09-11:
        # eligible("local", "sensitive", True) is False and
        # eligible("local", "sensitive", False) is True.
        #
        # roles.py hit this exact conflation on 2026-09-10 and carries the same
        # note (roles._data_class_ok). This copy never got it -- the second
        # authority that drifted from the first.
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
    # Tool-capable models only: every pane on this lane is handed write_file /
    # run_bash, and a model that cannot emit a tool_call would sit in the picker
    # looking usable while silently never being able to act. The provider
    # publishes the flag, so this is a filter on fact, not a guess.
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
        # loop.run's 180s default is sized for a local POST ("cold load ~20s").
        # Keep it: a local model silent for ten minutes is a real failure, and
        # masking it with a long timeout is the wrong direction.
        "call_timeout": None,
    },
    "fireworks": {
        "label": "Fireworks.ai",
        "models": _fireworks_models,
        "default": _fireworks_default,
        "transport": _fireworks_transport,
        "target": "fireworks",         # merit_policy key -> THIRD party
        "where": "on Fireworks",
        # HOSTED REASONING MODELS ARE SLOW BY DESIGN. glm-5p3 measured
        # 2026-09-13 at ~280s to produce 22,740 completion tokens (19,651 of
        # them reasoning). The 180s local default cut that off mid-generation,
        # turning the raised token ceiling into a timeout instead of an answer.
        # Sized to the token budget, not to a local node's cold load.
        "call_timeout": 900,
    },
    "deepseek": {
        # DeepSeek at DIRECT prices (2026-09-09): the same model costs ~2x
        # via Fireworks, so a DeepSeek pane must never ride that lane.
        "label": "DeepSeek (direct)",
        "models": _deepseek_models,
        "default": _deepseek_default,
        "transport": _deepseek_transport,
        "target": "deepseek",          # merit_policy key -> THIRD party
        "where": "on DeepSeek",
        # Same reason as Fireworks: V4 is hybrid-thinking and bills the CoT.
        "call_timeout": 900,
    },
}
if PROVIDER not in PROVIDERS:
    # Loud and fatal, not a silent fallback to local: a pane that quietly
    # answered from a different vendor than the lane promised would be the one
    # failure this seam must never have.
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
        """The vault reader — attached ONLY on a lane that may hold the vault.

        `~/notes` is Craig's own writing: the ranch, the family, the money, the
        client-adjacent thinking. A lane whose ceiling is `internal` may not
        carry it (P11), and until 2026-09-11 the ceiling was COMPUTED (line ~183)
        and REPORTED in serverInfo.dataClass and then not consulted here, so
        DATA_CLASS was a label rather than a gate.

        The reachable path, found by the 2026-09-11 bug bash (grok, CONFIRMED,
        re-read here): Library -> a vault note -> agent = Fireworks or DeepSeek
        -> "Open agent here". The pane's cwd is ~/notes, the opening prompt
        tells the model to read the file, and neither `search_notes` nor
        `read_note` is in RISKY -- so no permission card is ever raised and the
        bytes go to a third party. Roles could not save it: the data-class gate
        lives in roles.py and this path starts without a role.

        Fails closed with the rest: an unreadable taxonomy already resolves
        DATA_CLASS to `internal`, so it resolves to NO vault tools here.
        """
        if DATA_CLASS != "sensitive":
            return []
        try:
            import importlib.util
            p = Path(__file__).resolve().parent.parent / "wiki" / "ask_local.py"
            spec = importlib.util.spec_from_file_location("wiki_ask_local", p)
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            return mod._tools()
        except Exception:  # noqa: BLE001 — standalone installs have no vault
            return []

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

    def _permission(self, sess, tool_name, args, tc):
        """True iff the human allows this call. Absence of a yes is a no."""
        if tool_name in sess["allow_always"]:
            return True
        params = {
            "sessionId": sess["sessionId"],
            "options": [
                {"kind": "allow_once", "name": "Allow", "optionId": "allow"},
                {"kind": "allow_always",
                 "name": "Always allow %s" % tool_name,
                 "optionId": "allow_always"},
                {"kind": "reject_once", "name": "Reject", "optionId": "reject"},
            ],
            "toolCall": tc,
        }
        r = self._request("session/request_permission", params,
                          PERMISSION_TIMEOUT)
        outcome = ((r or {}).get("outcome") or {})
        if outcome.get("outcome") != "selected":
            return False                       # cancelled / timeout / malformed
        opt = outcome.get("optionId")
        if opt == "allow_always":
            sess["allow_always"].add(tool_name)
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
        # The provider is reported, not implied: two lanes now run this same
        # binary against different vendors with different data ceilings, and a
        # pane that cannot say which one it is would leave Craig guessing what
        # he may safely type into it.
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
        """Strip provider namespaces off a model id.

        Corral remembers a pane's last model per lane and re-applies it on
        create — and the retired opencode lane remembered ids like
        ``ollama/gemma4-e4b-agent-64k:latest``. Ollama's own API has no such
        namespace, so storing that verbatim 404'd every prompt (live,
        2026-08-03). The tag is the tag; prefixes are someone else's routing.
        """
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
            # Refuse loudly with the fix in the message — Corral renders this
            # as a note and keeps the current model, which is the safe default.
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
                answer, meta = hloop.run(
                    text, self._tools_for(sess), model=sess["model"],
                    system=SYSTEM % sess["cwd"], history=list(sess["history"]),
                    should_stop=sess["cancel"].is_set, transport=transport,
                    call_timeout=(SPEC.get("call_timeout")
                                  or hloop.DEFAULT_CALL_TIMEOUT))
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

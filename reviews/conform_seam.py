"""Cross-side seam conformance: OUR harness loop driven by THEIR model_shim
transport (dogma-2 ea0d7d0). Offline — the provider HTTP layer is stubbed.

The shim lives on dogma-2 and is NOT vendored here (one home per fact). Fetch a
copy, then point this at it:

    D=$(mktemp -d)
    ssh st21 'cat ~/ai-os/tools/local-model/model_shim.py' > $D/model_shim.py
    cd ~/Github/CC && SHIM_DIR=$D /usr/bin/python3 harness/reviews/conform_seam.py

Exit 0 = the shim conforms. Non-zero names every check that failed.
"""
import os, sys, json, tempfile, urllib.error
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))))
_shim_dir = os.environ.get("SHIM_DIR")
if not _shim_dir or not os.path.exists(os.path.join(_shim_dir, "model_shim.py")):
    sys.exit("set SHIM_DIR to a directory holding dogma-2's model_shim.py "
             "(see the docstring)")
sys.path.insert(0, _shim_dir)
from harness import loop, tools_local
import model_shim

FAILS = []
def ok(c, label):
    print(("PASS  " if c else "FAIL  ") + label)
    if not c: FAILS.append(label)

SEEN = []          # payloads that reached the wire
REPLIES = []       # canned /api/chat bodies, in order

def fake_http_json(url, body=None, timeout=300):
    if url.endswith("/api/ps"):
        return {"models": [{"name": "testmodel:latest"}]}
    SEEN.append(body)
    return REPLIES.pop(0)

model_shim.http_json = fake_http_json

def reset(replies):
    SEEN.clear(); REPLIES[:] = replies

# --- 1. end-to-end: tool turn then answer, through their transport ----------
reset([
    {"message": {"role": "assistant", "content": "",
                 "tool_calls": [{"function": {"name": "list_dir",
                                              "arguments": {"path": "."}}}]},
     "prompt_eval_count": 11, "eval_count": 7},
    {"message": {"role": "assistant", "content": "there is one entry"},
     "prompt_eval_count": 20, "eval_count": 5},
])
_work = tempfile.mkdtemp(prefix="seam-conform-")
open(os.path.join(_work, "one-entry.txt"), "w").close()
tools = tools_local.standard_tools(workdir=_work, bash=False)
ans, meta = loop.run("list the dir", tools, model="testmodel",
                     transport=model_shim.transport)
ok(ans.strip() == "there is one entry", "e2e: answer returned through their transport")
ok(meta["turns"] == 2 and [c[0] for c in meta["tool_calls"]] == ["list_dir"],
   "e2e: two turns, one tool call executed")
ok(meta["eval_count"] == 12 and meta["prompt_eval_count"] == 31,
   "e2e: token accounting survives their body reshaping")

# --- 2. transport evidence lands in meta (the seam agreement) ---------------
t = meta.get("transport") or {}
ok(t.get("backend") == "ollama", "evidence: backend preserved into meta")
ok(t.get("resident") == ["testmodel:latest"], "evidence: residency preserved")
ok(t.get("attributed") is True, "evidence: attribution (latest-suffix) correct")
ok(isinstance(t.get("latency_s"), float), "evidence: latency preserved")

# --- 3. what actually reached the provider ---------------------------------
first = SEEN[0]
ok(bool(first.get("tools")), "payload: tool specs survived the shim")
ok(first.get("think") is False, "payload: think=false survived (registry profile)")
ok(first.get("keep_alive"), "payload: keep_alive survived")

# The contract says transport DELIVERS our payload. Theirs re-derives it:
reset([{"message": {"role": "assistant", "content": "ok"}}])
loop.run("x", [], model="testmodel", transport=model_shim.transport,
         options={"num_predict": 64, "num_ctx": 4096, "temperature": 0.7})
sent = SEEN[0]["options"]
ok(sent.get("num_predict") == 64, "payload: harness options.num_predict delivered")
ok(sent.get("num_ctx") == 4096, "payload: harness options.num_ctx delivered")
ok(sent.get("temperature") == 0.7, "payload: harness options.temperature delivered")

# --- 4. provider failure fails closed through their typed error ------------
def boom(url, body=None, timeout=300):
    raise urllib.error.URLError("connection refused")
model_shim.http_json = boom
reset([])
try:
    loop.run("x", [], model="testmodel", transport=model_shim.transport)
    ok(False, "failure: provider error raised HarnessError")
except loop.HarnessError as e:
    ok("refused" in str(e) or "URLError" in str(e), "failure: provider error fails closed as HarnessError")
except Exception as e:
    ok(False, "failure: provider error fails closed (got %s)" % type(e).__name__)

print()
print("CONFORMANCE %s — %d check(s) failed" % ("FAIL" if FAILS else "PASS", len(FAILS)))
for f in FAILS: print("  - " + f)
sys.exit(1 if FAILS else 0)

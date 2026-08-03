"""tools_local — the confined default toolset for the agentic floor (stdlib-only).

Filesystem tools are JAILED to the workdir: every path resolves inside it or
the call refuses. ``run_bash`` is BOUNDED (cwd=workdir, per-command timeout,
capped output) but NOT jailed — bash can address any path the user can. That
is honest scope, not an oversight: the floor's lane is trusted local cron work
(AGENTIC_FAILOVER_SPEC §2), and callers wanting hard enforcement pass a
``gate`` to the loop (the Progent seam) or drop bash from the set with
``standard_tools(workdir, bash=False)``.
"""
import subprocess
from pathlib import Path

try:
    from .loop import Tool
except ImportError:
    from loop import Tool

MAX_READ_CHARS = 20000
BASH_TIMEOUT = 60
BASH_MAX_OUT = 8000


class ToolRefused(RuntimeError):
    """A tool refused its arguments (confinement, missing arg)."""


def _confine(workdir, path):
    """Resolve ``path`` inside ``workdir`` or refuse. '' / '.' mean workdir."""
    base = Path(workdir).resolve()
    target = (base / (path or ".")).resolve()
    if target != base and base not in target.parents:
        raise ToolRefused("path %r escapes the workdir jail" % path)
    return target


def _req(args, key):
    val = args.get(key)
    if val is None or (isinstance(val, str) and not val.strip()):
        raise ToolRefused("missing required argument %r" % key)
    return val


def standard_tools(workdir, bash=True):
    """The default floor toolset, confined to ``workdir``."""
    base = Path(workdir).resolve()
    if not base.is_dir():
        raise ToolRefused("workdir %s is not a directory" % workdir)

    def read_file(args):
        p = _confine(base, _req(args, "path"))
        text = p.read_text(encoding="utf-8", errors="replace")
        if len(text) > MAX_READ_CHARS:
            return text[:MAX_READ_CHARS] + "\n…[truncated %d chars]" % (
                len(text) - MAX_READ_CHARS)
        return text

    def write_file(args):
        p = _confine(base, _req(args, "path"))
        content = args.get("content", "")
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(str(content), encoding="utf-8")
        return "wrote %d bytes to %s" % (len(str(content)), p.relative_to(base))

    def list_dir(args):
        p = _confine(base, args.get("path", "."))
        entries = sorted(p.iterdir())
        if not entries:
            return "(empty)"
        return "\n".join(e.name + ("/" if e.is_dir() else "")
                         for e in entries[:200])

    def run_bash(args):
        cmd = _req(args, "command")
        try:
            cp = subprocess.run(["bash", "-c", cmd], cwd=str(base),
                                capture_output=True, text=True,
                                timeout=BASH_TIMEOUT)
        except subprocess.TimeoutExpired:
            return "ERROR: command exceeded %ds timeout" % BASH_TIMEOUT
        out = (cp.stdout or "") + (("\n[stderr]\n" + cp.stderr) if cp.stderr else "")
        out = out.strip() or "(no output)"
        if len(out) > BASH_MAX_OUT:
            out = out[:BASH_MAX_OUT] + "\n…[truncated]"
        return "exit=%d\n%s" % (cp.returncode, out)

    S = lambda d: {"type": "string", "description": d}  # noqa: E731
    tools = [
        Tool("read_file", "Read a text file inside the workdir",
             {"type": "object", "properties": {"path": S("relative path")},
              "required": ["path"]}, read_file),
        Tool("write_file", "Write/overwrite a text file inside the workdir",
             {"type": "object", "properties": {
                 "path": S("relative path"), "content": S("file content")},
              "required": ["path", "content"]}, write_file),
        Tool("list_dir", "List a directory inside the workdir",
             {"type": "object", "properties": {"path": S("relative path, default .")}},
             list_dir),
    ]
    if bash:
        tools.append(Tool(
            "run_bash",
            "Run one bash command (cwd=workdir, %ds timeout, output capped)" % BASH_TIMEOUT,
            {"type": "object", "properties": {"command": S("bash command")},
             "required": ["command"]}, run_bash))
    return tools

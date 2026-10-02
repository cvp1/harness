"""policy — the harness's default tool gate; ``loop.run(gate=None)`` uses DEFAULT.

Lookup order: caller-declared tools (may never replace a built-in), built-in
path/content checks for read_file/write_file/list_dir, run_bash denied unless
declared via :func:`bash_policy`, unknown tools fall through to
``_lib.policy_gate`` when importable and otherwise deny.

    from harness import policy
    gate = policy.gate({"run_bash": policy.bash_policy([r"wc -l [\\w./-]+"])})
    answer, meta = loop.run(task, tools, gate=gate)
"""
import os
import re
import unicodedata
from types import MappingProxyType

MAX_PATH_CHARS = 4096
MAX_WRITE_CHARS = 200_000
MAX_BASH_CHARS = 4000
MAX_STRING_ARG = 4096

# Shell syntax an allowlist regex must not swallow (newline is a control char).
SHELL_META = ";|&$`()<>"

# Sentinel: allow any single-line bash command; only for human-driven lanes.
BASH_ANY = object()


# ------------------------------------------------------------- primitives ---
def _has_ctrl(val):
    """True if ``val`` carries any control or Unicode line/paragraph separator."""
    for c in val:
        if unicodedata.category(c) in ("Cc", "Zl", "Zp"):
            return True
    return False


def _bad_text(val, cap, what):
    """Return a denial reason for a string arg, or None when it is clean."""
    if not isinstance(val, str):
        return "%s must be a string, got %s" % (what, type(val).__name__)
    if len(val) > cap:
        return "%s is %d chars, cap %d" % (what, len(val), cap)
    if _has_ctrl(val):
        return "%s carries a control character" % what
    return None


# Path segments write_file may never touch: the policy's own code and repo plumbing.
WRITE_DENY_SEGMENTS = frozenset({"harness", "_lib", ".git", ".claude"})


def _check_path(args, required=True):
    path = args.get("path")
    if path is None:
        return (False, "path is required") if required else (True, "ok")
    # Shape check runs before the empty-means-default shortcut.
    why = _bad_text(path, MAX_PATH_CHARS, "path")
    if why:
        return False, why
    if not path.strip():
        return (False, "path is required") if required else (True, "ok")
    if path != path.strip():
        return False, "path has leading/trailing whitespace"
    if "\\" in path:
        return False, "path may not contain a backslash"
    if path.startswith("/") or path.startswith("~") or re.match(r"^[A-Za-z]:", path):
        return False, "path must be relative to the workdir"
    parts = path.split("/")
    if ".." in parts or ".." in os.path.normpath(path).split("/"):
        return False, "path may not climb (.. segment)"
    return True, "ok"


def _check_read_file(args):
    return _check_path(args, required=True)


def _check_list_dir(args):
    return _check_path(args, required=False)


def _check_write_file(args):
    ok, why = _check_path(args, required=True)
    if not ok:
        return ok, why
    hit = WRITE_DENY_SEGMENTS & set(os.path.normpath(args["path"]).split("/"))
    if hit:
        return False, ("write_file may not touch %s — that is the policy's own "
                       "code or the repo's plumbing" % ", ".join(sorted(hit)))
    content = args.get("content", "")
    if not isinstance(content, str):
        return False, "content must be a string, got %s" % type(content).__name__
    if len(content) > MAX_WRITE_CHARS:
        return False, "content is %d chars, cap %d" % (len(content), MAX_WRITE_CHARS)
    return True, "ok"


def _deny_bash_default(args):
    return False, ("run_bash has no standing policy — the caller must declare "
                   "one with harness.policy.bash_policy(allow=[...]) or "
                   "bash_policy(BASH_ANY) for a human-driven lane")


_BUILTIN = MappingProxyType({
    "read_file": _check_read_file,
    "write_file": _check_write_file,
    "list_dir": _check_list_dir,
    "run_bash": _deny_bash_default,
})


# ------------------------------------------------------------- composers ---
def bash_policy(allow, hint="", shell_syntax=False):
    """A ``run_bash`` predicate: shape checks, then a whole-command allowlist.

    ``allow`` is a sequence of regex patterns (str or compiled) matched with
    ``fullmatch`` against the whitespace-normalised command, or
    :data:`BASH_ANY`. With an allowlist, any of :data:`SHELL_META` in the
    command is refused unless ``shell_syntax=True`` — set that only when every
    pattern is a tight literal that spells its own metacharacters out.
    ``hint`` is appended to a denial so the model can retry inside its lane.
    """
    if allow is BASH_ANY:
        pats = None
    else:
        pats = [p if hasattr(p, "fullmatch") else re.compile(p) for p in allow]
        if not pats:
            raise ValueError("bash_policy: an empty allowlist denies everything "
                             "— say so with the default instead")

    def check(args):
        raw = args.get("command")
        why = _bad_text(raw, MAX_BASH_CHARS, "command")
        if why:
            return False, why
        if not raw.strip():
            return False, "command is empty"
        if pats is None:
            return True, "ok"
        if not shell_syntax:
            hit = [c for c in SHELL_META if c in raw]
            if hit:
                return False, ("shell syntax %s is not permitted in this lane"
                               % "".join(hit))
        # Refuse non-ASCII spaces: bash reads them as part of a word, so a
        # normalised command would not be the one executing.
        if any(unicodedata.category(c) == "Zs" and c != " " for c in raw):
            return False, "command carries a non-ASCII space"
        cmd = re.sub(r" +", " ", raw.strip())
        if any(p.fullmatch(cmd) for p in pats):
            return True, "ok"
        return False, ("not in the caller's bash allowlist%s: %.120s"
                       % ((" — " + hint) if hint else "", cmd))

    check._harness_bash_policy = True
    return check


def string_args(required=(), optional=(), max_len=MAX_STRING_ARG, match=None):
    """A predicate for a simple tool: the named args must be clean strings.

    Unknown arg keys are denied — a tool's schema is closed, and an extra key
    is how a recipient or a target gets smuggled in beside the real fields.
    ``match`` maps an arg name to a regex the whole value must ``fullmatch``.
    """
    req, opt = tuple(required), tuple(optional)
    known = set(req) | set(opt)
    pats = {k: (p if hasattr(p, "fullmatch") else re.compile(p))
            for k, p in (match or {}).items()}

    def check(args):
        extra = set(args) - known
        if extra:
            return False, "unexpected argument(s): %s" % ", ".join(sorted(extra))
        for k in req:
            v = args.get(k)
            if v is None or (isinstance(v, str) and not v.strip()):
                return False, "%s is required" % k
        for k in known:
            if k in args and args[k] is not None:
                why = _bad_text(args[k], max_len, k)
                if why:
                    return False, why
                p = pats.get(k)
                if p is not None and not p.fullmatch(args[k]):
                    return False, "%s does not match the tool's shape" % k
        return True, "ok"

    return check


def relative_path(required=True):
    """The built-in path predicate, for a declared tool whose one argument is
    a workdir-relative ``path`` (e.g. a vault reader)."""
    def check(args):
        extra = set(args) - {"path"}
        if extra:
            return False, "unexpected argument(s): %s" % ", ".join(sorted(extra))
        return _check_path(args, required=required)
    return check


def _spine_check():
    """``_lib.policy_gate.check`` when the spine is present, else None."""
    try:
        from _lib import policy_gate
    except ImportError:
        return None
    return policy_gate.check


def gate(declare=None):
    """Build the gate callable ``(tool_name, args) -> (bool, reason)``.

    Raises ``ValueError`` at construction for a declaration that would weaken
    a built-in policy.
    """
    declared = dict(declare or {})
    for name, fn in declared.items():
        if name in _BUILTIN and name != "run_bash":
            raise ValueError("policy.gate: %r has a built-in policy that a "
                             "caller may not replace" % name)
        if name == "run_bash" and not getattr(fn, "_harness_bash_policy", False):
            raise ValueError("policy.gate: a run_bash policy must be built by "
                             "harness.policy.bash_policy(...)")
        if not callable(fn):
            raise ValueError("policy.gate: policy for %r is not callable" % name)
    state = {"spine": _spine_check()}

    def check(tool, args):
        args = dict(args or {})
        fn = declared.get(tool) or _BUILTIN.get(tool)
        if fn is not None:
            try:
                ok, why = fn(args)
            except Exception as e:  # noqa: BLE001 — a broken rule denies
                return False, "policy error evaluating %r: %s" % (tool, e)
            return bool(ok), why
        if state["spine"] is None:
            # Re-discover the spine on demand; still absent means deny.
            state["spine"] = _spine_check()
        if state["spine"] is not None:
            d = state["spine"](tool, args)
            return bool(d.allowed), d.reason
        return False, "unknown tool %r — no policy, fail closed" % tool

    check.declared = frozenset(declared)
    return check


DEFAULT = gate()

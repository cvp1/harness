"""registry — model-tag → dialect profile, held as DATA (stdlib-only).

Principle 10: let data, not code, hold the assignment. ``registry.json`` maps
fnmatch patterns over Ollama model tags to a dialect + per-call options; the
loop consults ``profile_for()`` and never special-cases a model family in code.

Entries carry a ``status``: ``proven`` cites the measurement that earned the
assignment; ``assumed`` means nobody has gated it yet — run
``python -m harness --probe <model>`` before trusting one.
"""
import fnmatch
import json
from pathlib import Path

REGISTRY_PATH = Path(__file__).resolve().parent / "registry.json"


def load(path=None):
    with open(path or REGISTRY_PATH, encoding="utf-8") as fh:
        return json.load(fh)


def profile_for(model, path=None):
    """Return ``{"dialect", "options", "status", ...}`` for a model tag.

    First matching pattern wins; no match falls back to the registry default
    (native, think=false) — the universal decoder makes that a safe default,
    and the probe exists to earn a better one.
    """
    reg = load(path)
    for entry in reg.get("models", []):
        if fnmatch.fnmatch(model or "", entry["match"]):
            return dict(entry)
    prof = dict(reg["default"])
    prof.setdefault("status", "default")
    return prof

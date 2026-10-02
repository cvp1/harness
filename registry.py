"""registry — map Ollama model tags (fnmatch patterns in registry.json) to a dialect profile."""
import fnmatch
import json
from pathlib import Path

REGISTRY_PATH = Path(__file__).resolve().parent / "registry.json"


def load(path=None):
    with open(path or REGISTRY_PATH, encoding="utf-8") as fh:
        return json.load(fh)


def profile_for(model, path=None):
    """Return the profile dict for a model tag; first match wins, else the registry default."""
    reg = load(path)
    for entry in reg.get("models", []):
        if fnmatch.fnmatch(model or "", entry["match"]):
            return dict(entry)
    prof = dict(reg["default"])
    prof.setdefault("status", "default")
    return prof

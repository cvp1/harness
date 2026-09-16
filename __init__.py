"""harness — the fleet's own agentic loop + model translation layer.

We ARE the harness: canonical tools/messages in, any local model's dialect
translated by ``dialects``, bounded execution by ``loop``. See DESIGN.md.
"""
from .loop import Tool, HarnessError, run, run_agentic  # noqa: F401
from . import policy  # noqa: F401  — the default gate; compose with policy.gate()

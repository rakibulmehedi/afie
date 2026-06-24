"""
Model routing and configuration for the CSPE synthesis package.

NOTE: Claude-vs-Gemini routing policy is unspecified in the blueprint
(task_ledger.md S2.6 GAP). Defaulting to Anthropic Haiku for cost/speed.
Override via SYNTHESIS_MODEL env var for future policy changes.
"""

from __future__ import annotations

import os

_DEFAULT_MODEL = "claude-haiku-4-5-20251001"


def get_synthesis_model() -> str:
    """
    Read SYNTHESIS_MODEL env var.
    Default: "claude-haiku-4-5-20251001"
    """
    return os.environ.get("SYNTHESIS_MODEL", _DEFAULT_MODEL)


def is_anthropic_model(model: str) -> bool:
    """Return True if model name starts with 'claude'."""
    return model.startswith("claude")


def is_gemini_model(model: str) -> bool:
    """Return True if model name starts with 'gemini'."""
    return model.startswith("gemini")

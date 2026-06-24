from __future__ import annotations

import json
import re
from typing import Final

MAX_OUTPUT_CHARS: Final[int] = 2000

_URL_RE = re.compile(r"https?://\S+", re.IGNORECASE)
_CODE_BLOCK_RE = re.compile(r"```.*?```", re.DOTALL)
_CONTROL_RE = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")  # keep \t \n \r
_INJECTION_RE = re.compile(
    r"ignore\s+all\s+(previous|prior)\s+instructions?"
    r"|disregard\s+(all\s+)?(previous|prior|system)\s+instructions?"
    r"|forget\s+(all\s+)?previous\s+instructions?",
    re.IGNORECASE,
)


def sanitize_payload(source: str, raw_payload: str) -> str:
    """Extract only allowlisted fields from raw_payload (JSON string),
    strip URLs/code-blocks/control chars, and return a flat inert string.

    Raises ValueError for unknown source.
    Returns a safe fallback string on malformed JSON.

    # S2.8: raw_payload is untrusted user-tier input.
    # Allowlisted fields only — anything not explicitly extracted is discarded.
    # Never follows URLs. Output is a flat string, never structured data.
    """
    try:
        data: dict[str, object] = json.loads(raw_payload)
    except (json.JSONDecodeError, ValueError):
        # Sanitize source to prevent log injection (valid values only)
        safe_source = source if source in {"github", "telegram"} else "unknown"
        return f"[{safe_source}] payload parse error — content withheld"

    if source == "github":
        return _sanitize_github(data)
    elif source == "telegram":
        return _sanitize_telegram(data)
    else:
        raise ValueError(f"Unknown source: {source!r}")


def _sanitize_github(data: dict[str, object]) -> str:
    repo_raw = data.get("repository", {})
    repo = str(repo_raw.get("full_name", "unknown") if isinstance(repo_raw, dict) else "unknown")

    pusher_raw = data.get("pusher", {})
    pusher = str(pusher_raw.get("name", "unknown") if isinstance(pusher_raw, dict) else "unknown")

    commits_raw = data.get("commits", [])
    commits = commits_raw if isinstance(commits_raw, list) else []
    messages = [
        str(c.get("message", "") if isinstance(c, dict) else "")
        for c in commits
    ][:5]

    parts = ["GitHub Event", f"Repo: {_clean(repo)}", f"Pusher: {_clean(pusher)}"]
    for i, msg in enumerate(messages, 1):
        parts.append(f"Commit {i}: {_clean(msg)}")
    return " | ".join(parts)[:MAX_OUTPUT_CHARS]


def _sanitize_telegram(data: dict[str, object]) -> str:
    msg_raw = data.get("message", {})
    text = str(msg_raw.get("text", "") if isinstance(msg_raw, dict) else "")
    if text.startswith("/"):
        text = text.split(None, 1)[1] if " " in text else ""
    return f"Telegram Message: {_clean(text)}"[:MAX_OUTPUT_CHARS]


def _clean(text: str) -> str:
    """Remove URLs, code blocks, control chars, and injection trigger phrases."""
    text = _CODE_BLOCK_RE.sub("[code block removed]", text)
    text = _URL_RE.sub("[url removed]", text)
    text = _INJECTION_RE.sub("[injection attempt removed]", text)
    text = _CONTROL_RE.sub(" ", text)
    return text.strip()


class PayloadGuard:
    def sanitize(self, source: str, raw_payload: str) -> str:
        return sanitize_payload(source, raw_payload)

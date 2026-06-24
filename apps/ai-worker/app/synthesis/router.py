"""
Async LLM caller with timeout and retry support for the CSPE synthesis package.

IMPORTANT SECURITY NOTE:
# TODO S2.8: raw_payload is untrusted user-tier input. S2.8 (payload_guard.py)
# must sanitize this before production use. For now, pass as-is but NEVER
# include it in the system_prompt. Treat as user message only.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Final

from app.synthesis.frameworks import is_anthropic_model, is_gemini_model

logger = logging.getLogger(__name__)

SYNTHESIS_TIMEOUT_SECONDS: Final[int] = 60


class SynthesisTimeoutError(Exception):
    """Raised when LLM call exceeds SYNTHESIS_TIMEOUT_SECONDS."""


class SynthesisError(Exception):
    """Raised when LLM call fails for non-timeout reasons."""


async def _call_anthropic(system_prompt: str, raw_payload: str, model: str) -> str:
    """Call Anthropic API and return text response."""
    import anthropic

    client = anthropic.AsyncAnthropic()
    response = await client.messages.create(
        model=model,
        max_tokens=2048,
        system=system_prompt,
        messages=[{"role": "user", "content": raw_payload}],
    )
    return str(response.content[0].text)  # type: ignore[union-attr]


async def _call_gemini(system_prompt: str, raw_payload: str, model: str) -> str:
    """Call Gemini API and return text response."""
    import google.genai as genai

    client = genai.Client()
    response = await client.aio.models.generate_content(
        model=model,
        contents=raw_payload,
        config={"system_instruction": system_prompt, "max_output_tokens": 2048},
    )
    return str(response.text)


async def synthesize(
    system_prompt: str,
    raw_payload: str,
    model: str,
) -> str:
    """
    Call the LLM (Anthropic or Gemini based on model name) with:
    - system_prompt: built from tenant blueprint (trusted)
    - raw_payload: the raw webhook payload as user message (UNTRUSTED)

    IMPORTANT SECURITY NOTE:
    # TODO S2.8: raw_payload is untrusted user-tier input. S2.8 (payload_guard.py)
    # must sanitize this before production use. For now, pass as-is but NEVER
    # include it in the system_prompt. Treat as user message only.

    Timeout: SYNTHESIS_TIMEOUT_SECONDS (60s) via asyncio.wait_for.
    Raises SynthesisTimeoutError on timeout.
    Raises SynthesisError on other LLM failures.
    Returns the LLM text response string.
    """
    if is_anthropic_model(model):
        coro = _call_anthropic(system_prompt, raw_payload, model)
    elif is_gemini_model(model):
        coro = _call_gemini(system_prompt, raw_payload, model)
    else:
        raise SynthesisError(f"Unknown model prefix: {model}")

    try:
        result: str = await asyncio.wait_for(coro, timeout=SYNTHESIS_TIMEOUT_SECONDS)
        logger.info("Synthesis completed successfully with model %s", model)
        return result
    except asyncio.TimeoutError as exc:
        logger.warning("Synthesis timed out after %ds with model %s", SYNTHESIS_TIMEOUT_SECONDS, model)
        raise SynthesisTimeoutError(
            f"LLM call exceeded {SYNTHESIS_TIMEOUT_SECONDS}s timeout"
        ) from exc
    except Exception as exc:
        logger.error("Synthesis failed with model %s: %s", model, exc)
        raise SynthesisError(str(exc)) from exc

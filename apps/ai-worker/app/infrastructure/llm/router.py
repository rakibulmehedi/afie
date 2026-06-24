from __future__ import annotations

import asyncio
from typing import Final

from app.infrastructure.llm.frameworks import is_anthropic_model, is_gemini_model
from app.infrastructure.llm.anthropic_adapter import AnthropicAdapter
from app.infrastructure.llm.gemini_adapter import GeminiAdapter
from app.synthesis.router import SynthesisError, SynthesisTimeoutError  # re-export for consumers

SYNTHESIS_TIMEOUT_SECONDS: Final[int] = 60

__all__ = ["SynthesisAdapter", "SynthesisError", "SynthesisTimeoutError"]


class SynthesisAdapter:
    async def synthesize(self, system_prompt: str, sanitized_payload: str, model: str) -> str:
        if is_anthropic_model(model):
            adapter: AnthropicAdapter | GeminiAdapter = AnthropicAdapter()
        elif is_gemini_model(model):
            adapter = GeminiAdapter()
        else:
            raise SynthesisError(f"Unknown model prefix: {model}")
        try:
            return await asyncio.wait_for(
                adapter.synthesize(system_prompt, sanitized_payload, model),
                timeout=SYNTHESIS_TIMEOUT_SECONDS,
            )
        except asyncio.TimeoutError as exc:
            raise SynthesisTimeoutError(f"LLM exceeded {SYNTHESIS_TIMEOUT_SECONDS}s") from exc
        except (SynthesisError, SynthesisTimeoutError):
            raise
        except Exception as exc:
            raise SynthesisError(str(exc)) from exc

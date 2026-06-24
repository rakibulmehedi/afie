from __future__ import annotations

from app.synthesis.router import _call_anthropic


class AnthropicAdapter:
    async def synthesize(self, system_prompt: str, sanitized_payload: str, model: str) -> str:
        return await _call_anthropic(system_prompt, sanitized_payload, model)

from __future__ import annotations

from app.synthesis.router import _call_gemini


class GeminiAdapter:
    async def synthesize(self, system_prompt: str, sanitized_payload: str, model: str) -> str:
        return await _call_gemini(system_prompt, sanitized_payload, model)

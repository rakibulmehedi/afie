"""
Tests for the CSPE (Content Synthesis and Persona Engine) synthesis package.

Tests mock the LLM SDKs — no real API calls are made.
"""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.synthesis.frameworks import (
    get_synthesis_model,
    is_anthropic_model,
    is_gemini_model,
)
from app.synthesis.persona import build_system_prompt
from app.synthesis.router import (
    SynthesisError,
    SynthesisTimeoutError,
    synthesize,
)

# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------

_SAMPLE_BLUEPRINT: dict[str, Any] = {
    "persona": "Empire Founder",
    "persona_version": "1.0",
    "cognitive_state": {
        "core_philosophy": "Build empire through relentless execution and leverage.",
        "axioms": [
            "Engineering Rigor",
            "Physical Contrast",
            "Business Translation",
            "Signal-to-Noise",
        ],
        "frameworks": [
            "First Principles Thinking",
            "80/20 Leverage Framework",
        ],
        "lexical_rules": {
            "avoid": ["synergy", "pivot"],
            "prefer": ["leverage", "execute"],
        },
    },
}


# ===========================================================================
# Tests for persona.py — build_system_prompt
# ===========================================================================


class TestBuildSystemPrompt:
    """Tests for build_system_prompt function."""

    def test_build_system_prompt_includes_philosophy(self) -> None:
        """core_philosophy must appear in the generated prompt."""
        prompt = build_system_prompt(_SAMPLE_BLUEPRINT)
        assert "Build empire through relentless execution and leverage." in prompt

    def test_build_system_prompt_includes_axioms(self) -> None:
        """All 4 axioms must appear in the generated prompt."""
        prompt = build_system_prompt(_SAMPLE_BLUEPRINT)
        for axiom in [
            "Engineering Rigor",
            "Physical Contrast",
            "Business Translation",
            "Signal-to-Noise",
        ]:
            assert axiom in prompt, f"Expected axiom '{axiom}' not found in prompt"

    def test_build_system_prompt_includes_frameworks(self) -> None:
        """Active frameworks must be listed in the generated prompt."""
        prompt = build_system_prompt(_SAMPLE_BLUEPRINT)
        assert "First Principles Thinking" in prompt
        assert "80/20 Leverage Framework" in prompt

    def test_build_system_prompt_includes_persona_and_version(self) -> None:
        """Persona name and version must appear in the prompt header."""
        prompt = build_system_prompt(_SAMPLE_BLUEPRINT)
        assert "Empire Founder" in prompt
        assert "1.0" in prompt

    def test_build_system_prompt_includes_lexical_rules(self) -> None:
        """Lexical avoid/prefer terms must be in the prompt when present."""
        prompt = build_system_prompt(_SAMPLE_BLUEPRINT)
        assert "synergy" in prompt
        assert "pivot" in prompt
        assert "leverage" in prompt
        assert "execute" in prompt

    def test_build_system_prompt_empty_lexical_rules(self) -> None:
        """Handles missing/empty lexical_rules gracefully without errors."""
        blueprint: dict[str, Any] = {
            "persona": "Minimal Persona",
            "persona_version": "0.1",
            "cognitive_state": {
                "core_philosophy": "Keep it simple.",
                "axioms": ["Simplicity"],
                "frameworks": ["KISS"],
                # lexical_rules deliberately omitted
            },
        }
        prompt = build_system_prompt(blueprint)
        assert "Minimal Persona" in prompt
        assert "Keep it simple." in prompt
        assert "Simplicity" in prompt
        assert "KISS" in prompt
        # Should not crash or include lexical section header
        assert "Lexical Rules" not in prompt

    def test_build_system_prompt_empty_cognitive_state(self) -> None:
        """Handles empty cognitive_state dict gracefully."""
        blueprint: dict[str, Any] = {
            "persona": "Ghost",
            "persona_version": "0.0",
            "cognitive_state": {},
        }
        prompt = build_system_prompt(blueprint)
        assert "Ghost" in prompt

    def test_build_system_prompt_lexical_rules_empty_lists(self) -> None:
        """Handles lexical_rules with empty avoid/prefer lists without showing the section."""
        blueprint: dict[str, Any] = {
            "persona": "Silent",
            "persona_version": "1.0",
            "cognitive_state": {
                "core_philosophy": "Say nothing.",
                "axioms": ["Silence"],
                "frameworks": [],
                "lexical_rules": {"avoid": [], "prefer": []},
            },
        }
        prompt = build_system_prompt(blueprint)
        assert "Lexical Rules" not in prompt


# ===========================================================================
# Tests for frameworks.py
# ===========================================================================


class TestFrameworks:
    """Tests for model routing helper functions."""

    def test_default_model_is_haiku(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """When SYNTHESIS_MODEL env var is absent, default to claude-haiku-4-5-20251001."""
        monkeypatch.delenv("SYNTHESIS_MODEL", raising=False)
        model = get_synthesis_model()
        assert model == "claude-haiku-4-5-20251001"

    def test_env_var_overrides_model(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """SYNTHESIS_MODEL env var overrides the default model."""
        monkeypatch.setenv("SYNTHESIS_MODEL", "gemini-1.5-pro")
        model = get_synthesis_model()
        assert model == "gemini-1.5-pro"

    def test_is_anthropic_model_true(self) -> None:
        """Model names starting with 'claude' are identified as Anthropic."""
        assert is_anthropic_model("claude-haiku-4-5-20251001") is True
        assert is_anthropic_model("claude-sonnet-4-6") is True

    def test_is_anthropic_model_false(self) -> None:
        """Non-Claude model names return False for is_anthropic_model."""
        assert is_anthropic_model("gemini-1.5-pro") is False
        assert is_anthropic_model("gpt-4o") is False

    def test_is_gemini_model_true(self) -> None:
        """Model names starting with 'gemini' are identified as Gemini."""
        assert is_gemini_model("gemini-1.5-pro") is True
        assert is_gemini_model("gemini-flash-002") is True

    def test_is_gemini_model_false(self) -> None:
        """Non-Gemini model names return False for is_gemini_model."""
        assert is_gemini_model("claude-haiku-4-5-20251001") is False
        assert is_gemini_model("gpt-4o") is False


# ===========================================================================
# Tests for router.py — synthesize
# ===========================================================================


class TestSynthesize:
    """Tests for the synthesize async function."""

    async def test_synthesize_anthropic_success(self) -> None:
        """Mock AsyncAnthropic to return a fake response; synthesize returns the text."""
        mock_response = MagicMock()
        mock_response.content = [MagicMock(text="synthesized content")]

        mock_create = AsyncMock(return_value=mock_response)
        mock_messages = MagicMock()
        mock_messages.create = mock_create

        mock_client_instance = MagicMock()
        mock_client_instance.messages = mock_messages

        mock_async_anthropic = MagicMock(return_value=mock_client_instance)

        with patch("anthropic.AsyncAnthropic", mock_async_anthropic):
            result = await synthesize(
                system_prompt="You are a helpful assistant.",
                raw_payload='{"event": "push"}',
                model="claude-haiku-4-5-20251001",
            )

        assert result == "synthesized content"
        mock_create.assert_awaited_once()
        call_kwargs = mock_create.call_args.kwargs
        assert call_kwargs["model"] == "claude-haiku-4-5-20251001"
        assert call_kwargs["max_tokens"] == 2048
        assert call_kwargs["system"] == "You are a helpful assistant."
        assert call_kwargs["messages"] == [
            {"role": "user", "content": '{"event": "push"}'}
        ]

    async def test_synthesize_timeout_raises_synthesis_timeout_error(self) -> None:
        """wait_for raising asyncio.TimeoutError → SynthesisTimeoutError is raised."""
        # Patch wait_for at the router module level so no coroutine is left unawaited.
        with patch(
            "app.synthesis.router.asyncio.wait_for",
            new=AsyncMock(side_effect=asyncio.TimeoutError()),
        ):
            with pytest.raises(SynthesisTimeoutError):
                await synthesize(
                    system_prompt="Test",
                    raw_payload="payload",
                    model="claude-haiku-4-5-20251001",
                )

    async def test_synthesize_unknown_model_raises_synthesis_error(self) -> None:
        """Model with unknown prefix raises SynthesisError immediately."""
        with pytest.raises(SynthesisError, match="Unknown model prefix: unknown-xyz"):
            await synthesize(
                system_prompt="Test",
                raw_payload="payload",
                model="unknown-xyz",
            )

    async def test_synthesize_anthropic_error_raises_synthesis_error(self) -> None:
        """SDK raising a generic exception is wrapped in SynthesisError."""
        # Patch wait_for at the router level so the exception propagates cleanly
        # without leaving an unawaited coroutine behind.
        with patch(
            "app.synthesis.router.asyncio.wait_for",
            new=AsyncMock(side_effect=RuntimeError("API rate limit exceeded")),
        ):
            with pytest.raises(SynthesisError, match="API rate limit exceeded"):
                await synthesize(
                    system_prompt="Test",
                    raw_payload="payload",
                    model="claude-haiku-4-5-20251001",
                )

    async def test_synthesize_gemini_success(self) -> None:
        """Mock Gemini client to return a fake response; synthesize returns the text."""
        mock_response = MagicMock()
        mock_response.text = "gemini synthesized content"

        mock_generate = AsyncMock(return_value=mock_response)
        mock_models = MagicMock()
        mock_models.generate_content = mock_generate

        mock_aio = MagicMock()
        mock_aio.models = mock_models

        mock_client_instance = MagicMock()
        mock_client_instance.aio = mock_aio

        mock_genai_client = MagicMock(return_value=mock_client_instance)

        with patch("google.genai.Client", mock_genai_client):
            result = await synthesize(
                system_prompt="You are a Gemini assistant.",
                raw_payload='{"event": "push"}',
                model="gemini-1.5-pro",
            )

        assert result == "gemini synthesized content"

    async def test_synthesize_gemini_error_raises_synthesis_error(self) -> None:
        """Gemini SDK raising a generic exception is wrapped in SynthesisError."""
        mock_generate = AsyncMock(side_effect=ConnectionError("Gemini unreachable"))
        mock_models = MagicMock()
        mock_models.generate_content = mock_generate

        mock_aio = MagicMock()
        mock_aio.models = mock_models

        mock_client_instance = MagicMock()
        mock_client_instance.aio = mock_aio

        mock_genai_client = MagicMock(return_value=mock_client_instance)

        with patch("google.genai.Client", mock_genai_client):
            with pytest.raises(SynthesisError, match="Gemini unreachable"):
                await synthesize(
                    system_prompt="Test",
                    raw_payload="payload",
                    model="gemini-1.5-pro",
                )

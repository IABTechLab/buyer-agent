# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""LLM construction shared by all agents.

The named providers (Anthropic, OpenAI, Gemini, Bedrock) are switched by
setting DEFAULT_LLM_MODEL / MANAGER_LLM_MODEL to a "<provider>/<model>"
string plus that provider's API key, per docs/guides/configuration.md — no
code here is involved in that path.

This module adds one more alternative: any OpenAI-wire-compatible endpoint
that has no CrewAI native provider prefix of its own (NVIDIA NIM, Ollama,
HuggingFace TGI, vLLM, ...). Setting OPENAI_COMPATIBLE_LLM_API_BASE_URL pins
the request to CrewAI's native OpenAI-compatible client regardless of the
model id's shape, using the raw model id the endpoint expects for
DEFAULT_LLM_MODEL / MANAGER_LLM_MODEL. Leaving
OPENAI_COMPATIBLE_LLM_API_BASE_URL unset keeps today's behavior exactly as-is.
"""

from typing import Any

from crewai import LLM

from ..config import get_settings

# CrewAI's native OpenAI SDK client; with a base_url it drives any endpoint
# that speaks the OpenAI wire format (NVIDIA NIM, Ollama, HuggingFace TGI, ...).
_OPENAI_COMPATIBLE_PROVIDER = "openai"

# CrewAI's native Anthropic SDK client; with a base_url it drives any endpoint
# that speaks the Anthropic Messages wire format (Amazon Bedrock's /anthropic
# route, a self-hosted Anthropic-compatible gateway, ...).
_ANTHROPIC_COMPATIBLE_PROVIDER = "anthropic"

# Anthropic removed sampling parameters (temperature/top_p/top_k) starting
# with Opus 4.7; sending temperature to these families returns
# "400 invalid_request_error: 'temperature' is deprecated for this model."
# Matched case-insensitively as substrings of the model id so provider
# prefixes ("anthropic/", "bedrock/us.anthropic.", ...) and version/date
# suffixes are covered. Kept deliberately conservative: models not listed
# here (e.g. claude-sonnet-4-5, claude-haiku-4-5) keep their tuned
# temperatures unchanged.
_TEMPERATURE_REJECTING_MODEL_FAMILIES = (
    "claude-opus-4-7",
    "claude-opus-4-8",
    "claude-sonnet-5",
    "claude-fable",
    "claude-mythos",
)


def _model_accepts_temperature(model: str) -> bool:
    """Return ``True`` unless ``model`` is a family known to reject ``temperature``."""
    model_id = model.lower()
    return not any(family in model_id for family in _TEMPERATURE_REJECTING_MODEL_FAMILIES)


# OpenAI's newer models (GPT-5.x and the o-series reasoning models) reject the
# legacy ``max_tokens`` parameter — sending it returns
# "400 invalid_request_error: Unsupported parameter: 'max_tokens' is not
# supported with this model." They take ``max_completion_tokens`` instead.
# Matched as substrings so a Bedrock inference-profile id
# ("us.openai.gpt-5.6-sol", ...) or a raw OpenAI id ("gpt-5", "o1", ...) is
# covered. Other models (gpt-4o, gpt-oss, Claude, Nova, ...) keep ``max_tokens``.
_MAX_COMPLETION_TOKENS_MODEL_FAMILIES = (
    "gpt-5",
    "gpt-6",
    "o1",
    "o3",
    "o4",
)


def _model_uses_max_completion_tokens(model: str) -> bool:
    """Return ``True`` if ``model`` needs ``max_completion_tokens`` (not ``max_tokens``)."""
    model_id = model.lower()
    return any(family in model_id for family in _MAX_COMPLETION_TOKENS_MODEL_FAMILIES)


def build_llm(model: str, temperature: float, max_tokens: int) -> LLM:
    """Build an ``LLM`` for ``model``, honoring a custom base URL if configured.

    ``temperature`` is omitted from the constructed ``LLM`` for model families
    where Anthropic rejects it; CrewAI drops ``None``-valued params before the
    completion call, so no ``temperature`` reaches the API request.
    """
    settings = get_settings()

    kwargs: dict[str, Any] = {"model": model}
    # GPT-5.x / o-series reject legacy ``max_tokens``; they take
    # ``max_completion_tokens``. Everything else keeps ``max_tokens``.
    if _model_uses_max_completion_tokens(model):
        kwargs["max_completion_tokens"] = max_tokens
    else:
        kwargs["max_tokens"] = max_tokens
    if _model_accepts_temperature(model):
        kwargs["temperature"] = temperature

    # Amazon Bedrock's Anthropic-compatible endpoint (and any other endpoint
    # that speaks the Anthropic Messages wire format). Setting
    # ANTHROPIC_COMPATIBLE_LLM_API_BASE_URL routes Claude through CrewAI's
    # native Anthropic provider (messages.create) against that base URL,
    # instead of the Bedrock Converse provider. This is the path that lets us
    # run Claude on Bedrock WITHOUT the Converse toolUse/toolResult sanitizer:
    # the Messages API assembles tool turns itself, so the orphaned-block
    # ValidationException class does not arise. Use a Messages-supported model
    # id (e.g. Claude Sonnet 5 / Opus 4.7+ / Haiku 4.5) and a Bedrock API key.
    # For bedrock-runtime, base_url is
    #   https://bedrock-runtime.<region>.amazonaws.com/anthropic
    # and the model id is the Bedrock model / inference-profile id.
    if settings.anthropic_compatible_llm_api_base_url:
        return LLM(
            api_key=settings.anthropic_compatible_llm_api_key,
            provider=_ANTHROPIC_COMPATIBLE_PROVIDER,
            base_url=settings.anthropic_compatible_llm_api_base_url,
            **kwargs,
        )

    if settings.openai_compatible_llm_api_base_url:
        return LLM(
            api_key=settings.openai_compatible_llm_api_key,
            provider=_OPENAI_COMPATIBLE_PROVIDER,
            base_url=settings.openai_compatible_llm_api_base_url,
            **kwargs,
        )

    return LLM(**kwargs)

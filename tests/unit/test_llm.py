# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Unit tests for the custom OpenAI-compatible endpoint alternative (build_llm)."""

from ad_buyer.config.settings import Settings
from ad_buyer.llm import (
    _model_accepts_temperature,
    _model_uses_max_completion_tokens,
    build_llm,
)


def _settings(**overrides) -> Settings:
    """Build an isolated Settings instance that ignores any local .env file.

    Explicitly nulls the custom-endpoint fields unless a test overrides them,
    so an ambient repo ``.env`` (or exported ``*_COMPATIBLE_*`` env vars) can't
    bleed in and flip which provider branch ``build_llm`` takes. Without this,
    a checkout that has a real ``.env`` pointing at the Bedrock Anthropic
    endpoint would make the OpenAI-compatible-branch tests see the anthropic
    branch instead.
    """
    overrides.setdefault("anthropic_api_key", "sk-ant-test")
    overrides.setdefault("openai_compatible_llm_api_base_url", None)
    overrides.setdefault("openai_compatible_llm_api_key", None)
    overrides.setdefault("anthropic_compatible_llm_api_base_url", None)
    overrides.setdefault("anthropic_compatible_llm_api_key", None)
    return Settings(_env_file=None, **overrides)


class TestUnchangedWhenNoBaseUrl:
    """No OPENAI_COMPATIBLE_LLM_API_BASE_URL configured — identical to
    constructing LLM directly, so DEFAULT_LLM_MODEL/MANAGER_LLM_MODEL
    provider swapping (Anthropic, OpenAI, Gemini, Bedrock) works exactly as
    before this module existed."""

    def test_anthropic_model_routes_natively(self, monkeypatch):
        monkeypatch.setattr(
            "ad_buyer.llm.get_settings",
            lambda: _settings(default_llm_model="anthropic/claude-sonnet-4-5-20250929"),
        )
        llm = build_llm(
            model="anthropic/claude-sonnet-4-5-20250929",
            temperature=0.3,
            max_tokens=4096,
        )
        assert llm.model == "claude-sonnet-4-5-20250929"
        assert llm.provider == "anthropic"

    def test_openai_model_routes_natively(self, monkeypatch):
        monkeypatch.setattr(
            "ad_buyer.llm.get_settings",
            lambda: _settings(openai_api_key="sk-openai-test"),
        )
        llm = build_llm(model="openai/gpt-4o", temperature=0.5, max_tokens=4096)
        assert llm.model == "gpt-4o"
        assert llm.provider == "openai"

    def test_temperature_and_max_tokens_pass_through(self, monkeypatch):
        monkeypatch.setattr("ad_buyer.llm.get_settings", lambda: _settings())
        llm = build_llm(
            model="anthropic/claude-sonnet-4-5-20250929",
            temperature=0.7,
            max_tokens=2048,
        )
        assert llm.temperature == 0.7
        assert llm.max_tokens == 2048


class TestCustomOpenAICompatibleEndpoint:
    """OPENAI_COMPATIBLE_LLM_API_BASE_URL configured — pins routing to the
    native OpenAI client regardless of the model id's shape, covering NVIDIA
    NIM, Ollama, HuggingFace TGI, and similar endpoints."""

    def test_nvidia_nim_routes_via_openai_with_base_url(self, monkeypatch):
        monkeypatch.setattr(
            "ad_buyer.llm.get_settings",
            lambda: _settings(
                openai_compatible_llm_api_key="nvapi-test",
                openai_compatible_llm_api_base_url="https://integrate.api.nvidia.com/v1",
            ),
        )
        llm = build_llm(model="meta/llama-3.1-70b-instruct", temperature=0.3, max_tokens=4096)
        assert llm.provider == "openai"
        assert llm.model == "meta/llama-3.1-70b-instruct"
        assert llm.base_url == "https://integrate.api.nvidia.com/v1"
        assert llm.api_key == "nvapi-test"

    def test_local_ollama_needs_no_key(self, monkeypatch):
        monkeypatch.setattr(
            "ad_buyer.llm.get_settings",
            lambda: _settings(openai_compatible_llm_api_base_url="http://localhost:11434/v1"),
        )
        llm = build_llm(model="llama3", temperature=0.3, max_tokens=4096)
        assert llm.provider == "openai"
        assert llm.model == "llama3"
        assert llm.base_url == "http://localhost:11434/v1"


class TestBedrockAnthropicCompatibleEndpoint:
    """ANTHROPIC_COMPATIBLE_LLM_API_BASE_URL configured — routes Claude through
    CrewAI's native Anthropic provider (Messages API) against a custom base URL
    such as Amazon Bedrock's /anthropic endpoint. This is the path that lets
    Claude run on Bedrock WITHOUT the Converse toolUse/toolResult sanitizer."""

    def test_bedrock_messages_routes_via_anthropic_with_base_url(self, monkeypatch):
        monkeypatch.setattr(
            "ad_buyer.llm.get_settings",
            lambda: _settings(
                anthropic_compatible_llm_api_key="bedrock-key-test",
                anthropic_compatible_llm_api_base_url="https://bedrock-runtime.us-west-2.amazonaws.com/anthropic",
            ),
        )
        llm = build_llm(
            model="us.anthropic.claude-sonnet-5-v1:0",
            temperature=0.3,
            max_tokens=4096,
        )
        assert llm.provider == "anthropic"
        assert llm.base_url == "https://bedrock-runtime.us-west-2.amazonaws.com/anthropic"
        assert llm.api_key == "bedrock-key-test"

    def test_anthropic_compatible_takes_precedence_over_openai_compatible(self, monkeypatch):
        """When both custom endpoints are set, the Anthropic-compatible branch
        wins so Claude never gets misrouted to the OpenAI client (Claude is not
        served on Bedrock's OpenAI Chat Completions path)."""
        monkeypatch.setattr(
            "ad_buyer.llm.get_settings",
            lambda: _settings(
                anthropic_compatible_llm_api_key="bedrock-key-test",
                anthropic_compatible_llm_api_base_url="https://bedrock-runtime.us-west-2.amazonaws.com/anthropic",
                openai_compatible_llm_api_key="should-not-win",
                openai_compatible_llm_api_base_url="https://integrate.api.nvidia.com/v1",
            ),
        )
        llm = build_llm(model="us.anthropic.claude-sonnet-5-v1:0", temperature=0.3, max_tokens=4096)
        assert llm.provider == "anthropic"
        assert llm.base_url == "https://bedrock-runtime.us-west-2.amazonaws.com/anthropic"

    def test_sonnet_5_omits_temperature_on_bedrock_messages(self, monkeypatch):
        monkeypatch.setattr(
            "ad_buyer.llm.get_settings",
            lambda: _settings(
                anthropic_compatible_llm_api_base_url="https://bedrock-runtime.us-west-2.amazonaws.com/anthropic",
            ),
        )
        llm = build_llm(model="us.anthropic.claude-sonnet-5-v1:0", temperature=0.3, max_tokens=4096)
        assert llm.provider == "anthropic"
        assert llm.temperature is None


class TestModelAcceptsTemperature:
    """Anthropic rejects the temperature parameter on Opus 4.7+, Sonnet 5+,
    and Fable/Mythos with a 400 invalid_request_error; older models still
    accept their tuned temperatures."""

    def test_opus_4_8_rejects_temperature(self):
        assert _model_accepts_temperature("anthropic/claude-opus-4-8") is False

    def test_opus_4_7_rejects_temperature(self):
        assert _model_accepts_temperature("anthropic/claude-opus-4-7") is False

    def test_bedrock_prefixed_opus_4_8_rejects_temperature(self):
        assert _model_accepts_temperature("bedrock/us.anthropic.claude-opus-4-8") is False

    def test_sonnet_5_rejects_temperature(self):
        assert _model_accepts_temperature("anthropic/claude-sonnet-5") is False

    def test_fable_rejects_temperature(self):
        assert _model_accepts_temperature("anthropic/claude-fable-5") is False

    def test_match_is_case_insensitive(self):
        assert _model_accepts_temperature("Anthropic/Claude-Opus-4-8") is False

    def test_sonnet_4_5_accepts_temperature(self):
        assert _model_accepts_temperature("anthropic/claude-sonnet-4-5-20250929") is True

    def test_haiku_accepts_temperature(self):
        assert _model_accepts_temperature("anthropic/claude-haiku-4-5") is True

    def test_non_anthropic_model_accepts_temperature(self):
        assert _model_accepts_temperature("openai/gpt-4o") is True


class TestTemperatureOmittedWhereRejected:
    """build_llm leaves temperature unset (None) for models that reject it,
    so CrewAI's None-filtering drops it from the API request entirely; models
    that accept it keep their tuned value."""

    def test_manager_model_omits_temperature(self, monkeypatch):
        monkeypatch.setattr("ad_buyer.llm.get_settings", lambda: _settings())
        llm = build_llm(model="anthropic/claude-opus-4-8", temperature=0.3, max_tokens=4096)
        assert llm.temperature is None

    def test_omitted_temperature_never_reaches_completion_params(self, monkeypatch):
        monkeypatch.setattr("ad_buyer.llm.get_settings", lambda: _settings())
        llm = build_llm(model="anthropic/claude-opus-4-8", temperature=0.3, max_tokens=4096)
        params = llm._prepare_completion_params("ping")
        assert "temperature" not in params

    def test_worker_model_keeps_temperature(self, monkeypatch):
        monkeypatch.setattr("ad_buyer.llm.get_settings", lambda: _settings())
        llm = build_llm(
            model="anthropic/claude-sonnet-4-5-20250929",
            temperature=0.5,
            max_tokens=4096,
        )
        assert llm.temperature == 0.5

    def test_openai_compatible_path_also_omits_temperature(self, monkeypatch):
        monkeypatch.setattr(
            "ad_buyer.llm.get_settings",
            lambda: _settings(openai_compatible_llm_api_base_url="http://localhost:11434/v1"),
        )
        llm = build_llm(model="claude-opus-4-8", temperature=0.3, max_tokens=4096)
        assert llm.provider == "openai"
        assert llm.temperature is None


class TestModelUsesMaxCompletionTokens:
    """OpenAI's GPT-5.x and o-series reasoning models reject the legacy
    ``max_tokens`` parameter (400 invalid_request_error: 'max_tokens' is not
    supported with this model) and take ``max_completion_tokens`` instead.
    Other models keep ``max_tokens``."""

    def test_gpt_5_uses_max_completion_tokens(self):
        assert _model_uses_max_completion_tokens("us.openai.gpt-5.6-sol") is True

    def test_bare_gpt_5_uses_max_completion_tokens(self):
        assert _model_uses_max_completion_tokens("openai/gpt-5") is True

    def test_o1_uses_max_completion_tokens(self):
        assert _model_uses_max_completion_tokens("o1") is True

    def test_o3_uses_max_completion_tokens(self):
        assert _model_uses_max_completion_tokens("openai/o3-mini") is True

    def test_match_is_case_insensitive(self):
        assert _model_uses_max_completion_tokens("US.OpenAI.GPT-5.6-SOL") is True

    def test_gpt_4o_uses_legacy_max_tokens(self):
        assert _model_uses_max_completion_tokens("openai/gpt-4o") is False

    def test_claude_uses_legacy_max_tokens(self):
        assert _model_uses_max_completion_tokens("us.anthropic.claude-sonnet-5") is False


class TestMaxTokensRoutedToCorrectParam:
    """build_llm routes the token cap to max_completion_tokens for models that
    require it, and to max_tokens for everything else."""

    def test_gpt_5_routes_to_max_completion_tokens(self, monkeypatch):
        monkeypatch.setattr(
            "ad_buyer.llm.get_settings",
            lambda: _settings(
                openai_compatible_llm_api_base_url="https://bedrock-runtime.us-west-2.amazonaws.com/openai/v1",
                openai_compatible_llm_api_key="bedrock-key",
            ),
        )
        llm = build_llm(model="us.openai.gpt-5.6-sol", temperature=0.5, max_tokens=4096)
        assert llm.max_completion_tokens == 4096
        assert llm.max_tokens is None

    def test_gpt_4o_keeps_max_tokens(self, monkeypatch):
        monkeypatch.setattr(
            "ad_buyer.llm.get_settings",
            lambda: _settings(
                openai_compatible_llm_api_base_url="https://bedrock-runtime.us-west-2.amazonaws.com/openai/v1",
                openai_compatible_llm_api_key="bedrock-key",
            ),
        )
        llm = build_llm(model="gpt-4o", temperature=0.5, max_tokens=4096)
        assert llm.max_tokens == 4096

# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Application settings loaded from environment variables."""

from functools import lru_cache
from typing import Literal

from dotenv import find_dotenv
from pydantic_settings import BaseSettings

# Find .env file by searching up from current working directory
_ENV_FILE = find_dotenv(usecwd=True)


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    # API Keys
    anthropic_api_key: str = ""
    openai_api_key: str | None = None
    google_api_key: str | None = None

    # Inbound API key for authenticating requests to this service.
    # When empty/not set, authentication is disabled (development mode).
    api_key: str = ""

    # IAB agentic-direct server URL
    # Override via IAB_SERVER_URL env var or .env file
    iab_server_url: str = "http://localhost:8001"

    # Real IAB AAMP agent registry (EP-5.1). When aamp_registry_url is set
    # (env AAMP_REGISTRY_URL), seller discovery and agent-card fetch go
    # through the shared contract library's RegistryClient against the real
    # /api/agents API; when empty (the default), the legacy in-process /
    # sandbox discovery path is used. The swap is config, not code.
    # AAMP_REGISTRY_AUTH_TOKEN carries the bearer JWT — never log it.
    aamp_registry_url: str = ""
    aamp_registry_auth_token: str = ""

    # Seller Agent Endpoints (comma-separated list of MCP/A2A server URLs)
    # Each endpoint should implement IAB Tech Lab OpenDirect/AdCOM standards
    seller_endpoints: str = ""

    # OpenDirect API Configuration (legacy single-server mode)
    opendirect_base_url: str = "http://localhost:3000/api/v2.1"
    opendirect_token: str | None = None
    opendirect_api_key: str | None = None
    # OpenDirect 2.1 spec-dialect account context. When BOTH are set the
    # avails client emits the published ProductAvailsSearch wire shape
    # (spec-required accountid/advertiserbrandid); when unset it stays on
    # the legacy simplified profile — the spec ids are never fabricated.
    opendirect_account_id: str | None = None
    opendirect_advertiser_brand_id: str | None = None

    # IAB Diligence Platform — vendor approval gate, wired into the
    # canonical booking path (MultiSellerOrchestrator discovery stage).
    # Default OFF: with ``sgp_enforce`` false, no SGP client is built, no
    # SGP calls are made, and booking behavior is byte-identical. When
    # enforcing, sellers whose IAB buyer-agent approval cannot be
    # positively verified are excluded from quoting/booking, with one
    # ``sgp.vendor_gate`` event per decision. FAIL-CLOSED: when enforcing
    # and the SGP API is unreachable — or ``sgp_enforce`` is set without
    # an ``sgp_api_key`` — NO seller passes the gate; the emitted reason
    # always carries the cause.
    sgp_api_key: str = ""
    # Production endpoint. For testing, use the demo environment:
    # https://api.safeguardprivacy-demo.com
    sgp_base_url: str = "https://api.safeguardprivacy.com"
    sgp_enforce: bool = False
    # Behavior when IAB Diligence Platform returns 404 for a seller domain (vendor
    # not in the buyer's SGP portfolio). One of: "block", "warn", "allow".
    sgp_unknown_vendor_policy: str = "block"
    sgp_cache_ttl_seconds: int = 900

    def get_seller_endpoints(self) -> list[str]:
        """Parse seller endpoints from comma-separated string.

        Returns:
            List of seller endpoint URLs
        """
        if not self.seller_endpoints:
            return []
        return [url.strip() for url in self.seller_endpoints.split(",") if url.strip()]

    # Negotiation in the real booking path. When a seller
    # quote exceeds the buyer's max_cpm ceiling but sits within the
    # negotiation band (quote <= ceiling * negotiation_band), the
    # orchestrator attempts a deterministic negotiation before discarding
    # the quote. Default ON; set NEGOTIATION_ENABLED=false to restore the
    # legacy strict filter. The band default mirrors the reference SDK's
    # negotiation_band_per_mille=1250 (1.25x).
    negotiation_enabled: bool = True
    negotiation_band: float = 1.25
    negotiation_max_rounds: int = 3
    # HTTP timeout for the negotiation surface (proposal open + counter
    # rounds), SEPARATE from the 30 s quote timeout. Sellers
    # may answer POST /proposals with a synchronous LLM crew: the live
    # rig's ProposalHandlingFlow measured ~10m46s (~646 s) per proposal
    # (S2 live proof 2026-07-21, Bug I), so the old 30 s quote-timeout
    # deterministically killed every live negotiation at round 1. Default
    # 720 s = the measured flow plus ~11% headroom, finite by design.
    # Override via NEGOTIATION_TIMEOUT_SECONDS.
    negotiation_timeout_seconds: float = 720.0

    # Cross-seller product resolution. Research reads one
    # catalog, but discovery may return OTHER sellers whose catalogs use
    # different product IDs -- quoting them with the recommended ID 404s
    # (product_not_found) seller-side. When enabled, the orchestrator
    # re-resolves an equivalent product on each seller's own catalog before
    # quoting it; sellers with no equivalent are skipped with a clear
    # per-seller error. Set PRODUCT_RESOLUTION_ENABLED=false to restore the
    # legacy passthrough (same product_id sent to every discovered seller).
    product_resolution_enabled: bool = True

    # LLM Settings
    # Supported providers: anthropic (default), openai, gemini, bedrock
    # Set DEFAULT_LLM_MODEL to switch provider, e.g.:
    #   anthropic/claude-sonnet-4-5-20250929  (requires ANTHROPIC_API_KEY)
    #   openai/gpt-4o                          (requires OPENAI_API_KEY)
    #   gemini/gemini-2.5-flash                (requires GOOGLE_API_KEY)
    #   bedrock/us.anthropic.claude-sonnet-4-5-20250929-v1:0 (requires AWS creds)
    default_llm_model: str = "anthropic/claude-sonnet-4-5-20250929"
    manager_llm_model: str = "anthropic/claude-opus-4-20250514"
    llm_temperature: float = 0.3
    llm_max_tokens: int = 4096

    # Alternative: any OpenAI-wire-compatible endpoint (NVIDIA NIM, Ollama,
    # HuggingFace TGI, vLLM, ...). Set OPENAI_COMPATIBLE_LLM_API_BASE_URL
    # alongside DEFAULT_LLM_MODEL/MANAGER_LLM_MODEL (using the raw model id
    # the endpoint expects) to route through that endpoint instead of a named
    # provider above. OPENAI_COMPATIBLE_LLM_API_KEY is optional — omit it for
    # endpoints like a local Ollama server that don't require one.
    openai_compatible_llm_api_key: str | None = None
    openai_compatible_llm_api_base_url: str | None = None

    # Database / Storage Configuration
    database_url: str = "sqlite:///./ad_buyer.db"
    redis_url: str | None = None
    storage_type: str = "sqlite"  # sqlite, redis, hybrid
    postgres_pool_min: int = 2
    postgres_pool_max: int = 10

    # Durable fallback for audit-class events (see events/audit_fallback.py).
    # When the event bus fails for an audit-class event, the event is appended
    # to this JSONL file (fsynced per write) instead of being dropped.
    audit_fallback_path: str = "data/audit_fallback.jsonl"

    # CrewAI Settings
    crew_memory_enabled: bool = True
    crew_verbose: bool = True
    crew_max_iterations: int = 15

    # CORS
    cors_allowed_origins: str = "*"

    def get_cors_origins(self) -> list[str]:
        """Parse CORS allowed origins from comma-separated string."""
        if not self.cors_allowed_origins:
            return []
        return [o.strip() for o in self.cors_allowed_origins.split(",") if o.strip()]

    # Mixpeek contextual enrichment
    mixpeek_api_key: str = ""
    mixpeek_base_url: str = "https://api.mixpeek.com"
    mixpeek_namespace: str = ""

    # Environment
    environment: str = "development"
    log_level: str = "INFO"

    # Feature flag (proposal §6 row 15 / wire-format spec §9):
    # When True, the buyer's OpenRTB builder emits the temporary
    # `user.ext.iab_agentic_audiences.refs[]` extension carrying agentic
    # audience refs. Default off until IAB ratifies an extension shape;
    # see the 90-day dual-emit migration policy in the wire-format spec.
    enable_agentic_openrtb_ext: bool = False

    # Embedding mode for the buyer's UCP query embeddings.
    # Locked decision in docs/decisions/EMBEDDING_STRATEGY_2026-04-25.md (E2-1):
    # - "mock": SHA256-seeded deterministic vector (legacy; CI fallback)
    # - "local": sentence-transformers all-MiniLM-L6-v2 (384-dim)
    # - "advertiser": use advertiser-supplied vector verbatim
    # - "hybrid": prefer advertiser-supplied; else local; else mock
    # Override via EMBEDDING_MODE env var.
    embedding_mode: Literal["mock", "local", "advertiser", "hybrid"] = "hybrid"

    # --------------------------------------------------------------------------
    # Meta Ads API integration
    # --------------------------------------------------------------------------
    # System user access token (from Meta Business Manager → System Users)
    meta_access_token: str = ""
    # Ad account ID (format: act_XXXXXXXXX — assign to system user in Business Manager)
    meta_ad_account_id: str = ""
    # Facebook Page ID (required for ad creative creation)
    meta_page_id: str = ""
    # Graph API version (used for reach estimates)
    meta_api_version: str = "v21.0"

    model_config = {
        "env_file": _ENV_FILE if _ENV_FILE else None,
        "env_file_encoding": "utf-8",
        "extra": "ignore",
    }


@lru_cache
def get_settings() -> Settings:
    """Get cached settings instance."""
    return Settings()


class _LazySettings:
    """Lazy proxy that defers Settings() construction until first attribute access.

    Many modules import the module-level `settings` symbol at import time.
    Constructing Settings() eagerly at import time freezes env vars before
    tests can override them. This proxy delegates all attribute access to a
    cached Settings instance built on first use, so tests that patch env vars
    before any settings.X read see the correct values.
    """

    __slots__ = ()

    def __getattr__(self, name: str):
        return getattr(get_settings(), name)

    def __setattr__(self, name: str, value) -> None:
        setattr(get_settings(), name, value)

    def __repr__(self) -> str:
        return f"_LazySettings(proxy_to={get_settings()!r})"


settings = _LazySettings()

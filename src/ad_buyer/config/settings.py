# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Application settings loaded from environment variables."""

from functools import lru_cache
from typing import Optional

from dotenv import find_dotenv
from pydantic_settings import BaseSettings

# Find .env file by searching up from current working directory
_ENV_FILE = find_dotenv(usecwd=True)


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    # API Keys
    anthropic_api_key: str = ""

    # Inbound API key for authenticating requests to this service.
    # When empty/not set, authentication is disabled (development mode).
    api_key: str = ""

    # IAB agentic-direct server URL
    # Override via IAB_SERVER_URL env var or .env file
    iab_server_url: str = "http://localhost:8001"

    # Seller Agent Endpoints (comma-separated list of MCP/A2A server URLs)
    # Each endpoint should implement IAB Tech Lab OpenDirect/AdCOM standards
    seller_endpoints: str = ""

    # OpenDirect API Configuration (legacy single-server mode)
    opendirect_base_url: str = "http://localhost:3000/api/v2.1"
    opendirect_token: Optional[str] = None
    opendirect_api_key: Optional[str] = None

    def get_seller_endpoints(self) -> list[str]:
        """Parse seller endpoints from comma-separated string.

        Returns:
            List of seller endpoint URLs
        """
        if not self.seller_endpoints:
            return []
        return [url.strip() for url in self.seller_endpoints.split(",") if url.strip()]

    # LLM Settings
    default_llm_model: str = "anthropic/claude-sonnet-4-5-20250929"
    manager_llm_model: str = "anthropic/claude-opus-4-20250514"
    llm_temperature: float = 0.3
    llm_max_tokens: int = 4096

    # Database
    database_url: str = "sqlite:///./ad_buyer.db"

    # Optional Redis
    redis_url: Optional[str] = None

    # CrewAI Settings
    crew_memory_enabled: bool = True
    crew_verbose: bool = True
    crew_max_iterations: int = 15

    # CORS
    cors_allowed_origins: str = "http://localhost:3000,http://localhost:8080"

    def get_cors_origins(self) -> list[str]:
        """Parse CORS allowed origins from comma-separated string."""
        if not self.cors_allowed_origins:
            return []
        return [o.strip() for o in self.cors_allowed_origins.split(",") if o.strip()]

    # Environment
    environment: str = "development"
    log_level: str = "INFO"

    model_config = {
        "env_file": _ENV_FILE if _ENV_FILE else None,
        "env_file_encoding": "utf-8",
        "extra": "ignore",
    }


@lru_cache
def get_settings() -> Settings:
    """Get cached settings instance."""
    return Settings()


settings = get_settings()

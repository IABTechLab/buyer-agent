# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Application settings loaded from environment variables."""

from functools import lru_cache
from typing import Optional

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    # API Keys
    anthropic_api_key: str = ""

    # OpenDirect API Configuration
    opendirect_base_url: str = "http://localhost:3000/api/v2.1"
    opendirect_token: Optional[str] = None
    opendirect_api_key: Optional[str] = None

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

    # Environment
    environment: str = "development"
    log_level: str = "INFO"

    model_config = {
        "env_file": ".env",
        "env_file_encoding": "utf-8",
        "extra": "ignore",
    }


@lru_cache
def get_settings() -> Settings:
    """Get cached settings instance."""
    return Settings()


settings = get_settings()

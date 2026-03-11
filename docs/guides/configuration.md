# Configuration Reference

The buyer agent is configured through environment variables loaded via [pydantic-settings](https://docs.pydantic.dev/latest/concepts/pydantic_settings/). Settings are defined in a single `Settings` class and can be set via a `.env` file, shell environment variables, or directly in code.

**Key file:** `src/ad_buyer/config/settings.py`

---

## Quick Start

Create a `.env` file in the project root:

```bash
# Required
ANTHROPIC_API_KEY=sk-ant-...

# Seller communication
SELLER_ENDPOINTS=http://localhost:8000
IAB_SERVER_URL=http://localhost:8001

# Optional overrides
DEFAULT_LLM_MODEL=anthropic/claude-sonnet-4-5-20250929
MANAGER_LLM_MODEL=anthropic/claude-opus-4-20250514
LOG_LEVEL=INFO
```

Access settings in code:

```python
from ad_buyer.config.settings import settings

print(settings.default_llm_model)
print(settings.get_seller_endpoints())
```

---

## Environment Variables

### API Keys

| Variable | Type | Default | Description |
|----------|------|---------|-------------|
| `ANTHROPIC_API_KEY` | `str` | `""` | Anthropic API key for Claude models. Required for agent functionality. |
| `API_KEY` | `str` | `""` | Inbound API key for authenticating requests to this service. When empty, authentication is disabled (development mode). |

!!! warning "Development mode"
    When `API_KEY` is empty, the buyer agent's API endpoints are unauthenticated. Set a value in production to require `X-Api-Key` headers on incoming requests.

### Seller Endpoints

| Variable | Type | Default | Description |
|----------|------|---------|-------------|
| `SELLER_ENDPOINTS` | `str` | `""` | Comma-separated list of seller MCP/A2A server URLs. |
| `IAB_SERVER_URL` | `str` | `http://localhost:8001` | Primary IAB agentic-direct server URL. Used as the default `base_url` for flows and clients. |

Parse seller endpoints as a list:

```python
endpoints = settings.get_seller_endpoints()
# ["http://seller1.example.com:8000", "http://seller2.example.com:8000"]
```

### OpenDirect (Legacy)

| Variable | Type | Default | Description |
|----------|------|---------|-------------|
| `OPENDIRECT_BASE_URL` | `str` | `http://localhost:3000/api/v2.1` | Base URL for the OpenDirect 2.1 REST API. |
| `OPENDIRECT_TOKEN` | `str` | `None` | Bearer token for OpenDirect authentication. |
| `OPENDIRECT_API_KEY` | `str` | `None` | API key for OpenDirect authentication. |

!!! note "Legacy mode"
    OpenDirect settings support single-server mode for backwards compatibility. For multi-seller workflows, use `SELLER_ENDPOINTS` with the MCP/A2A protocol clients instead.

---

### LLM Configuration

| Variable | Type | Default | Description |
|----------|------|---------|-------------|
| `DEFAULT_LLM_MODEL` | `str` | `anthropic/claude-sonnet-4-5-20250929` | Model for Level 2 channel specialists and Level 3 functional agents. |
| `MANAGER_LLM_MODEL` | `str` | `anthropic/claude-opus-4-20250514` | Model for the Level 1 Portfolio Manager. Opus is used for strategic reasoning. |
| `LLM_TEMPERATURE` | `float` | `0.3` | Default temperature for LLM calls. Individual agents may override this. |
| `LLM_MAX_TOKENS` | `int` | `4096` | Maximum token output for LLM responses. |

Models are specified in [litellm](https://docs.litellm.ai/) format: `provider/model-name`. This allows swapping providers without code changes.

```bash
# Use a different model provider
DEFAULT_LLM_MODEL=openai/gpt-4o
MANAGER_LLM_MODEL=anthropic/claude-opus-4-20250514
```

**Agent temperature overrides:**

While `LLM_TEMPERATURE` sets the global default, each agent type uses a tuned temperature:

| Agent | Temperature | Rationale |
|-------|------------|-----------|
| Portfolio Manager (L1) | 0.3 | Balanced strategic reasoning |
| Channel Specialists (L2) | 0.5 | Creative inventory selection |
| Audience Planner (L3) | 0.3 | Precise audience strategy |
| Research Agent (L3) | 0.2 | Data-focused, minimal creativity |
| Reporting Agent (L3) (Coming Soon) | 0.2 | Analytical precision |
| Execution Agent (L3) | 0.1 | Precise booking execution |

---

### Database

| Variable | Type | Default | Description |
|----------|------|---------|-------------|
| `DATABASE_URL` | `str` | `sqlite:///./ad_buyer.db` | SQLAlchemy connection string for the deal store and local persistence. |

Supports any SQLAlchemy-compatible database. SQLite is the default for development; use PostgreSQL or MySQL for production.

```bash
# PostgreSQL
DATABASE_URL=postgresql://user:pass@localhost:5432/ad_buyer

# SQLite (default)
DATABASE_URL=sqlite:///./ad_buyer.db
```

---

### Redis

| Variable | Type | Default | Description |
|----------|------|---------|-------------|
| `REDIS_URL` | `str` | `None` | Redis connection URL for caching and session management. Optional. |

When set, Redis is used for CrewAI memory persistence and session caching. When `None`, in-memory storage is used.

```bash
REDIS_URL=redis://localhost:6379/0
```

---

### CrewAI Settings

| Variable | Type | Default | Description |
|----------|------|---------|-------------|
| `CREW_MEMORY_ENABLED` | `bool` | `True` | Enable CrewAI agent memory across tasks. |
| `CREW_VERBOSE` | `bool` | `True` | Enable verbose logging for crew execution. Set to `False` in production. |
| `CREW_MAX_ITERATIONS` | `int` | `15` | Maximum iterations per crew task before forced completion. |

```bash
# Production settings
CREW_MEMORY_ENABLED=True
CREW_VERBOSE=False
CREW_MAX_ITERATIONS=10
```

---

### CORS

| Variable | Type | Default | Description |
|----------|------|---------|-------------|
| `CORS_ALLOWED_ORIGINS` | `str` | `http://localhost:3000,http://localhost:8080` | Comma-separated list of allowed CORS origins. |

Parse as a list:

```python
origins = settings.get_cors_origins()
# ["http://localhost:3000", "http://localhost:8080"]
```

```bash
# Production
CORS_ALLOWED_ORIGINS=https://dashboard.example.com,https://app.example.com
```

---

### Environment

| Variable | Type | Default | Description |
|----------|------|---------|-------------|
| `ENVIRONMENT` | `str` | `development` | Runtime environment identifier. |
| `LOG_LEVEL` | `str` | `INFO` | Logging level: `DEBUG`, `INFO`, `WARNING`, `ERROR`, `CRITICAL`. |

---

## Settings Class

The full `Settings` class for reference:

```python
from ad_buyer.config.settings import Settings, get_settings

# Singleton (cached)
settings = get_settings()

# Or create a fresh instance (useful for testing)
test_settings = Settings(
    anthropic_api_key="test-key",
    database_url="sqlite:///./test.db",
    environment="testing",
)
```

The `get_settings()` function returns a cached singleton via `@lru_cache`. This means environment variable changes after first access require a process restart.

---

## .env File Resolution

The settings module uses `python-dotenv` to find a `.env` file. It searches upward from the current working directory. This means the `.env` file can live in the project root or any parent directory.

```
project-root/
  .env              <-- found automatically
  src/
    ad_buyer/
      config/
        settings.py  <-- searches upward from cwd
```

!!! tip "Multiple environments"
    For different environments, use separate `.env` files and set the working directory accordingly, or override variables directly in the shell environment. Shell variables take precedence over `.env` values.

---

## Example Configurations

### Development (Minimal)

```bash
ANTHROPIC_API_KEY=sk-ant-...
```

Everything else uses defaults: local SQLite database, localhost seller, Sonnet for agents, Opus for manager, verbose logging enabled.

### Production

```bash
ANTHROPIC_API_KEY=sk-ant-...
API_KEY=your-service-api-key

SELLER_ENDPOINTS=https://seller1.example.com,https://seller2.example.com
IAB_SERVER_URL=https://primary-seller.example.com

DATABASE_URL=postgresql://buyer:pass@db.example.com:5432/ad_buyer
REDIS_URL=redis://cache.example.com:6379/0

CREW_VERBOSE=False
CREW_MAX_ITERATIONS=10

CORS_ALLOWED_ORIGINS=https://dashboard.example.com
ENVIRONMENT=production
LOG_LEVEL=WARNING
```

### Testing

```bash
ANTHROPIC_API_KEY=test-key
API_KEY=test-api-key
DATABASE_URL=sqlite:///./test_ad_buyer.db
ENVIRONMENT=testing
LOG_LEVEL=DEBUG
CREW_VERBOSE=True
CREW_MAX_ITERATIONS=5
```

---

## Related

- [Agent Hierarchy](../architecture/agent-hierarchy.md) --- How LLM settings map to agent levels
- [Architecture Overview](../architecture/overview.md) --- System component overview
- [Authentication](../api/authentication.md) --- API key setup for inbound requests

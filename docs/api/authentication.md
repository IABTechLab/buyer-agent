# Authentication

The buyer agent uses API-key authentication via an HTTP middleware on all non-public endpoints.

## X-API-Key Header

Protected endpoints require the `X-API-Key` header:

```bash
curl -H "X-API-Key: your-secret-key" http://localhost:8001/bookings
```

The middleware compares the provided key against the `api_key` setting. If the key is missing or does not match, the server returns `401`:

```json
{"detail": "Invalid or missing API key"}
```

## Development Mode

When `api_key` is empty (the default), authentication is **disabled entirely**. All requests are allowed without a key. This is the intended mode for local development.

To enable auth, set the `API_KEY` environment variable or add it to your `.env` file:

```dotenv
API_KEY=my-secret-buyer-key
```

## Public Paths

The following paths always skip authentication, even when an API key is configured:

| Path | Purpose |
|------|---------|
| `/health` | Health check |
| `/docs` | Swagger UI |
| `/openapi.json` | OpenAPI schema |
| `/redoc` | ReDoc documentation |

## Configuration

The API key is loaded through `pydantic-settings` in `ad_buyer.config.settings.Settings`:

```python
class Settings(BaseSettings):
    # Inbound API key for authenticating requests to this service.
    # When empty/not set, authentication is disabled (development mode).
    api_key: str = ""
```

Set it via any method that `pydantic-settings` supports: environment variable, `.env` file, or constructor argument.

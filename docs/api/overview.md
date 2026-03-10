# API Overview

The Ad Buyer Agent API is a FastAPI application running on port 8001 by default. All endpoints return JSON.

Base URL: `http://localhost:8001`

## Endpoint Summary

| Method | Path | Tag | Summary |
|--------|------|-----|---------|
| `GET` | `/health` | Health | Service health check |
| `POST` | `/bookings` | Bookings | Start a new booking workflow |
| `GET` | `/bookings/{job_id}` | Bookings | Get booking workflow status |
| `POST` | `/bookings/{job_id}/approve` | Bookings | Approve specific recommendations |
| `POST` | `/bookings/{job_id}/approve-all` | Bookings | Approve all recommendations |
| `GET` | `/bookings` | Bookings | List all booking jobs |
| `POST` | `/products/search` | Products | Search seller product catalog |

## Tags

- **Health** -- service health and readiness
- **Bookings** -- campaign booking workflow lifecycle
- **Products** -- seller inventory product search

## Interactive Documentation

When the server is running, Swagger UI is available at `/docs` and ReDoc at `/redoc`. The raw OpenAPI schema is at `/openapi.json`.

## Related Pages

- [Authentication](authentication.md)
- [Bookings API](bookings.md)
- [Products API](products.md)

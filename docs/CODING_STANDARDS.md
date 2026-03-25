# Coding Standards: ad_buyer_system

Standards derived from the code quality audit (ar-diwq, 2026-03-25).
Applies to all Python source in `src/ad_buyer/` and `tests/`.

---

## 1. Python Version

Target **Python 3.11+** (per `pyproject.toml`).  Use features available
since 3.11 freely (`StrEnum`, `X | None`, `tomllib`, etc.).

## 2. Formatter and Linter

**Ruff** with rules `E, F, I, N, W, UP` (configured in `pyproject.toml`).
Run `ruff check src/ tests/` before committing.  Auto-fixable violations
can be cleaned with `ruff check --fix`.

## 3. Line Length

**100 characters** maximum.  Wrap long `Field(description=...)` strings
with parenthesised multi-line strings.  SQL uses triple-quoted strings.

## 4. Type Hints

Required on **all public functions** (parameters and return types).
Use `X | None` (PEP 604) instead of `Optional[X]`.

## 5. Docstrings

Required on all public classes and functions.  Use **Google-style** format:

```python
def book_deal(self, request: BookingRequest) -> DealResponse:
    """Book a deal from an accepted quote.

    Args:
        request: Booking request with quote_id.

    Returns:
        The booked deal response.

    Raises:
        DealsClientError: On HTTP or transport errors.
    """
```

## 6. Datetime Handling

Always use **`datetime.now(timezone.utc)`**.  Never use the deprecated
`datetime.utcnow()` (removed in Python 3.12).

```python
# Good
from datetime import datetime, timezone
now = datetime.now(timezone.utc)

# Bad
now = datetime.utcnow()
```

## 7. Pydantic Models

Use `Field(...)` with `description` on all public API fields.  Use
`model_config = {"populate_by_name": True}` consistently when aliases
are needed.

## 8. SQL Safety

Always use **parameterised queries** (`?` placeholders) for values.
Column names must come from hardcoded allow-lists, never from user input.

```python
# Good
cursor.execute("SELECT * FROM deals WHERE id = ?", (deal_id,))

# Bad
cursor.execute(f"SELECT * FROM deals WHERE id = '{deal_id}'")
```

## 9. Error Handling

Catch **specific exception types**.  Document any intentional broad
`except Exception:` with a `# noqa: BLE001` comment explaining why.

Acceptable broad-catch patterns (must be documented):

| Pattern | Rationale |
|---------|-----------|
| Event bus subscriber isolation | One subscriber failure must not block others |
| Event emission fail-open | Events are non-critical; flows continue on failure |
| CrewAI flow step handlers | CrewAI can raise arbitrary exceptions from LLM calls |
| Top-level CLI/background task handlers | Must capture any failure for user display or job status |
| Per-seller / per-channel isolation loops | One failing item must not abort the entire batch |

All other catches should name specific types (`ValueError`, `KeyError`,
`OSError`, `httpx.HTTPError`, `sqlite3.Error`, etc.).

## 10. Imports

Sorted per **ruff I001** (isort-compatible).  Order:

1. Standard library
2. Third-party packages
3. Local/project imports

## 11. Enums

Use **`StrEnum`** (Python 3.11+) instead of `(str, Enum)`:

```python
# Good
from enum import StrEnum

class DealType(StrEnum):
    PG = "PG"
    PD = "PD"

# Bad
class DealType(str, Enum):
    PG = "PG"
    PD = "PD"
```

## 12. Testing

- **In-memory SQLite** for store tests (no file I/O in CI).
- **Fixtures** over `setUp`/`tearDown`.
- **Test class per module** (e.g., `TestDealStore`, `TestCampaignStore`).
- Thread-safety tests for shared storage classes.
- Run full suite: `pytest tests/ -v --tb=short`.

## 13. Logging

Use `logging.getLogger(__name__)`.  Never use `print()` for operational
output.  Use structured messages with `%s` formatting (not f-strings in
log calls):

```python
# Good
logger.info("Deal %s booked at CPM %.2f", deal_id, cpm)

# Bad
logger.info(f"Deal {deal_id} booked at CPM {cpm:.2f}")
```

## 14. Module Headers

Every source file should include:

```python
# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Module docstring describing purpose."""
```

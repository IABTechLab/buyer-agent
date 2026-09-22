# Donated to IAB Tech Lab

"""PostgreSQL DDL for the buyer store, DERIVED from the SQLite DDL in ``schema.py``.

Scope 2 / Req 12.6 — the AgentCore ``--storage postgres`` path opts into a
durable Postgres backend WITHOUT rewriting the shared SQLite ``schema.py`` (that
dialect unification is deferred to the upstream maintainers). Rather than
hand-maintain a parallel copy of every ``CREATE TABLE`` (which would drift), this
module IMPORTS the SQLite DDL strings from ``schema.py`` and applies a small,
mechanical SQLite→Postgres translation. The SQLite path is untouched.

Translations (the only dialect deltas in this schema — verified by grep):
  - ``INTEGER PRIMARY KEY AUTOINCREMENT`` → ``BIGSERIAL PRIMARY KEY``
  - ``strftime('%Y-%m-%dT%H:%M:%fZ','now')`` default → ``now()`` (timestamptz-ish
    text; the app also writes ISO strings explicitly, so the default is a
    fallback only)
  - ``REAL`` → ``DOUBLE PRECISION``
  - ``INTEGER`` boolean-ish flag columns stay INTEGER (the app stores 0/1)
Runtime queries are already portable: ``?`` paramstyle is handled by the
connection adapter, and the one upsert (``ON CONFLICT(id) DO UPDATE SET
col = excluded.col``) is valid on both SQLite and Postgres.
"""

from __future__ import annotations

import re

from . import schema as _sqlite_schema


def _to_postgres(ddl: str) -> str:
    """Translate a SQLite CREATE-TABLE/INDEX string to PostgreSQL dialect."""
    out = ddl
    # Autoincrement integer PK → BIGSERIAL (Postgres identity)
    out = re.sub(
        r"INTEGER\s+PRIMARY\s+KEY\s+AUTOINCREMENT",
        "BIGSERIAL PRIMARY KEY",
        out,
        flags=re.IGNORECASE,
    )
    # SQLite strftime(...) default → Postgres now() (both yield a timestamp text;
    # the app writes explicit ISO strings on insert, so this is only a fallback).
    out = re.sub(
        r"strftime\('%Y-%m-%dT%H:%M:%fZ',\s*'now'\)",
        "now()",
        out,
        flags=re.IGNORECASE,
    )
    # REAL → DOUBLE PRECISION (Postgres has no REAL-with-same-semantics alias we
    # want; DOUBLE PRECISION matches the app's float usage).
    out = re.sub(r"\bREAL\b", "DOUBLE PRECISION", out)
    return out


def _translate_all(ddls: list[str]) -> list[str]:
    return [_to_postgres(d) for d in ddls]


# Ordered DDL: version table, then each table + its indexes, mirroring
# schema.py's create_tables() order. We pull the module's DDL constants by name
# so this list stays in lockstep with the source without duplicating SQL.
def postgres_ddl_statements() -> list[str]:
    """Return the full ordered list of Postgres DDL statements."""
    stmts: list[str] = []
    # schema_version first
    stmts.append(_to_postgres(_sqlite_schema.SCHEMA_VERSION_TABLE))
    # Every *_TABLE / *_INDEXES constant defined on the schema module, in
    # definition order, so new tables added upstream are picked up automatically.
    for name in dir(_sqlite_schema):
        if name == "SCHEMA_VERSION_TABLE":
            continue
        val = getattr(_sqlite_schema, name)
        if name.endswith("_TABLE") and isinstance(val, str):
            stmts.append(_to_postgres(val))
        elif name.endswith("_INDEXES") and isinstance(val, list):
            stmts.extend(_translate_all(val))
    return stmts


def initialize_schema_pg(conn) -> None:
    """Create all tables + indexes on a PostgreSQL connection (adapter-wrapped).

    Idempotent (every statement is ``IF NOT EXISTS``). Uses the same connection
    API the SQLite path uses (``execute`` + ``commit``), which the pg adapter
    presents.
    """
    for stmt in postgres_ddl_statements():
        conn.execute(stmt)
    conn.commit()

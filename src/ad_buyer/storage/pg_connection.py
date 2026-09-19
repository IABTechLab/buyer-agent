# Donated to IAB Tech Lab

"""A psycopg connection that presents the ``sqlite3`` API the buyer stores use.

Scope 2 / Req 12.6 — lets the existing synchronous stores talk to PostgreSQL
with NO change to their query code. The stores call ``conn.execute("… ?",
params)``, iterate dict-like rows, and call ``conn.commit()`` — all of which
this adapter maps onto psycopg. The only per-query dialect delta in this schema
is the ``?`` → ``%s`` paramstyle, translated here at the boundary.

NOT a general SQLite emulator — it covers exactly the surface the buyer stores
exercise (execute/executemany, fetchone/fetchall, row-by-name access, commit,
rollback, close, context manager, no-op PRAGMA). Deliberately thin so the
maintainers can later replace it with a unified dialect layer.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


def _qmark_to_pyformat(sql: str) -> str:
    """Translate SQLite ``?`` placeholders to psycopg ``%s``.

    Leaves ``?`` inside single-quoted string literals alone, and escapes any
    literal ``%`` so psycopg does not treat it as a format marker.
    """
    # Escape existing % so psycopg's own %s parsing is not confused.
    sql = sql.replace("%", "%%")
    out = []
    in_str = False
    for ch in sql:
        if ch == "'":
            in_str = not in_str
            out.append(ch)
        elif ch == "?" and not in_str:
            out.append("%s")
        else:
            out.append(ch)
    return "".join(out)


class _Cursor:
    """Wraps a psycopg cursor to accept ``?`` SQL and return dict-like rows."""

    def __init__(self, cur):
        self._cur = cur

    def execute(self, sql: str, params: Any = ()):
        self._cur.execute(_qmark_to_pyformat(sql), params or ())
        return self

    def executemany(self, sql: str, seq):
        self._cur.executemany(_qmark_to_pyformat(sql), list(seq))
        return self

    def fetchone(self):
        return self._cur.fetchone()

    def fetchall(self):
        return self._cur.fetchall()

    @property
    def rowcount(self):
        return self._cur.rowcount

    @property
    def lastrowid(self):
        # Not used by the buyer stores (they use explicit ids / RETURNING-free
        # upserts); return None rather than emulate SQLite rowids.
        return None

    def __iter__(self):
        return iter(self._cur)

    def close(self):
        self._cur.close()


class PgConnection:
    """A ``sqlite3.Connection``-shaped wrapper over a psycopg connection.

    Rows come back as mappings (``psycopg.rows.dict_row``) so the stores'
    ``row["col"]`` access works exactly as with ``sqlite3.Row``.
    """

    def __init__(self, dsn: str):
        import psycopg
        from psycopg.rows import dict_row

        self._conn = psycopg.connect(dsn, row_factory=dict_row, autocommit=False)

    # -- sqlite3.Connection surface used by the stores --------------------
    def execute(self, sql: str, params: Any = ()):
        cur = self._conn.cursor()
        cur.execute(_qmark_to_pyformat(sql), params or ())
        return _Cursor(cur)

    def executemany(self, sql: str, seq):
        cur = self._conn.cursor()
        cur.executemany(_qmark_to_pyformat(sql), list(seq))
        return _Cursor(cur)

    def cursor(self):
        return _Cursor(self._conn.cursor())

    def commit(self):
        self._conn.commit()

    def rollback(self):
        self._conn.rollback()

    def close(self):
        self._conn.close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        if exc_type is None:
            self._conn.commit()
        else:
            self._conn.rollback()
        return False

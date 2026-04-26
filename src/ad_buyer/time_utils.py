# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Time / datetime helpers.

`utc_now()` replaces the deprecated `datetime.utcnow()` (Python 3.12+)
while preserving the existing project-wide convention of NAIVE-UTC
timestamps. Per ar-4e9b: `datetime.utcnow()` is on a deprecation path
in Python 3.12+; the recommended `datetime.now(datetime.UTC)` returns
a TZ-AWARE value, which is semantically correct but would require
synchronized changes across every comparator and every test that
expects naive UTC.

`utc_now()` returns the AWARE UTC datetime stripped of its tzinfo,
giving callers exactly what `datetime.utcnow()` used to return without
the deprecation warning.
"""

from datetime import datetime, timezone

__all__ = ["utc_now"]


def utc_now() -> datetime:
    """Return the current UTC time as a naive `datetime` (tzinfo=None).

    Matches the semantic of the deprecated `datetime.utcnow()` so existing
    comparators / serializers / tests don't have to change.
    """

    return datetime.now(timezone.utc).replace(tzinfo=None)

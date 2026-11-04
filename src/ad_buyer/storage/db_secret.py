# Donated to IAB Tech Lab

"""Assemble the buyer's Postgres DATABASE_URL from the RDS-managed secret.

Scope 2 / Req 12.7. On the AgentCore ``--storage postgres`` path the runtime
receives NON-secret env only — ``DB_SECRET_ARN`` + ``AURORA_ENDPOINT`` +
``AURORA_PORT`` + ``DB_NAME`` — never the password. At startup the app fetches
the RDS-managed secret from Secrets Manager by ARN, parses its JSON
(``{username, password, …}``), and assembles a libpq URL. The password is never
logged and never placed in the environment.

The RDS-managed secret rotates; callers should re-invoke on a connection failure
so a rotated password is picked up (the value is read fresh each call — no
process-level caching of the password).
"""

from __future__ import annotations

import json
import logging
import os
from urllib.parse import quote

logger = logging.getLogger(__name__)


def resolve_database_url() -> str | None:
    """Return a ``postgresql://`` URL from the RDS-managed secret, or None.

    None means "no Postgres configured" — the caller falls back to its default
    (SQLite). Any failure to fetch/parse the secret returns None with a warning
    rather than crashing startup.
    """
    secret_arn = os.environ.get("DB_SECRET_ARN")
    host = os.environ.get("AURORA_ENDPOINT")
    port = os.environ.get("AURORA_PORT", "5432")
    dbname = os.environ.get("DB_NAME", "ad_buyer")
    if not secret_arn or not host:
        return None

    try:
        import boto3

        region = os.environ.get("AWS_REGION", "us-west-2")
        client = boto3.client("secretsmanager", region_name=region)
        resp = client.get_secret_value(SecretId=secret_arn)
        secret = json.loads(resp["SecretString"])
    except Exception as exc:  # noqa: BLE001 — never crash startup on secret fetch
        logger.warning("Could not fetch DB secret (%s); falling back to default storage.", exc)
        return None

    username = secret.get("username")
    password = secret.get("password")
    if not username or not password:
        logger.warning("DB secret missing username/password; falling back to default storage.")
        return None

    # URL-encode credentials so special characters are safe in the libpq URL.
    # NOTE: never log the assembled URL (it contains the password).
    return f"postgresql://{quote(username)}:{quote(password)}@{host}:{port}/{dbname}"

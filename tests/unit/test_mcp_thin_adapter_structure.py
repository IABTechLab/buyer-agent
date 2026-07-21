# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Structural guards keeping the MCP interface a thin adapter (EP-2.2).

The MCP interface layer must not:
  1. open raw ``sqlite3`` connections (that belongs in the storage
     layer -- see ``ad_buyer.storage.health``), or
  2. import private (``_``-prefixed) names out of the ``tools`` package
     (that logic now lives behind ``ad_buyer.services.deal_service``).

These are enforced by static analysis of the module source so a future
regression fails loudly rather than silently re-fattening the adapter.
"""

from __future__ import annotations

import ast
import pathlib

import ad_buyer.interfaces.mcp_server as mcp_server

_SOURCE_PATH = pathlib.Path(mcp_server.__file__)
_TREE = ast.parse(_SOURCE_PATH.read_text())


def _module_is_tools(module: str | None) -> bool:
    """Whether an ImportFrom module path refers to the tools package.

    Handles both absolute (``ad_buyer.tools.deal_import``) and the
    relative form as parsed by ``ast`` for ``from ..tools.x import y``
    (``module='tools.x'``).
    """
    if not module:
        return False
    parts = module.split(".")
    return "tools" in parts


def test_no_raw_sqlite3_import():
    """mcp_server.py must not import the sqlite3 module."""
    imported = set()
    for node in ast.walk(_TREE):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
    assert "sqlite3" not in imported, "mcp_server.py must not import sqlite3 directly"


def test_no_raw_sqlite3_connect_call():
    """mcp_server.py must not open a raw sqlite3 connection."""
    offenders = []
    for node in ast.walk(_TREE):
        if isinstance(node, ast.Attribute) and node.attr == "connect":
            value = node.value
            if isinstance(value, ast.Name) and value.id == "sqlite3":
                offenders.append(node.lineno)
    assert not offenders, f"raw sqlite3.connect() found at lines {offenders}"


def test_no_private_tools_imports():
    """mcp_server.py must not import ``_``-prefixed names from tools.*"""
    offenders = []
    for node in ast.walk(_TREE):
        if isinstance(node, ast.ImportFrom) and _module_is_tools(node.module):
            for alias in node.names:
                if alias.name.startswith("_"):
                    offenders.append(f"{node.module}.{alias.name}")
    assert not offenders, f"private tools imports found: {offenders}"

"""ar-gsd: every BaseTool subclass's `_run`/`_arun` method has a return annotation.

This is a regression guard. As of ar-gsd's audit all 44 (22 sync + 22 async)
methods already carried return-type hints; this test ensures that any new
tool added to `ad_buyer.tools.*` is required to include one.
"""

import importlib
import inspect
import os
import pkgutil

os.environ.setdefault("ANTHROPIC_API_KEY", "test-key-for-unit-tests")

import pytest
from crewai.tools import BaseTool

import ad_buyer.tools as _tools_root


def _discover_tool_methods() -> list[tuple[str, str]]:
    """Walk ad_buyer.tools.* for BaseTool subclasses and yield (qualified_method_name, label)."""

    discovered: list[tuple[str, str]] = []
    for _finder, modname, _ispkg in pkgutil.walk_packages(
        _tools_root.__path__, prefix="ad_buyer.tools."
    ):
        try:
            module = importlib.import_module(modname)
        except Exception:  # noqa: BLE001 — tolerate optional-dep modules
            continue
        for cls_name, cls in inspect.getmembers(module, inspect.isclass):
            if cls is BaseTool or not issubclass(cls, BaseTool):
                continue
            # Only count classes defined in this module (skip re-exports).
            if cls.__module__ != modname:
                continue
            for method_name in ("_run", "_arun"):
                if not hasattr(cls, method_name):
                    continue
                method = getattr(cls, method_name)
                if not callable(method):
                    continue
                discovered.append(
                    (f"{cls.__module__}.{cls_name}.{method_name}", method_name)
                )
    return discovered


_TOOL_METHODS = _discover_tool_methods()


@pytest.mark.parametrize("qualname,method_name", _TOOL_METHODS)
def test_tool_method_has_return_annotation(qualname: str, method_name: str) -> None:
    """Every Tool's `_run` / `_arun` must declare a return type."""

    module_path, cls_name, _ = qualname.rsplit(".", 2)
    cls = getattr(importlib.import_module(module_path), cls_name)
    method = getattr(cls, method_name)
    sig = inspect.signature(method)
    assert sig.return_annotation is not inspect.Signature.empty, (
        f"{qualname} is missing a return-type annotation. "
        "Per ar-gsd, every Tool _run/_arun must declare one."
    )


def test_at_least_one_tool_discovered() -> None:
    """Defense in depth: ensure the discovery walk actually finds tools."""

    assert len(_TOOL_METHODS) > 10, (
        f"Discovery found only {len(_TOOL_METHODS)} tool methods — likely a "
        "broken walk_packages scan, not a code regression."
    )

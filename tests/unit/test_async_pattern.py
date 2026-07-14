# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Tests for the safe async execution pattern.

Verifies that run_async works both when no event loop is running
(standalone context) and when called from within an already-running
event loop (CrewAI/FastAPI context) -- and that it does so WITHOUT
monkeypatching the running loop via nest_asyncio (EP-2.5: one async
boundary, delete the nest_asyncio monkeypatch).
"""

import ast
import asyncio
import threading
from pathlib import Path

import pytest

from ad_buyer.async_utils import run_async

SRC_ROOT = Path(__file__).resolve().parents[2] / "src"


async def _sample_coroutine(value: int) -> int:
    """Simple coroutine for testing."""
    await asyncio.sleep(0)  # Yield to event loop
    return value * 2


def test_run_async_no_running_loop():
    """run_async works when no event loop is running (standalone)."""
    result = run_async(_sample_coroutine(21))
    assert result == 42


@pytest.mark.asyncio
async def test_run_async_inside_running_loop():
    """run_async works when called from within an already-running event loop.

    This is the exact scenario that causes RuntimeError with asyncio.run().
    CrewAI and FastAPI both run their own event loops, so tools called
    from those contexts hit this case.
    """
    result = run_async(_sample_coroutine(21))
    assert result == 42


@pytest.mark.asyncio
async def test_run_async_inside_running_loop_does_not_patch_loop():
    """The running loop is never re-entered nor patched by nest_asyncio.

    The worker-thread seam must drive the coroutine on a *separate* thread
    with its own fresh loop. So (a) the caller's running loop must remain
    unpatched (no ``_nest_patched`` attribute set by nest_asyncio), and
    (b) the coroutine body must execute on a different thread than the
    caller's.
    """
    caller_loop = asyncio.get_running_loop()
    caller_thread_id = threading.get_ident()
    observed: dict[str, int] = {}

    async def _record() -> int:
        observed["thread_id"] = threading.get_ident()
        return 7

    result = run_async(_record())

    assert result == 7
    # Ran on a dedicated worker thread, not by re-entering the caller loop.
    assert observed["thread_id"] != caller_thread_id
    # nest_asyncio.apply() sets ``loop._nest_patched = True``; it must not
    # have been applied to the caller's running loop.
    assert not getattr(caller_loop, "_nest_patched", False)
    # The caller's loop is still the running loop and untouched.
    assert asyncio.get_running_loop() is caller_loop


def test_run_async_propagates_exceptions():
    """run_async propagates exceptions from the coroutine."""

    async def _failing():
        raise ValueError("test error")

    with pytest.raises(ValueError, match="test error"):
        run_async(_failing())


@pytest.mark.asyncio
async def test_run_async_propagates_exceptions_in_running_loop():
    """run_async propagates exceptions even inside a running loop."""

    async def _failing():
        raise ValueError("test error")

    with pytest.raises(ValueError, match="test error"):
        run_async(_failing())


def test_nest_asyncio_not_imported_anywhere_in_src():
    """No module under src/ may import or reference nest_asyncio in code.

    EP-2.5 deletes the nest_asyncio monkeypatch entirely. This AST-based
    guard fails if any ``import nest_asyncio`` / ``from nest_asyncio ...``
    or any live ``nest_asyncio.<attr>`` code reference creeps back in.
    It walks the AST (not raw text) so an explanatory docstring or comment
    that merely names the deleted monkeypatch does not trip it.
    """
    offenders_import: list[str] = []
    offenders_ref: list[str] = []

    for path in SRC_ROOT.rglob("*.py"):
        rel = str(path.relative_to(SRC_ROOT))
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                if any(alias.name.split(".")[0] == "nest_asyncio" for alias in node.names):
                    offenders_import.append(rel)
            elif isinstance(node, ast.ImportFrom):
                if node.module and node.module.split(".")[0] == "nest_asyncio":
                    offenders_import.append(rel)
            elif isinstance(node, ast.Name) and node.id == "nest_asyncio":
                offenders_ref.append(rel)

    assert not offenders_import, f"nest_asyncio imported in: {offenders_import}"
    assert not offenders_ref, f"nest_asyncio referenced in code in: {offenders_ref}"


def _run_async_in_fresh_thread(coro_factory) -> object:
    """Run run_async(coro_factory()) on a brand-new thread with no loop.

    Used so the standalone (no-running-loop) branch of run_async is
    exercised from a clean thread regardless of the caller's context.
    """
    box: dict[str, object] = {}

    def _worker() -> None:
        box["result"] = run_async(coro_factory())

    t = threading.Thread(target=_worker)
    t.start()
    t.join()
    return box["result"]


def test_run_async_uvloop_compat_smoke():
    """Smoke test: run_async works under a uvloop event-loop policy.

    nest_asyncio is hostile to uvloop; the worker-thread seam is not.
    Skipped when uvloop is not installed.
    """
    uvloop = pytest.importorskip("uvloop")

    original_policy = asyncio.get_event_loop_policy()
    asyncio.set_event_loop_policy(uvloop.EventLoopPolicy())
    try:
        # No running loop: run_async -> asyncio.run on a uvloop loop.
        result = _run_async_in_fresh_thread(lambda: _sample_coroutine(21))
        assert result == 42

        # Inside a running uvloop loop: run_async offloads to a worker thread.
        async def _driver() -> int:
            return run_async(_sample_coroutine(50))

        assert asyncio.run(_driver()) == 100
    finally:
        asyncio.set_event_loop_policy(original_policy)

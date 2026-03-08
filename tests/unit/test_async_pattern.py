# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Tests for the safe async execution pattern.

Verifies that run_async works both when no event loop is running
(standalone context) and when called from within an already-running
event loop (CrewAI/FastAPI context).
"""

import asyncio

import pytest

from ad_buyer.async_utils import run_async


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

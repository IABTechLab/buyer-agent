# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Safe async execution utilities.

Provides run_async() which works correctly whether or not an asyncio
event loop is already running. This is needed because CrewAI and FastAPI
run their own event loops, and calling asyncio.run() from within a
running loop raises RuntimeError.
"""

import asyncio
from typing import Any, Coroutine, TypeVar

import nest_asyncio

T = TypeVar("T")


def run_async(coro: Coroutine[Any, Any, T]) -> T:
    """Run an async coroutine safely from synchronous code.

    Works in both standalone contexts (no running loop) and within
    already-running event loops (CrewAI, FastAPI, Jupyter, etc.).

    Args:
        coro: The coroutine to execute.

    Returns:
        The result of the coroutine.
    """
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        # No running loop -- safe to use asyncio.run()
        return asyncio.run(coro)
    else:
        # Already inside a running loop -- patch it with nest_asyncio
        # so we can call run_until_complete without RuntimeError.
        nest_asyncio.apply(loop)
        return loop.run_until_complete(coro)

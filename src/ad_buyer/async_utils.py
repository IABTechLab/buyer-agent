# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Safe async execution utilities.

Provides run_async() which works correctly whether or not an asyncio
event loop is already running. This is needed because CrewAI and FastAPI
run their own event loops, and calling asyncio.run() from within a
running loop raises RuntimeError.

Design (EP-2.5 -- one async boundary):
    There is exactly one async seam here and it never monkeypatches the
    running loop. The canonical rule is that the event loop never runs a
    crew and a crew thread never re-enters the loop:

    * No running loop  -> drive the coroutine directly with asyncio.run().
    * Running loop      -> hand the coroutine to a dedicated worker thread
                           that owns a fresh event loop, and block for the
                           result. The caller's loop is never re-entered and
                           is never patched.

    This deliberately avoids ``nest_asyncio.apply()`` (which re-patches the
    running loop, is hostile to uvloop, and is the root cause of a
    loop-blocking bug class).
"""

import asyncio
import concurrent.futures
from collections.abc import Coroutine
from typing import Any, TypeVar

T = TypeVar("T")


def _run_on_fresh_loop(coro: Coroutine[Any, Any, T]) -> T:
    """Drive ``coro`` to completion on a brand-new event loop.

    Runs in whatever thread calls it. Always creates, uses and closes its
    own loop so it never touches (or depends on) any loop that may already
    be running in another thread.
    """
    loop = asyncio.new_event_loop()
    try:
        asyncio.set_event_loop(loop)
        return loop.run_until_complete(coro)
    finally:
        try:
            asyncio.set_event_loop(None)
        finally:
            loop.close()


def run_async(coro: Coroutine[Any, Any, T]) -> T:
    """Run an async coroutine safely from synchronous code.

    Works in both standalone contexts (no running loop) and within
    already-running event loops (CrewAI, FastAPI, Jupyter, etc.).

    Args:
        coro: The coroutine to execute.

    Returns:
        The result of the coroutine.

    Raises:
        Any exception raised by the coroutine is propagated to the caller,
        identical to ``asyncio.run``/``loop.run_until_complete`` semantics.
    """
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        # No running loop -- safe to drive the coroutine directly.
        return asyncio.run(coro)

    # Already inside a running loop. Never re-enter or patch it: run the
    # coroutine on a dedicated worker thread that owns its own fresh loop
    # and block for the result. Exceptions propagate via Future.result().
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(_run_on_fresh_loop, coro).result()

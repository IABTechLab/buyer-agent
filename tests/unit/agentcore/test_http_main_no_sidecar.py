"""The AgentCore HTTP entrypoint must not run a background REST sidecar.

``http_main.py`` used to start the full buyer FastAPI app in a background
uvicorn thread on port 8001 and export ``BUYER_API_URL``, on the stated
premise that ``DealBookingFlow`` used the buyer's REST API internally. It
doesn't: ``DealBookingFlow`` is called in-process (see ``crew_tools.py``),
and nothing in ``src/`` ever reads ``BUYER_API_URL``. The sidecar was dead
code that bound 0.0.0.0:8001 for no one. These tests lock its removal in,
mirroring the seller-agent precedent for the same pattern.
"""

import sys
from unittest.mock import MagicMock

# Mock bedrock_agentcore before importing the entrypoint, since it's
# imported at module level and is only available inside the AgentCore
# container. See test_routing_mode.py for the same pattern.
_mock_agentcore = MagicMock()
_mock_app = MagicMock()
_mock_app.entrypoint = lambda fn: fn
_mock_agentcore.BedrockAgentCoreApp.return_value = _mock_app
sys.modules.setdefault("bedrock_agentcore", MagicMock())
sys.modules.setdefault("bedrock_agentcore.runtime", _mock_agentcore)

from ad_buyer.interfaces.agentcore import http_main  # noqa: E402


def test_no_background_fastapi_helper():
    """The background-uvicorn sidecar helper and its state must be gone."""
    assert not hasattr(http_main, "_start_fastapi_background")
    assert not hasattr(http_main, "_INTERNAL_PORT")
    assert not hasattr(http_main, "_fastapi_started")


def test_source_has_no_sidecar_machinery():
    """No uvicorn/threading/port-8001/BUYER_API_URL references remain."""
    import inspect

    source = inspect.getsource(http_main)
    lowered = source.lower()
    assert "uvicorn" not in lowered
    assert "8001" not in source
    assert "threading" not in lowered
    assert "internal_api_port" not in lowered
    assert "buyer_api_url" not in lowered


def test_main_block_does_not_start_sidecar():
    """The ``__main__`` block only runs the AgentCore app, nothing else."""
    import ast
    import inspect

    tree = ast.parse(inspect.getsource(http_main))
    main_block = next(
        (
            node
            for node in tree.body
            if isinstance(node, ast.If)
            and isinstance(node.test, ast.Compare)
            and getattr(node.test.left, "id", None) == "__name__"
        ),
        None,
    )
    assert main_block is not None, "expected an `if __name__ == '__main__':` block"
    calls = [
        ast.dump(stmt.value.func)
        for stmt in main_block.body
        if isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Call)
    ]
    assert not any("_start_fastapi_background" in c for c in calls)

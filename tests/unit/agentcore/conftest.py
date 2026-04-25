"""Conftest for agentcore unit tests.

These tests require mocking bedrock_agentcore which is only available
inside the AgentCore container. To avoid polluting sys.modules for
other test files in the same pytest session, we use subprocess isolation
via the pytest-forked plugin when available, or accept that these tests
must run in a separate pytest invocation.

Run agentcore tests separately:
    pytest tests/unit/agentcore/ -v

Run all tests (agentcore mock may affect other tests):
    pytest tests/unit/ -v
"""

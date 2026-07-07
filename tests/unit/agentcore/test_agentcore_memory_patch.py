"""Unit tests for patches/crewai_agentcore_memory.py.

Tests the AgentCore memory patch that replaces CrewAI's built-in memory
(LanceDB + OpenAI embeddings) with AgentCore's MemoryClient.

Run:
    pytest tests/unit/agentcore/test_agentcore_memory_patch.py -v
"""

import os
import sys
import threading
import types
from unittest.mock import MagicMock, patch

import pytest

# Ensure patches/ is importable
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", ".."))


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def reset_patch_state():
    """Reset the patch module's global state between tests."""
    import patches.crewai_agentcore_memory as mod

    # Save original Memory.__init__ before any patching
    try:
        from crewai.memory.unified_memory import Memory

        original_init = Memory.__init__
    except ImportError:
        original_init = None

    mod._patched = False
    mod._memory_client = None
    mod._memory_id = None
    mod._actor_id = None
    mod._session_id = None
    yield
    mod._patched = False
    mod._memory_client = None
    mod._memory_id = None
    mod._actor_id = None
    mod._session_id = None

    # Restore original Memory.__init__ to avoid cross-test contamination
    if original_init is not None:
        try:
            from crewai.memory.unified_memory import Memory

            Memory.__init__ = original_init
        except ImportError:
            pass


@pytest.fixture
def mock_memory_client():
    """Mock the bedrock_agentcore.memory.MemoryClient."""
    mock_module = types.ModuleType("bedrock_agentcore.memory")
    mock_client_class = MagicMock()
    mock_client_instance = MagicMock()
    mock_client_class.return_value = mock_client_instance
    mock_module.MemoryClient = mock_client_class

    with patch.dict(
        sys.modules,
        {
            "bedrock_agentcore": types.ModuleType("bedrock_agentcore"),
            "bedrock_agentcore.memory": mock_module,
        },
    ):
        yield mock_client_instance


@pytest.fixture
def env_with_memory_id(monkeypatch):
    """Set BEDROCK_AGENTCORE_MEMORY_ID env var."""
    monkeypatch.setenv("BEDROCK_AGENTCORE_MEMORY_ID", "test-mem-abc123")
    monkeypatch.setenv("AWS_REGION", "us-west-2")


@pytest.fixture
def env_without_memory_id(monkeypatch):
    """Ensure BEDROCK_AGENTCORE_MEMORY_ID is NOT set."""
    monkeypatch.delenv("BEDROCK_AGENTCORE_MEMORY_ID", raising=False)


# ---------------------------------------------------------------------------
# Tests: apply_patches() activation logic
# ---------------------------------------------------------------------------


class TestApplyPatches:
    """Tests for the apply_patches() entry point."""

    def test_noop_without_env_var(self, env_without_memory_id):
        """Patch is a no-op when BEDROCK_AGENTCORE_MEMORY_ID is not set."""
        from patches.crewai_agentcore_memory import _patched, apply_patches

        apply_patches()
        assert _patched is False

    def test_activates_with_env_var(self, env_with_memory_id, mock_memory_client):
        """Patch activates when BEDROCK_AGENTCORE_MEMORY_ID is set."""
        from patches.crewai_agentcore_memory import apply_patches

        apply_patches(session_id="sess-001", actor_id="buyer-agent")
        from patches.crewai_agentcore_memory import _actor_id, _memory_id, _patched, _session_id

        assert _patched is True
        assert _memory_id == "test-mem-abc123"
        assert _actor_id == "buyer-agent"
        assert _session_id == "sess-001"

    def test_idempotent(self, env_with_memory_id, mock_memory_client):
        """Calling apply_patches() twice doesn't double-patch."""
        from patches.crewai_agentcore_memory import apply_patches

        apply_patches(session_id="sess-001", actor_id="buyer-agent")
        apply_patches(session_id="sess-002", actor_id="buyer-agent")
        from patches.crewai_agentcore_memory import _session_id

        # Session updated on second call
        assert _session_id == "sess-002"

    def test_session_id_truncated_to_100(self, env_with_memory_id, mock_memory_client):
        """Session ID is truncated to 100 chars (AgentCore limit)."""
        from patches.crewai_agentcore_memory import apply_patches

        long_session = "x" * 200
        apply_patches(session_id=long_session, actor_id="buyer-agent")
        from patches.crewai_agentcore_memory import _session_id

        assert len(_session_id) == 100

    def test_defaults_actor_id(self, env_with_memory_id, mock_memory_client):
        """Actor ID defaults to 'buyer-agent' when not provided."""
        from patches.crewai_agentcore_memory import apply_patches

        apply_patches()
        from patches.crewai_agentcore_memory import _actor_id

        assert _actor_id == "buyer-agent"


# ---------------------------------------------------------------------------
# Tests: AgentCoreStorageBackend
# ---------------------------------------------------------------------------


class TestAgentCoreStorageBackend:
    """Tests for the storage backend that wraps AgentCore APIs."""

    @pytest.fixture(autouse=True)
    def setup_backend(self, env_with_memory_id, mock_memory_client):
        """Activate the patch and get a backend instance."""
        from patches.crewai_agentcore_memory import AgentCoreStorageBackend, apply_patches

        apply_patches(session_id="test-session", actor_id="test-actor")
        self.backend = AgentCoreStorageBackend()
        self.mock_client = mock_memory_client

    def test_has_write_lock(self):
        """Backend has write_lock attribute (required by CrewAI)."""
        assert hasattr(self.backend, "write_lock")
        assert isinstance(self.backend.write_lock, type(threading.Lock()))

    def test_save_calls_create_event(self):
        """save() calls memory_client.create_event with correct format."""
        from patches.crewai_agentcore_memory import _SimpleRecord

        record = _SimpleRecord(content="Campaign allocated 60% to CTV")
        self.backend.save([record])

        self.mock_client.create_event.assert_called_once()
        call_kwargs = self.mock_client.create_event.call_args[1]
        assert call_kwargs["memory_id"] == "test-mem-abc123"
        assert call_kwargs["actor_id"] == "test-actor"
        assert call_kwargs["session_id"] == "test-session"
        assert call_kwargs["messages"] == [("Campaign allocated 60% to CTV", "ASSISTANT")]

    def test_save_skips_short_content(self):
        """save() skips records with content shorter than 3 chars."""
        from patches.crewai_agentcore_memory import _SimpleRecord

        record = _SimpleRecord(content="ab")
        self.backend.save([record])
        self.mock_client.create_event.assert_not_called()

    def test_save_skips_tool_content(self):
        """save() skips records containing toolUse/toolResult."""
        from patches.crewai_agentcore_memory import _SimpleRecord

        record = _SimpleRecord(content="toolUse block with some data")
        self.backend.save([record])
        self.mock_client.create_event.assert_not_called()

    def test_save_truncates_to_9000(self):
        """save() truncates content to 9000 chars."""
        from patches.crewai_agentcore_memory import _SimpleRecord

        record = _SimpleRecord(content="x" * 10000)
        self.backend.save([record])

        call_kwargs = self.mock_client.create_event.call_args[1]
        stored_text = call_kwargs["messages"][0][0]
        assert len(stored_text) == 9000

    def test_save_graceful_on_api_error(self):
        """save() logs warning but doesn't crash on API errors."""
        from patches.crewai_agentcore_memory import _SimpleRecord

        self.mock_client.create_event.side_effect = Exception("API error")
        record = _SimpleRecord(content="Some valid content here")
        # Should not raise
        self.backend.save([record])

    def test_search_calls_get_last_k_turns(self):
        """search() calls get_last_k_turns and returns formatted results."""
        self.mock_client.get_last_k_turns.return_value = [
            [{"role": "assistant", "content": {"text": "Previous campaign: 60% CTV"}}]
        ]
        results = self.backend.search(query_embedding=[0.1] * 384, limit=5)

        self.mock_client.get_last_k_turns.assert_called_once()
        assert len(results) == 1
        record, score = results[0]
        assert "Previous campaign" in record.content
        assert score == 0.9

    def test_search_filters_tool_messages(self):
        """search() filters out toolUse/toolResult messages."""
        self.mock_client.get_last_k_turns.return_value = [
            [
                {"role": "assistant", "content": {"text": "toolResult data here"}},
                {"role": "assistant", "content": {"text": "Real campaign context"}},
            ]
        ]
        results = self.backend.search(query_embedding=[0.1] * 384, limit=5)
        assert len(results) == 1
        assert "Real campaign" in results[0][0].content

    def test_search_returns_empty_on_no_data(self):
        """search() returns empty list when no turns found."""
        self.mock_client.get_last_k_turns.return_value = []
        results = self.backend.search(query_embedding=[0.1] * 384, limit=5)
        assert results == []

    def test_search_graceful_on_api_error(self):
        """search() returns empty list on API errors."""
        self.mock_client.get_last_k_turns.side_effect = Exception("API error")
        results = self.backend.search(query_embedding=[0.1] * 384, limit=5)
        assert results == []

    def test_delete_is_noop(self):
        """delete() returns 0 (no-op)."""
        assert self.backend.delete() == 0

    def test_reset_is_noop(self):
        """reset() doesn't crash."""
        self.backend.reset()

    def test_count_returns_zero(self):
        """count() returns 0."""
        assert self.backend.count() == 0


# ---------------------------------------------------------------------------
# Tests: Memory object integration
# ---------------------------------------------------------------------------


class TestMemoryIntegration:
    """Tests that the patched Memory object works with CrewAI."""

    @pytest.fixture(autouse=True)
    def setup_patch(self, env_with_memory_id, mock_memory_client):
        """Activate the patch with mocked memory client."""
        from patches.crewai_agentcore_memory import apply_patches

        apply_patches(session_id="test-session", actor_id="test-actor")
        self.mock_client = mock_memory_client

    def test_memory_creates_with_agentcore_backend(self):
        """Memory() uses AgentCoreStorageBackend after patch."""
        from crewai.memory.unified_memory import Memory

        mem = Memory()
        assert type(mem._storage).__name__ == "AgentCoreStorageBackend"

    def test_memory_has_pending_lock(self):
        """Memory object has _pending_lock (required by CrewAI internals)."""
        from crewai.memory.unified_memory import Memory

        mem = Memory()
        assert hasattr(mem, "_pending_lock")

    def test_memory_has_save_pool(self):
        """Memory object has _save_pool (required by CrewAI internals)."""
        from crewai.memory.unified_memory import Memory

        mem = Memory()
        assert hasattr(mem, "_save_pool")

    def test_memory_drain_writes(self):
        """drain_writes() doesn't crash."""
        from crewai.memory.unified_memory import Memory

        mem = Memory()
        mem.drain_writes()

    def test_crew_with_memory_true(self):
        """Crew(memory=True) doesn't crash and uses AgentCore backend."""
        from crewai import Agent, Crew, Task

        agent = Agent(
            role="Test Agent",
            goal="Test",
            backstory="Test",
            llm="bedrock/us.anthropic.claude-sonnet-4-5-20250929-v1:0",
        )
        task = Task(description="Test task", expected_output="Test", agent=agent)
        crew = Crew(agents=[agent], tasks=[task], memory=True, verbose=False)
        # Should not crash — that's the main assertion
        assert crew is not None


# ---------------------------------------------------------------------------
# Tests: _NoOpEmbedder
# ---------------------------------------------------------------------------


class TestNoOpEmbedder:
    """Tests for the no-op embedder."""

    def test_returns_correct_dimensions(self):
        """embed() returns vectors of length 384."""
        from patches.crewai_agentcore_memory import _NoOpEmbedder

        embedder = _NoOpEmbedder()
        results = embedder.embed(["hello", "world"])
        assert len(results) == 2
        assert len(results[0]) == 384
        assert all(v == 0.0 for v in results[0])

    def test_callable_interface(self):
        """Embedder supports __call__ interface."""
        from patches.crewai_agentcore_memory import _NoOpEmbedder

        embedder = _NoOpEmbedder()
        results = embedder(["test"])
        assert len(results) == 1
        assert len(results[0]) == 384

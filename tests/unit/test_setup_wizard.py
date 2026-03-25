# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Tests for the Setup Wizard Service.

Tests wizard initialization, state tracking, step validation, defaults,
skip/complete logic, auto-detection, and MCP tool integration.
"""

import json
from datetime import datetime, timezone
from unittest.mock import patch

import pytest

from ad_buyer.services.setup_wizard import (
    SetupWizard,
    WizardPhase,
    WizardState,
    WizardStep,
    WizardStepStatus,
)


# ---------------------------------------------------------------------------
# Wizard Initialization
# ---------------------------------------------------------------------------


class TestWizardInitialization:
    """Test wizard creation and initial state."""

    def test_wizard_creates_with_default_state(self):
        """A new wizard should have all 8 steps in not_started status."""
        wizard = SetupWizard()
        state = wizard.get_state()
        assert len(state.steps) == 8

    def test_wizard_has_correct_step_numbers(self):
        """Steps should be numbered 1 through 8."""
        wizard = SetupWizard()
        state = wizard.get_state()
        step_numbers = [s.step_number for s in state.steps]
        assert step_numbers == [1, 2, 3, 4, 5, 6, 7, 8]

    def test_wizard_initial_phase_is_developer(self):
        """New wizard should start in the developer phase."""
        wizard = SetupWizard()
        state = wizard.get_state()
        assert state.current_phase == WizardPhase.DEVELOPER

    def test_wizard_initial_completed_false(self):
        """New wizard should not be marked complete."""
        wizard = SetupWizard()
        state = wizard.get_state()
        assert state.completed is False

    def test_all_steps_start_not_started(self):
        """All steps should initially be not_started."""
        wizard = SetupWizard()
        state = wizard.get_state()
        for step in state.steps:
            assert step.status == WizardStepStatus.NOT_STARTED


# ---------------------------------------------------------------------------
# Step Definitions
# ---------------------------------------------------------------------------


class TestStepDefinitions:
    """Test that all 8 steps have correct metadata."""

    def test_step_1_deploy_environment(self):
        """Step 1 should be Deploy & Environment in developer phase."""
        wizard = SetupWizard()
        step = wizard.get_step(1)
        assert step.step_number == 1
        assert step.title == "Deploy & Environment"
        assert step.phase == WizardPhase.DEVELOPER

    def test_step_2_seller_connections(self):
        """Step 2 should be Seller Connections in developer phase."""
        wizard = SetupWizard()
        step = wizard.get_step(2)
        assert step.step_number == 2
        assert step.title == "Seller Connections"
        assert step.phase == WizardPhase.DEVELOPER

    def test_step_3_generate_credentials(self):
        """Step 3 should be Generate Operator Credentials in developer phase."""
        wizard = SetupWizard()
        step = wizard.get_step(3)
        assert step.step_number == 3
        assert step.title == "Generate Operator Credentials"
        assert step.phase == WizardPhase.DEVELOPER

    def test_step_4_buyer_identity(self):
        """Step 4 should be Buyer Identity in business phase."""
        wizard = SetupWizard()
        step = wizard.get_step(4)
        assert step.step_number == 4
        assert step.title == "Buyer Identity"
        assert step.phase == WizardPhase.BUSINESS

    def test_step_5_deal_preferences(self):
        """Step 5 should be Deal Preferences in business phase."""
        wizard = SetupWizard()
        step = wizard.get_step(5)
        assert step.step_number == 5
        assert step.title == "Deal Preferences"
        assert step.phase == WizardPhase.BUSINESS

    def test_step_6_campaign_defaults(self):
        """Step 6 should be Campaign Defaults in business phase."""
        wizard = SetupWizard()
        step = wizard.get_step(6)
        assert step.step_number == 6
        assert step.title == "Campaign Defaults"
        assert step.phase == WizardPhase.BUSINESS

    def test_step_7_approval_gates(self):
        """Step 7 should be Approval Gates in business phase."""
        wizard = SetupWizard()
        step = wizard.get_step(7)
        assert step.step_number == 7
        assert step.title == "Approval Gates"
        assert step.phase == WizardPhase.BUSINESS

    def test_step_8_review_launch(self):
        """Step 8 should be Review & Launch in business phase."""
        wizard = SetupWizard()
        step = wizard.get_step(8)
        assert step.step_number == 8
        assert step.title == "Review & Launch"
        assert step.phase == WizardPhase.BUSINESS

    def test_invalid_step_number_raises(self):
        """Getting a non-existent step should raise ValueError."""
        wizard = SetupWizard()
        with pytest.raises(ValueError, match="Invalid step number"):
            wizard.get_step(0)
        with pytest.raises(ValueError, match="Invalid step number"):
            wizard.get_step(9)

    def test_each_step_has_description(self):
        """Every step should have a non-empty description."""
        wizard = SetupWizard()
        for i in range(1, 9):
            step = wizard.get_step(i)
            assert step.description, f"Step {i} has no description"

    def test_each_step_has_config_fields(self):
        """Every step except Review & Launch should have config_fields."""
        wizard = SetupWizard()
        for i in range(1, 8):
            step = wizard.get_step(i)
            assert len(step.config_fields) > 0, f"Step {i} has no config_fields"

    def test_step_8_has_no_config_fields(self):
        """Review & Launch should have no config_fields (it's a review step)."""
        wizard = SetupWizard()
        step = wizard.get_step(8)
        assert len(step.config_fields) == 0


# ---------------------------------------------------------------------------
# Step Defaults
# ---------------------------------------------------------------------------


class TestStepDefaults:
    """Test that each step provides sensible defaults."""

    def test_step_1_defaults(self):
        """Step 1 should provide default deployment target and storage backend."""
        wizard = SetupWizard()
        step = wizard.get_step(1)
        defaults = step.defaults
        assert "deployment_target" in defaults
        assert "storage_backend" in defaults

    def test_step_2_defaults(self):
        """Step 2 should provide default seller endpoints."""
        wizard = SetupWizard()
        step = wizard.get_step(2)
        defaults = step.defaults
        assert "seller_endpoints" in defaults

    def test_step_3_defaults(self):
        """Step 3 defaults should be empty (credentials are generated)."""
        wizard = SetupWizard()
        step = wizard.get_step(3)
        # Credentials are generated, not defaulted
        assert isinstance(step.defaults, dict)

    def test_step_4_defaults(self):
        """Step 4 should have default agency_name."""
        wizard = SetupWizard()
        step = wizard.get_step(4)
        defaults = step.defaults
        assert "agency_name" in defaults

    def test_step_5_defaults(self):
        """Step 5 should have default deal types."""
        wizard = SetupWizard()
        step = wizard.get_step(5)
        defaults = step.defaults
        assert "default_deal_types" in defaults

    def test_step_6_defaults(self):
        """Step 6 should have default budget template."""
        wizard = SetupWizard()
        step = wizard.get_step(6)
        defaults = step.defaults
        assert "default_budget_currency" in defaults

    def test_step_7_defaults(self):
        """Step 7 should have default auto-approve threshold."""
        wizard = SetupWizard()
        step = wizard.get_step(7)
        defaults = step.defaults
        assert "auto_approve_below" in defaults


# ---------------------------------------------------------------------------
# Complete Step
# ---------------------------------------------------------------------------


class TestCompleteStep:
    """Test completing wizard steps."""

    def test_complete_step_marks_completed(self):
        """Completing a step should mark it as completed."""
        wizard = SetupWizard()
        wizard.complete_step(1, {"deployment_target": "local"})
        step = wizard.get_step(1)
        assert step.status == WizardStepStatus.COMPLETED

    def test_complete_step_stores_config(self):
        """Completing a step should store the provided configuration."""
        wizard = SetupWizard()
        config = {"deployment_target": "local", "storage_backend": "sqlite"}
        wizard.complete_step(1, config)
        step = wizard.get_step(1)
        assert step.config == config

    def test_complete_step_sets_completed_at(self):
        """Completing a step should set completed_at timestamp."""
        wizard = SetupWizard()
        wizard.complete_step(1, {})
        step = wizard.get_step(1)
        assert step.completed_at is not None

    def test_complete_step_with_empty_config_uses_defaults(self):
        """Completing a step with empty config should still work."""
        wizard = SetupWizard()
        wizard.complete_step(1, {})
        step = wizard.get_step(1)
        assert step.status == WizardStepStatus.COMPLETED

    def test_complete_invalid_step_raises(self):
        """Completing a non-existent step should raise ValueError."""
        wizard = SetupWizard()
        with pytest.raises(ValueError, match="Invalid step number"):
            wizard.complete_step(0, {})

    def test_complete_already_completed_step_updates(self):
        """Re-completing a step should update its config."""
        wizard = SetupWizard()
        wizard.complete_step(1, {"deployment_target": "local"})
        wizard.complete_step(1, {"deployment_target": "cloud"})
        step = wizard.get_step(1)
        assert step.config["deployment_target"] == "cloud"


# ---------------------------------------------------------------------------
# Skip Step
# ---------------------------------------------------------------------------


class TestSkipStep:
    """Test skipping wizard steps."""

    def test_skip_step_marks_skipped(self):
        """Skipping a step should mark it as skipped."""
        wizard = SetupWizard()
        wizard.skip_step(1)
        step = wizard.get_step(1)
        assert step.status == WizardStepStatus.SKIPPED

    def test_skip_step_applies_defaults(self):
        """Skipping a step should apply default config values."""
        wizard = SetupWizard()
        wizard.skip_step(1)
        step = wizard.get_step(1)
        # When skipped, the step's defaults should be applied as config
        assert step.config == step.defaults

    def test_skip_invalid_step_raises(self):
        """Skipping a non-existent step should raise ValueError."""
        wizard = SetupWizard()
        with pytest.raises(ValueError, match="Invalid step number"):
            wizard.skip_step(99)

    def test_skip_step_8_raises(self):
        """Step 8 (Review & Launch) should not be skippable."""
        wizard = SetupWizard()
        with pytest.raises(ValueError, match="cannot be skipped"):
            wizard.skip_step(8)


# ---------------------------------------------------------------------------
# Auto-Detection
# ---------------------------------------------------------------------------


class TestAutoDetection:
    """Test auto-detection of completed steps."""

    def test_detect_step_1_when_api_key_set(self):
        """Step 1 should auto-detect as done when API key is configured."""
        wizard = SetupWizard()
        with patch(
            "ad_buyer.services.setup_wizard._get_settings"
        ) as mock_settings:
            mock_settings.return_value.api_key = "test-key-123"
            mock_settings.return_value.database_url = "sqlite:///./ad_buyer.db"
            mock_settings.return_value.environment = "development"
            wizard.auto_detect()
        step = wizard.get_step(1)
        assert step.status == WizardStepStatus.AUTO_DETECTED

    def test_detect_step_1_not_detected_when_no_key(self):
        """Step 1 should not auto-detect without API key."""
        wizard = SetupWizard()
        with patch(
            "ad_buyer.services.setup_wizard._get_settings"
        ) as mock_settings:
            mock_settings.return_value.api_key = ""
            mock_settings.return_value.database_url = "sqlite:///./ad_buyer.db"
            mock_settings.return_value.environment = "development"
            mock_settings.return_value.seller_endpoints = ""
            mock_settings.return_value.get_seller_endpoints.return_value = []
            wizard.auto_detect()
        step = wizard.get_step(1)
        assert step.status == WizardStepStatus.NOT_STARTED

    def test_detect_step_2_when_sellers_configured(self):
        """Step 2 should auto-detect when seller endpoints are configured."""
        wizard = SetupWizard()
        with patch(
            "ad_buyer.services.setup_wizard._get_settings"
        ) as mock_settings:
            mock_settings.return_value.api_key = ""
            mock_settings.return_value.database_url = "sqlite:///./ad_buyer.db"
            mock_settings.return_value.environment = "development"
            mock_settings.return_value.seller_endpoints = "http://seller1.example.com"
            mock_settings.return_value.get_seller_endpoints.return_value = [
                "http://seller1.example.com"
            ]
            wizard.auto_detect()
        step = wizard.get_step(2)
        assert step.status == WizardStepStatus.AUTO_DETECTED

    def test_auto_detect_does_not_override_completed(self):
        """Auto-detection should not override a manually completed step."""
        wizard = SetupWizard()
        wizard.complete_step(1, {"deployment_target": "local"})
        with patch(
            "ad_buyer.services.setup_wizard._get_settings"
        ) as mock_settings:
            mock_settings.return_value.api_key = "test-key"
            mock_settings.return_value.database_url = "sqlite:///./ad_buyer.db"
            mock_settings.return_value.environment = "development"
            wizard.auto_detect()
        step = wizard.get_step(1)
        # Should remain COMPLETED, not overridden to AUTO_DETECTED
        assert step.status == WizardStepStatus.COMPLETED


# ---------------------------------------------------------------------------
# Wizard State Tracking
# ---------------------------------------------------------------------------


class TestWizardStateTracking:
    """Test overall wizard state and progress tracking."""

    def test_progress_percentage_starts_at_zero(self):
        """New wizard should have 0% progress."""
        wizard = SetupWizard()
        state = wizard.get_state()
        assert state.progress_pct == 0.0

    def test_progress_increases_on_complete(self):
        """Completing a step should increase progress."""
        wizard = SetupWizard()
        wizard.complete_step(1, {})
        state = wizard.get_state()
        assert state.progress_pct == pytest.approx(12.5)  # 1/8

    def test_progress_includes_skipped(self):
        """Skipped steps count toward progress."""
        wizard = SetupWizard()
        wizard.skip_step(1)
        state = wizard.get_state()
        assert state.progress_pct == pytest.approx(12.5)

    def test_progress_includes_auto_detected(self):
        """Auto-detected steps count toward progress."""
        wizard = SetupWizard()
        with patch(
            "ad_buyer.services.setup_wizard._get_settings"
        ) as mock_settings:
            mock_settings.return_value.api_key = "test-key"
            mock_settings.return_value.database_url = "sqlite:///./ad_buyer.db"
            mock_settings.return_value.environment = "development"
            mock_settings.return_value.seller_endpoints = ""
            mock_settings.return_value.get_seller_endpoints.return_value = []
            wizard.auto_detect()
        state = wizard.get_state()
        # Step 1 auto-detected = 12.5%
        assert state.progress_pct >= 12.5

    def test_wizard_complete_when_all_done(self):
        """Wizard should be complete when all 8 steps are done/skipped."""
        wizard = SetupWizard()
        for i in range(1, 8):
            wizard.complete_step(i, {})
        # Step 8 must be completed, not skipped
        wizard.complete_step(8, {})
        state = wizard.get_state()
        assert state.completed is True
        assert state.progress_pct == 100.0

    def test_wizard_not_complete_with_7_of_8(self):
        """Wizard should not be complete with only 7 steps done."""
        wizard = SetupWizard()
        for i in range(1, 8):
            wizard.complete_step(i, {})
        state = wizard.get_state()
        assert state.completed is False

    def test_phase_transitions_to_business(self):
        """Phase should transition to business after developer steps are done."""
        wizard = SetupWizard()
        wizard.complete_step(1, {})
        wizard.complete_step(2, {})
        wizard.complete_step(3, {})
        state = wizard.get_state()
        assert state.current_phase == WizardPhase.BUSINESS

    def test_phase_stays_developer_until_all_dev_done(self):
        """Phase should stay developer until all 3 dev steps are done."""
        wizard = SetupWizard()
        wizard.complete_step(1, {})
        wizard.complete_step(2, {})
        state = wizard.get_state()
        assert state.current_phase == WizardPhase.DEVELOPER

    def test_skipped_dev_steps_count_for_phase_transition(self):
        """Skipped developer steps should count for phase transition."""
        wizard = SetupWizard()
        wizard.skip_step(1)
        wizard.skip_step(2)
        wizard.complete_step(3, {})
        state = wizard.get_state()
        assert state.current_phase == WizardPhase.BUSINESS


# ---------------------------------------------------------------------------
# Run Wizard (Full Workflow)
# ---------------------------------------------------------------------------


class TestRunWizard:
    """Test the run_wizard method that returns wizard status."""

    def test_run_wizard_returns_state(self):
        """run_wizard should return current wizard state."""
        wizard = SetupWizard()
        result = wizard.run_wizard()
        assert "steps" in result
        assert "current_phase" in result
        assert "completed" in result
        assert "progress_pct" in result

    def test_run_wizard_auto_detects(self):
        """run_wizard should auto-detect completed steps first."""
        wizard = SetupWizard()
        with patch(
            "ad_buyer.services.setup_wizard._get_settings"
        ) as mock_settings:
            mock_settings.return_value.api_key = "test-key"
            mock_settings.return_value.database_url = "sqlite:///./ad_buyer.db"
            mock_settings.return_value.environment = "development"
            mock_settings.return_value.seller_endpoints = ""
            mock_settings.return_value.get_seller_endpoints.return_value = []
            result = wizard.run_wizard()
        # Step 1 should be auto-detected
        step_1 = [s for s in result["steps"] if s["step_number"] == 1][0]
        assert step_1["status"] == "auto_detected"


# ---------------------------------------------------------------------------
# Serialization
# ---------------------------------------------------------------------------


class TestSerialization:
    """Test wizard state serialization and deserialization."""

    def test_state_to_dict(self):
        """Wizard state should serialize to a dictionary."""
        wizard = SetupWizard()
        wizard.complete_step(1, {"deployment_target": "local"})
        state = wizard.get_state()
        d = state.to_dict()
        assert isinstance(d, dict)
        assert d["completed"] is False
        assert len(d["steps"]) == 8

    def test_state_roundtrip(self):
        """Wizard state should survive a to_dict/from_dict roundtrip."""
        wizard = SetupWizard()
        wizard.complete_step(1, {"deployment_target": "local"})
        wizard.skip_step(2)
        state = wizard.get_state()
        d = state.to_dict()
        restored = WizardState.from_dict(d)
        assert restored.completed == state.completed
        assert restored.progress_pct == state.progress_pct
        assert len(restored.steps) == len(state.steps)
        assert restored.steps[0].status == WizardStepStatus.COMPLETED
        assert restored.steps[1].status == WizardStepStatus.SKIPPED

    def test_step_to_dict(self):
        """Individual step should serialize to a dictionary."""
        wizard = SetupWizard()
        step = wizard.get_step(1)
        d = step.to_dict()
        assert d["step_number"] == 1
        assert d["title"] == "Deploy & Environment"
        assert d["phase"] == "developer"


# ---------------------------------------------------------------------------
# MCP Tool Integration
# ---------------------------------------------------------------------------


class TestMCPToolIntegration:
    """Test that wizard MCP tools are registered and work."""

    @pytest.mark.asyncio
    async def test_mcp_has_wizard_tools(self):
        """MCP server should have wizard-related tools registered."""
        from ad_buyer.interfaces.mcp_server import mcp

        tools_result = await mcp.list_tools()
        tool_names = [t.name for t in tools_result]
        assert "run_setup_wizard" in tool_names
        assert "get_wizard_step" in tool_names
        assert "complete_wizard_step" in tool_names
        assert "skip_wizard_step" in tool_names

    @pytest.mark.asyncio
    async def test_run_setup_wizard_tool(self):
        """run_setup_wizard should return wizard state."""
        from ad_buyer.interfaces.mcp_server import mcp

        result = await mcp.call_tool("run_setup_wizard", {})
        text = result[0][0].text
        data = json.loads(text)
        assert "steps" in data
        assert "progress_pct" in data
        assert "current_phase" in data

    @pytest.mark.asyncio
    async def test_get_wizard_step_tool(self):
        """get_wizard_step should return a specific step's details."""
        from ad_buyer.interfaces.mcp_server import mcp

        result = await mcp.call_tool("get_wizard_step", {"step_number": 1})
        text = result[0][0].text
        data = json.loads(text)
        assert data["step_number"] == 1
        assert data["title"] == "Deploy & Environment"

    @pytest.mark.asyncio
    async def test_get_wizard_step_invalid_number(self):
        """get_wizard_step with invalid step should return error."""
        from ad_buyer.interfaces.mcp_server import mcp

        result = await mcp.call_tool("get_wizard_step", {"step_number": 99})
        text = result[0][0].text
        data = json.loads(text)
        assert "error" in data

    @pytest.mark.asyncio
    async def test_complete_wizard_step_tool(self):
        """complete_wizard_step should complete a step with config."""
        from ad_buyer.interfaces.mcp_server import mcp

        result = await mcp.call_tool(
            "complete_wizard_step",
            {
                "step_number": 4,
                "config": json.dumps({"agency_name": "Test Agency"}),
            },
        )
        text = result[0][0].text
        data = json.loads(text)
        assert data["success"] is True
        assert data["step_number"] == 4

    @pytest.mark.asyncio
    async def test_skip_wizard_step_tool(self):
        """skip_wizard_step should skip a step with defaults."""
        from ad_buyer.interfaces.mcp_server import mcp

        result = await mcp.call_tool("skip_wizard_step", {"step_number": 1})
        text = result[0][0].text
        data = json.loads(text)
        assert data["success"] is True
        assert data["step_number"] == 1

    @pytest.mark.asyncio
    async def test_skip_step_8_returns_error(self):
        """Skipping step 8 should return an error."""
        from ad_buyer.interfaces.mcp_server import mcp

        result = await mcp.call_tool("skip_wizard_step", {"step_number": 8})
        text = result[0][0].text
        data = json.loads(text)
        assert "error" in data


# ---------------------------------------------------------------------------
# Persistence (state save/load via JSON file)
# ---------------------------------------------------------------------------


class TestPersistence:
    """Test wizard state persistence."""

    def test_save_and_load_state(self, tmp_path):
        """Wizard state should persist to and load from a file."""
        state_file = tmp_path / "wizard_state.json"
        wizard = SetupWizard(state_file=str(state_file))
        wizard.complete_step(1, {"deployment_target": "local"})
        wizard.save()

        # Load into a new wizard
        wizard2 = SetupWizard(state_file=str(state_file))
        wizard2.load()
        step = wizard2.get_step(1)
        assert step.status == WizardStepStatus.COMPLETED
        assert step.config["deployment_target"] == "local"

    def test_load_from_nonexistent_file_is_fresh(self, tmp_path):
        """Loading from a non-existent file should give a fresh wizard."""
        state_file = tmp_path / "does_not_exist.json"
        wizard = SetupWizard(state_file=str(state_file))
        wizard.load()
        state = wizard.get_state()
        assert state.progress_pct == 0.0

    def test_save_creates_file(self, tmp_path):
        """save() should create the state file."""
        state_file = tmp_path / "wizard_state.json"
        wizard = SetupWizard(state_file=str(state_file))
        wizard.save()
        assert state_file.exists()

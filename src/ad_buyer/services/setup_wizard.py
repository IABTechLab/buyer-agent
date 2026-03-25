# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Setup Wizard Service for the Ad Buyer Agent.

Two-phase wizard with 8 steps for configuring the buyer agent:

Developer Phase (Claude Code):
  1. Deploy & Environment -- deployment target, API keys, storage backend
  2. Seller Connections -- configure seller agent URLs, test connectivity
  3. Generate Operator Credentials -- create operator API key and config

Business Phase (Claude Desktop):
  4. Buyer Identity -- agency name, seat IDs, organization details
  5. Deal Preferences -- default deal types, pricing thresholds, media types
  6. Campaign Defaults -- budget templates, pacing preferences, dates
  7. Approval Gates -- deal size thresholds, auto-approve, escalation
  8. Review & Launch -- verify all settings, run health check, confirm ready

Steps are skippable with sensible defaults (except Review & Launch).
Auto-detect completed steps from existing configuration.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any

from ..config.settings import Settings

logger = logging.getLogger(__name__)


def _get_settings() -> Settings:
    """Get a fresh Settings instance for auto-detection."""
    return Settings()


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class WizardPhase(str, Enum):
    """Two-phase wizard model."""

    DEVELOPER = "developer"
    BUSINESS = "business"


class WizardStepStatus(str, Enum):
    """Status of an individual wizard step."""

    NOT_STARTED = "not_started"
    COMPLETED = "completed"
    SKIPPED = "skipped"
    AUTO_DETECTED = "auto_detected"


# ---------------------------------------------------------------------------
# Step Definition
# ---------------------------------------------------------------------------


class WizardStep:
    """A single wizard step with metadata, defaults, and state."""

    def __init__(
        self,
        step_number: int,
        title: str,
        description: str,
        phase: WizardPhase,
        config_fields: list[str],
        defaults: dict[str, Any],
    ) -> None:
        self.step_number = step_number
        self.title = title
        self.description = description
        self.phase = phase
        self.config_fields = config_fields
        self.defaults = dict(defaults)  # copy to avoid mutation
        self.status = WizardStepStatus.NOT_STARTED
        self.config: dict[str, Any] = {}
        self.completed_at: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """Serialize step to dictionary."""
        return {
            "step_number": self.step_number,
            "title": self.title,
            "description": self.description,
            "phase": self.phase.value,
            "config_fields": self.config_fields,
            "defaults": self.defaults,
            "status": self.status.value,
            "config": self.config,
            "completed_at": self.completed_at,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> WizardStep:
        """Deserialize step from dictionary."""
        step = cls(
            step_number=d["step_number"],
            title=d["title"],
            description=d["description"],
            phase=WizardPhase(d["phase"]),
            config_fields=d.get("config_fields", []),
            defaults=d.get("defaults", {}),
        )
        step.status = WizardStepStatus(d.get("status", "not_started"))
        step.config = d.get("config", {})
        step.completed_at = d.get("completed_at")
        return step


# ---------------------------------------------------------------------------
# Wizard State
# ---------------------------------------------------------------------------


class WizardState:
    """Overall wizard state tracking all steps."""

    def __init__(self, steps: list[WizardStep]) -> None:
        self.steps = steps

    @property
    def completed(self) -> bool:
        """Whether all steps are done (completed, skipped, or auto-detected)."""
        return all(
            s.status
            in (
                WizardStepStatus.COMPLETED,
                WizardStepStatus.SKIPPED,
                WizardStepStatus.AUTO_DETECTED,
            )
            for s in self.steps
        )

    @property
    def progress_pct(self) -> float:
        """Percentage of steps that are done."""
        if not self.steps:
            return 0.0
        done = sum(
            1
            for s in self.steps
            if s.status
            in (
                WizardStepStatus.COMPLETED,
                WizardStepStatus.SKIPPED,
                WizardStepStatus.AUTO_DETECTED,
            )
        )
        return done / len(self.steps) * 100.0

    @property
    def current_phase(self) -> WizardPhase:
        """Current phase based on developer step completion."""
        developer_steps = [
            s for s in self.steps if s.phase == WizardPhase.DEVELOPER
        ]
        all_dev_done = all(
            s.status
            in (
                WizardStepStatus.COMPLETED,
                WizardStepStatus.SKIPPED,
                WizardStepStatus.AUTO_DETECTED,
            )
            for s in developer_steps
        )
        if all_dev_done:
            return WizardPhase.BUSINESS
        return WizardPhase.DEVELOPER

    def to_dict(self) -> dict[str, Any]:
        """Serialize state to dictionary."""
        return {
            "steps": [s.to_dict() for s in self.steps],
            "completed": self.completed,
            "progress_pct": self.progress_pct,
            "current_phase": self.current_phase.value,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> WizardState:
        """Deserialize state from dictionary."""
        steps = [WizardStep.from_dict(sd) for sd in d["steps"]]
        return cls(steps=steps)


# ---------------------------------------------------------------------------
# Step Definitions (the 8 wizard steps)
# ---------------------------------------------------------------------------


def _build_steps() -> list[WizardStep]:
    """Create the 8 wizard steps with metadata and defaults."""
    return [
        # --- Developer Phase ---
        WizardStep(
            step_number=1,
            title="Deploy & Environment",
            description=(
                "Configure deployment target (local, Docker, cloud), "
                "API keys for external services, and storage backend."
            ),
            phase=WizardPhase.DEVELOPER,
            config_fields=[
                "deployment_target",
                "api_key",
                "storage_backend",
                "database_url",
                "environment",
            ],
            defaults={
                "deployment_target": "local",
                "storage_backend": "sqlite",
                "database_url": "sqlite:///./ad_buyer.db",
                "environment": "development",
            },
        ),
        WizardStep(
            step_number=2,
            title="Seller Connections",
            description=(
                "Configure seller agent URLs (MCP/A2A endpoints) and "
                "test connectivity to each seller."
            ),
            phase=WizardPhase.DEVELOPER,
            config_fields=["seller_endpoints"],
            defaults={
                "seller_endpoints": [],
            },
        ),
        WizardStep(
            step_number=3,
            title="Generate Operator Credentials",
            description=(
                "Create an operator API key for authenticating Claude Desktop "
                "and other MCP clients, and generate connection configuration."
            ),
            phase=WizardPhase.DEVELOPER,
            config_fields=[
                "operator_api_key",
                "claude_desktop_config",
            ],
            defaults={},
        ),
        # --- Business Phase ---
        WizardStep(
            step_number=4,
            title="Buyer Identity",
            description=(
                "Set up your agency name, DSP seat IDs, and organization "
                "details for identity-based pricing tiers."
            ),
            phase=WizardPhase.BUSINESS,
            config_fields=[
                "agency_name",
                "agency_id",
                "seat_id",
                "seat_name",
                "agency_holding_company",
            ],
            defaults={
                "agency_name": "My Agency",
                "agency_id": "",
                "seat_id": "",
                "seat_name": "",
            },
        ),
        WizardStep(
            step_number=5,
            title="Deal Preferences",
            description=(
                "Configure default deal types, pricing thresholds, and "
                "preferred media types for deal discovery and negotiation."
            ),
            phase=WizardPhase.BUSINESS,
            config_fields=[
                "default_deal_types",
                "max_cpm_threshold",
                "preferred_media_types",
            ],
            defaults={
                "default_deal_types": ["PD", "PA"],
                "max_cpm_threshold": 50.0,
                "preferred_media_types": ["DIGITAL"],
            },
        ),
        WizardStep(
            step_number=6,
            title="Campaign Defaults",
            description=(
                "Set default budget templates, pacing preferences, "
                "and flight date defaults for new campaigns."
            ),
            phase=WizardPhase.BUSINESS,
            config_fields=[
                "default_budget_currency",
                "default_pacing_strategy",
                "default_flight_duration_days",
            ],
            defaults={
                "default_budget_currency": "USD",
                "default_pacing_strategy": "even",
                "default_flight_duration_days": 30,
            },
        ),
        WizardStep(
            step_number=7,
            title="Approval Gates",
            description=(
                "Configure deal size thresholds for auto-approval, "
                "rules for automatic approvals, and escalation policies."
            ),
            phase=WizardPhase.BUSINESS,
            config_fields=[
                "auto_approve_below",
                "require_approval_above",
                "escalation_email",
            ],
            defaults={
                "auto_approve_below": 5000.0,
                "require_approval_above": 50000.0,
                "escalation_email": "",
            },
        ),
        WizardStep(
            step_number=8,
            title="Review & Launch",
            description=(
                "Review all configured settings, run a system health check, "
                "and confirm the buyer agent is ready for operation."
            ),
            phase=WizardPhase.BUSINESS,
            config_fields=[],
            defaults={},
        ),
    ]


# ---------------------------------------------------------------------------
# SetupWizard
# ---------------------------------------------------------------------------


class SetupWizard:
    """Two-phase setup wizard with 8 steps.

    Args:
        state_file: Optional path for persisting wizard state as JSON.
    """

    def __init__(self, state_file: str | None = None) -> None:
        self._steps = _build_steps()
        self._state_file = state_file

    # -- Step access --------------------------------------------------------

    def get_step(self, step_number: int) -> WizardStep:
        """Get a step by number (1-8).

        Args:
            step_number: Step number from 1 to 8.

        Returns:
            The requested WizardStep.

        Raises:
            ValueError: If step_number is not 1-8.
        """
        if step_number < 1 or step_number > 8:
            raise ValueError(
                f"Invalid step number: {step_number}. Must be 1-8."
            )
        return self._steps[step_number - 1]

    def get_state(self) -> WizardState:
        """Get the current wizard state."""
        return WizardState(steps=self._steps)

    # -- Step operations ----------------------------------------------------

    def complete_step(
        self, step_number: int, config: dict[str, Any]
    ) -> WizardStep:
        """Mark a step as completed with the given configuration.

        Args:
            step_number: Step number (1-8).
            config: Configuration values for this step.

        Returns:
            The updated WizardStep.

        Raises:
            ValueError: If step_number is invalid.
        """
        step = self.get_step(step_number)
        step.status = WizardStepStatus.COMPLETED
        step.config = dict(config)
        step.completed_at = datetime.now(timezone.utc).isoformat()
        return step

    def skip_step(self, step_number: int) -> WizardStep:
        """Skip a step, applying its defaults.

        Step 8 (Review & Launch) cannot be skipped.

        Args:
            step_number: Step number (1-8).

        Returns:
            The updated WizardStep.

        Raises:
            ValueError: If step_number is invalid or step cannot be skipped.
        """
        step = self.get_step(step_number)
        if step_number == 8:
            raise ValueError(
                "Step 8 (Review & Launch) cannot be skipped."
            )
        step.status = WizardStepStatus.SKIPPED
        step.config = dict(step.defaults)
        step.completed_at = datetime.now(timezone.utc).isoformat()
        return step

    # -- Auto-detection -----------------------------------------------------

    def auto_detect(self) -> None:
        """Auto-detect completed steps from existing configuration.

        Checks the current Settings to determine which steps have already
        been configured. Only updates steps that are NOT_STARTED (does not
        override manually completed or skipped steps).
        """
        settings = _get_settings()

        # Step 1: Deploy & Environment -- detect if API key is set
        step1 = self.get_step(1)
        if step1.status == WizardStepStatus.NOT_STARTED:
            if settings.api_key:
                step1.status = WizardStepStatus.AUTO_DETECTED
                step1.config = {
                    "deployment_target": "local",
                    "storage_backend": "sqlite",
                    "database_url": settings.database_url,
                    "environment": settings.environment,
                    "api_key": "(configured)",
                }
                step1.completed_at = datetime.now(timezone.utc).isoformat()

        # Step 2: Seller Connections -- detect if seller endpoints configured
        step2 = self.get_step(2)
        if step2.status == WizardStepStatus.NOT_STARTED:
            endpoints = settings.get_seller_endpoints()
            if endpoints:
                step2.status = WizardStepStatus.AUTO_DETECTED
                step2.config = {
                    "seller_endpoints": endpoints,
                }
                step2.completed_at = datetime.now(timezone.utc).isoformat()

        # Steps 3-8 cannot be auto-detected from settings alone;
        # they require user configuration.

    # -- Run wizard ---------------------------------------------------------

    def run_wizard(self) -> dict[str, Any]:
        """Run the wizard: auto-detect first, then return state.

        Returns:
            Dictionary with wizard state including steps, progress, phase.
        """
        self.auto_detect()
        state = self.get_state()
        return state.to_dict()

    # -- Persistence --------------------------------------------------------

    def save(self) -> None:
        """Save wizard state to the state file."""
        if not self._state_file:
            return
        state = self.get_state()
        data = state.to_dict()
        path = Path(self._state_file)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, indent=2))
        logger.info("Wizard state saved to %s", self._state_file)

    def load(self) -> None:
        """Load wizard state from the state file.

        If the file does not exist, the wizard starts fresh.
        """
        if not self._state_file:
            return
        path = Path(self._state_file)
        if not path.exists():
            return
        try:
            data = json.loads(path.read_text())
            restored = WizardState.from_dict(data)
            self._steps = restored.steps
            logger.info("Wizard state loaded from %s", self._state_file)
        except (json.JSONDecodeError, KeyError) as exc:
            logger.warning(
                "Failed to load wizard state from %s: %s",
                self._state_file,
                exc,
            )

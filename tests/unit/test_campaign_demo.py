# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Tests for the Campaign Automation step-through demo app.

Covers:
  - Flask routes return expected status codes and HTML
  - Brief submission creates a campaign in DRAFT state
  - Plan approval transitions campaign to PLANNING then BOOKING
  - Booking approval triggers creative matching
  - Creative approval finalizes the campaign
  - Campaign report is generated for READY campaigns
  - Campaign activation transitions to ACTIVE with pacing data (Stage 6)
  - Pause and complete controls for active campaigns
  - Sample briefs are pre-seeded
  - Error handling for invalid briefs and missing campaigns

bead: ar-llj4, ar-uxpw
"""

import json

import pytest

from ad_buyer.demo.campaign_demo import create_campaign_demo_app
from ad_buyer.storage.campaign_store import CampaignStore

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def campaign_store():
    """Create an in-memory CampaignStore for testing."""
    store = CampaignStore("sqlite:///:memory:")
    store.connect()
    yield store
    store.disconnect()


@pytest.fixture
def app(campaign_store):
    """Flask test app with in-memory stores."""
    application = create_campaign_demo_app(
        database_url="sqlite:///:memory:",
    )
    application.config["TESTING"] = True
    return application


@pytest.fixture
def client(app):
    """Flask test client."""
    return app.test_client()


# ---------------------------------------------------------------------------
# Sample data
# ---------------------------------------------------------------------------


SAMPLE_BRIEF = {
    "advertiser_id": "ADV-TEST-001",
    "campaign_name": "Test Multi-Channel Campaign",
    "objective": "AWARENESS",
    "total_budget": 500000,
    "currency": "USD",
    "flight_start": "2026-06-01",
    "flight_end": "2026-09-30",
    "channels": [
        {"channel": "CTV", "budget_pct": 60, "format_prefs": ["video_30s"]},
        {"channel": "DISPLAY", "budget_pct": 40, "format_prefs": ["300x250", "728x90"]},
    ],
    "target_audience": ["IAB-AUD-001", "IAB-AUD-002"],
}


# ---------------------------------------------------------------------------
# Page load tests
# ---------------------------------------------------------------------------


class TestPageLoad:
    """Test that the main page loads correctly."""

    def test_index_returns_200(self, client):
        """Main demo page returns 200."""
        resp = client.get("/")
        assert resp.status_code == 200

    def test_index_contains_title(self, client):
        """Main page contains the demo title."""
        resp = client.get("/")
        html = resp.data.decode("utf-8")
        assert "Campaign Automation" in html

    def test_index_contains_stage_sections(self, client):
        """Main page has all 6 stage sections."""
        resp = client.get("/")
        html = resp.data.decode("utf-8")
        assert "Enter Brief" in html
        assert "Review Plan" in html
        assert "Review Deals" in html
        assert "Review Creative" in html
        assert "Campaign Ready" in html
        assert "Active" in html or "Live Campaign" in html

    def test_index_has_six_progress_steps(self, client):
        """Progress bar has 6 steps."""
        resp = client.get("/")
        html = resp.data.decode("utf-8")
        assert 'data-step="6"' in html


# ---------------------------------------------------------------------------
# API: Sample briefs
# ---------------------------------------------------------------------------


class TestSampleBriefs:
    """Test the sample briefs API endpoint."""

    def test_sample_briefs_returns_list(self, client):
        """GET /api/sample-briefs returns a list of at least 2 briefs."""
        resp = client.get("/api/sample-briefs")
        assert resp.status_code == 200
        data = resp.get_json()
        assert "briefs" in data
        assert len(data["briefs"]) >= 2

    def test_sample_brief_has_required_fields(self, client):
        """Each sample brief has the required campaign brief fields."""
        resp = client.get("/api/sample-briefs")
        data = resp.get_json()
        brief = data["briefs"][0]
        required_fields = [
            "advertiser_id",
            "campaign_name",
            "objective",
            "total_budget",
            "currency",
            "flight_start",
            "flight_end",
            "channels",
            "target_audience",
        ]
        for field in required_fields:
            assert field in brief, f"Missing field: {field}"


# ---------------------------------------------------------------------------
# API: Submit brief (Stage 1)
# ---------------------------------------------------------------------------


class TestSubmitBrief:
    """Test campaign brief submission."""

    def test_submit_brief_creates_campaign(self, client):
        """POST /api/submit-brief creates a campaign and returns its ID."""
        resp = client.post(
            "/api/submit-brief",
            data=json.dumps(SAMPLE_BRIEF),
            content_type="application/json",
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["success"] is True
        assert "campaign_id" in data
        assert data["status"] == "draft"

    def test_submit_invalid_brief_returns_error(self, client):
        """POST /api/submit-brief with invalid data returns an error."""
        resp = client.post(
            "/api/submit-brief",
            data=json.dumps({"advertiser_id": "test"}),
            content_type="application/json",
        )
        assert resp.status_code == 400
        data = resp.get_json()
        assert data["success"] is False
        assert "error" in data


# ---------------------------------------------------------------------------
# API: Get campaign state
# ---------------------------------------------------------------------------


class TestCampaignState:
    """Test the campaign state API."""

    def test_get_campaign_state(self, client):
        """GET /api/campaign/<id> returns campaign data."""
        # First create a campaign
        resp = client.post(
            "/api/submit-brief",
            data=json.dumps(SAMPLE_BRIEF),
            content_type="application/json",
        )
        campaign_id = resp.get_json()["campaign_id"]

        # Get its state
        resp = client.get(f"/api/campaign/{campaign_id}")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["campaign_id"] == campaign_id
        assert data["status"] == "draft"

    def test_get_nonexistent_campaign_returns_404(self, client):
        """GET /api/campaign/<bad-id> returns 404."""
        resp = client.get("/api/campaign/nonexistent-id")
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# API: Approve plan (Stage 2)
# ---------------------------------------------------------------------------


class TestApprovePlan:
    """Test campaign plan approval."""

    def _create_campaign(self, client):
        """Helper to create a campaign and return its ID."""
        resp = client.post(
            "/api/submit-brief",
            data=json.dumps(SAMPLE_BRIEF),
            content_type="application/json",
        )
        return resp.get_json()["campaign_id"]

    def test_approve_plan_transitions_campaign(self, client):
        """POST /api/approve-plan advances the campaign past planning."""
        campaign_id = self._create_campaign(client)
        resp = client.post(
            "/api/approve-plan",
            data=json.dumps({"campaign_id": campaign_id}),
            content_type="application/json",
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["success"] is True
        assert "plan" in data

    def test_approve_plan_returns_channel_breakdown(self, client):
        """Plan includes per-channel breakdown with budget allocation."""
        campaign_id = self._create_campaign(client)
        resp = client.post(
            "/api/approve-plan",
            data=json.dumps({"campaign_id": campaign_id}),
            content_type="application/json",
        )
        data = resp.get_json()
        plan = data["plan"]
        assert "channel_plans" in plan
        assert len(plan["channel_plans"]) == 2  # CTV + DISPLAY


# ---------------------------------------------------------------------------
# API: Approve booking (Stage 3)
# ---------------------------------------------------------------------------


class TestApproveBooking:
    """Test deal booking approval."""

    def _create_and_plan(self, client):
        """Helper: create campaign, approve plan, return campaign_id."""
        resp = client.post(
            "/api/submit-brief",
            data=json.dumps(SAMPLE_BRIEF),
            content_type="application/json",
        )
        campaign_id = resp.get_json()["campaign_id"]
        client.post(
            "/api/approve-plan",
            data=json.dumps({"campaign_id": campaign_id}),
            content_type="application/json",
        )
        return campaign_id

    def test_approve_booking_transitions_campaign(self, client):
        """POST /api/approve-booking advances past booking to creative."""
        campaign_id = self._create_and_plan(client)
        resp = client.post(
            "/api/approve-booking",
            data=json.dumps({"campaign_id": campaign_id}),
            content_type="application/json",
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["success"] is True

    def test_approve_booking_returns_deals(self, client):
        """Booking result includes deal data per channel."""
        campaign_id = self._create_and_plan(client)
        resp = client.post(
            "/api/approve-booking",
            data=json.dumps({"campaign_id": campaign_id}),
            content_type="application/json",
        )
        data = resp.get_json()
        assert "deals" in data


# ---------------------------------------------------------------------------
# API: Approve creative (Stage 4)
# ---------------------------------------------------------------------------


class TestApproveCreative:
    """Test creative approval."""

    def _advance_to_booking(self, client):
        """Helper: advance campaign to post-booking state."""
        resp = client.post(
            "/api/submit-brief",
            data=json.dumps(SAMPLE_BRIEF),
            content_type="application/json",
        )
        campaign_id = resp.get_json()["campaign_id"]
        client.post(
            "/api/approve-plan",
            data=json.dumps({"campaign_id": campaign_id}),
            content_type="application/json",
        )
        client.post(
            "/api/approve-booking",
            data=json.dumps({"campaign_id": campaign_id}),
            content_type="application/json",
        )
        return campaign_id

    def test_approve_creative_finalizes_campaign(self, client):
        """POST /api/approve-creative transitions campaign to READY."""
        campaign_id = self._advance_to_booking(client)
        resp = client.post(
            "/api/approve-creative",
            data=json.dumps({"campaign_id": campaign_id}),
            content_type="application/json",
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["success"] is True
        assert data["status"] == "ready"

    def test_approve_creative_returns_creative_data(self, client):
        """Creative result includes asset matching data."""
        campaign_id = self._advance_to_booking(client)
        resp = client.post(
            "/api/approve-creative",
            data=json.dumps({"campaign_id": campaign_id}),
            content_type="application/json",
        )
        data = resp.get_json()
        assert "creatives" in data


# ---------------------------------------------------------------------------
# API: Campaign report (Stage 5)
# ---------------------------------------------------------------------------


class TestCampaignReport:
    """Test the full campaign report endpoint."""

    def _advance_to_ready(self, client):
        """Helper: advance campaign through all stages to READY."""
        resp = client.post(
            "/api/submit-brief",
            data=json.dumps(SAMPLE_BRIEF),
            content_type="application/json",
        )
        campaign_id = resp.get_json()["campaign_id"]
        client.post(
            "/api/approve-plan",
            data=json.dumps({"campaign_id": campaign_id}),
            content_type="application/json",
        )
        client.post(
            "/api/approve-booking",
            data=json.dumps({"campaign_id": campaign_id}),
            content_type="application/json",
        )
        client.post(
            "/api/approve-creative",
            data=json.dumps({"campaign_id": campaign_id}),
            content_type="application/json",
        )
        return campaign_id

    def test_campaign_report_returns_full_report(self, client):
        """GET /api/campaign/<id>/report returns the full campaign report."""
        campaign_id = self._advance_to_ready(client)
        resp = client.get(f"/api/campaign/{campaign_id}/report")
        assert resp.status_code == 200
        data = resp.get_json()
        assert "status_summary" in data
        assert "pacing_dashboard" in data
        assert "creative_performance" in data
        assert "deal_report" in data

    def test_campaign_report_status_is_ready(self, client):
        """Report status summary shows READY status."""
        campaign_id = self._advance_to_ready(client)
        resp = client.get(f"/api/campaign/{campaign_id}/report")
        data = resp.get_json()
        assert data["status_summary"]["status"] == "ready"


# ---------------------------------------------------------------------------
# API: Event log
# ---------------------------------------------------------------------------


class TestEventLog:
    """Test the event log endpoint."""

    def test_events_returns_list(self, client):
        """GET /api/events returns events list."""
        resp = client.get("/api/events")
        assert resp.status_code == 200
        data = resp.get_json()
        assert "events" in data
        assert isinstance(data["events"], list)

    def test_events_populated_after_brief(self, client):
        """Events are emitted after brief submission."""
        client.post(
            "/api/submit-brief",
            data=json.dumps(SAMPLE_BRIEF),
            content_type="application/json",
        )
        resp = client.get("/api/events")
        data = resp.get_json()
        assert len(data["events"]) > 0


# ---------------------------------------------------------------------------
# Full pipeline walkthrough
# ---------------------------------------------------------------------------


class TestFullPipeline:
    """Test the complete 6-stage pipeline walkthrough."""

    def test_full_walkthrough(self, client):
        """Complete pipeline from brief to READY."""
        # Stage 1: Submit brief
        resp = client.post(
            "/api/submit-brief",
            data=json.dumps(SAMPLE_BRIEF),
            content_type="application/json",
        )
        assert resp.status_code == 200
        campaign_id = resp.get_json()["campaign_id"]

        # Stage 2: Approve plan
        resp = client.post(
            "/api/approve-plan",
            data=json.dumps({"campaign_id": campaign_id}),
            content_type="application/json",
        )
        assert resp.status_code == 200
        assert resp.get_json()["success"] is True

        # Stage 3: Approve booking
        resp = client.post(
            "/api/approve-booking",
            data=json.dumps({"campaign_id": campaign_id}),
            content_type="application/json",
        )
        assert resp.status_code == 200
        assert resp.get_json()["success"] is True

        # Stage 4: Approve creative
        resp = client.post(
            "/api/approve-creative",
            data=json.dumps({"campaign_id": campaign_id}),
            content_type="application/json",
        )
        assert resp.status_code == 200
        assert resp.get_json()["status"] == "ready"

        # Stage 5: Get report
        resp = client.get(f"/api/campaign/{campaign_id}/report")
        assert resp.status_code == 200
        report = resp.get_json()
        assert report["status_summary"]["status"] == "ready"
        assert report["status_summary"]["total_budget"] == 500000

        # Verify campaign state
        resp = client.get(f"/api/campaign/{campaign_id}")
        assert resp.status_code == 200
        assert resp.get_json()["status"] == "ready"

    def test_full_walkthrough_through_activation(self, client):
        """Complete pipeline from brief through ACTIVE (Stage 6)."""
        # Stages 1-4
        resp = client.post(
            "/api/submit-brief",
            data=json.dumps(SAMPLE_BRIEF),
            content_type="application/json",
        )
        campaign_id = resp.get_json()["campaign_id"]
        client.post(
            "/api/approve-plan",
            data=json.dumps({"campaign_id": campaign_id}),
            content_type="application/json",
        )
        client.post(
            "/api/approve-booking",
            data=json.dumps({"campaign_id": campaign_id}),
            content_type="application/json",
        )
        client.post(
            "/api/approve-creative",
            data=json.dumps({"campaign_id": campaign_id}),
            content_type="application/json",
        )

        # Stage 6: Activate campaign
        resp = client.post(
            "/api/activate-campaign",
            data=json.dumps({"campaign_id": campaign_id}),
            content_type="application/json",
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["success"] is True
        assert data["status"] == "active"

        # Verify campaign state
        resp = client.get(f"/api/campaign/{campaign_id}")
        assert resp.status_code == 200
        assert resp.get_json()["status"] == "active"


# ---------------------------------------------------------------------------
# API: Activate campaign (Stage 6)
# ---------------------------------------------------------------------------


class TestActivateCampaign:
    """Test campaign activation and pacing data generation."""

    def _advance_to_ready(self, client):
        """Helper: advance campaign through all stages to READY."""
        resp = client.post(
            "/api/submit-brief",
            data=json.dumps(SAMPLE_BRIEF),
            content_type="application/json",
        )
        campaign_id = resp.get_json()["campaign_id"]
        client.post(
            "/api/approve-plan",
            data=json.dumps({"campaign_id": campaign_id}),
            content_type="application/json",
        )
        client.post(
            "/api/approve-booking",
            data=json.dumps({"campaign_id": campaign_id}),
            content_type="application/json",
        )
        client.post(
            "/api/approve-creative",
            data=json.dumps({"campaign_id": campaign_id}),
            content_type="application/json",
        )
        return campaign_id

    def test_activate_campaign_transitions_to_active(self, client):
        """POST /api/activate-campaign transitions campaign from READY to ACTIVE."""
        campaign_id = self._advance_to_ready(client)
        resp = client.post(
            "/api/activate-campaign",
            data=json.dumps({"campaign_id": campaign_id}),
            content_type="application/json",
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["success"] is True
        assert data["status"] == "active"

    def test_activate_campaign_returns_pacing_data(self, client):
        """Activation response includes pacing dashboard data."""
        campaign_id = self._advance_to_ready(client)
        resp = client.post(
            "/api/activate-campaign",
            data=json.dumps({"campaign_id": campaign_id}),
            content_type="application/json",
        )
        data = resp.get_json()
        assert "pacing" in data
        pacing = data["pacing"]
        assert "total_budget" in pacing
        assert "total_spend" in pacing
        assert "expected_spend" in pacing
        assert "pacing_pct" in pacing
        assert "deviation_pct" in pacing
        assert "channel_snapshots" in pacing
        assert "deal_snapshots" in pacing

    def test_activate_campaign_generates_simulated_spend(self, client):
        """Activation generates non-zero simulated spend data."""
        campaign_id = self._advance_to_ready(client)
        resp = client.post(
            "/api/activate-campaign",
            data=json.dumps({"campaign_id": campaign_id}),
            content_type="application/json",
        )
        data = resp.get_json()
        pacing = data["pacing"]
        # Simulated spend should be non-zero
        assert pacing["total_spend"] > 0
        assert pacing["expected_spend"] > 0
        # Channel snapshots should have varying pacing
        for ch in pacing["channel_snapshots"]:
            assert ch["allocated_budget"] > 0

    def test_activate_campaign_has_varied_channel_pacing(self, client):
        """Channels should have different pacing percentages (some over, some under)."""
        campaign_id = self._advance_to_ready(client)
        resp = client.post(
            "/api/activate-campaign",
            data=json.dumps({"campaign_id": campaign_id}),
            content_type="application/json",
        )
        data = resp.get_json()
        pacing_pcts = [ch["pacing_pct"] for ch in data["pacing"]["channel_snapshots"]]
        # Not all channels should have the same pacing
        assert len(set(pacing_pcts)) > 1, "All channels have identical pacing"

    def test_activate_campaign_returns_alerts(self, client):
        """Activation should produce pacing deviation alerts."""
        campaign_id = self._advance_to_ready(client)
        resp = client.post(
            "/api/activate-campaign",
            data=json.dumps({"campaign_id": campaign_id}),
            content_type="application/json",
        )
        data = resp.get_json()
        assert "alerts" in data["pacing"]
        # With varied pacing, there should be at least one alert
        assert len(data["pacing"]["alerts"]) > 0

    def test_activate_campaign_returns_reallocation_proposals(self, client):
        """Activation should propose budget reallocations."""
        campaign_id = self._advance_to_ready(client)
        resp = client.post(
            "/api/activate-campaign",
            data=json.dumps({"campaign_id": campaign_id}),
            content_type="application/json",
        )
        data = resp.get_json()
        assert "recommendations" in data["pacing"]
        # With under/overpacing, should have at least one proposal
        assert len(data["pacing"]["recommendations"]) > 0
        rec = data["pacing"]["recommendations"][0]
        assert "source_channel" in rec
        assert "target_channel" in rec
        assert "amount" in rec
        assert "reason" in rec

    def test_activate_campaign_returns_deal_metrics(self, client):
        """Activation response includes deal-level metrics."""
        campaign_id = self._advance_to_ready(client)
        resp = client.post(
            "/api/activate-campaign",
            data=json.dumps({"campaign_id": campaign_id}),
            content_type="application/json",
        )
        data = resp.get_json()
        deals = data["pacing"]["deal_snapshots"]
        assert len(deals) > 0
        for deal in deals:
            assert "deal_id" in deal
            assert "allocated_budget" in deal
            assert "spend" in deal
            assert "fill_rate" in deal
            assert "win_rate" in deal

    def test_activate_campaign_emits_activated_event(self, client):
        """Activation emits a CAMPAIGN_ACTIVATED event."""
        campaign_id = self._advance_to_ready(client)
        client.post(
            "/api/activate-campaign",
            data=json.dumps({"campaign_id": campaign_id}),
            content_type="application/json",
        )
        resp = client.get(f"/api/events?campaign_id={campaign_id}")
        data = resp.get_json()
        event_types = [e["event_type"] for e in data["events"]]
        assert "campaign.activated" in event_types

    def test_activate_nonexistent_campaign_returns_404(self, client):
        """Activating a non-existent campaign returns 404."""
        resp = client.post(
            "/api/activate-campaign",
            data=json.dumps({"campaign_id": "nonexistent-id"}),
            content_type="application/json",
        )
        assert resp.status_code == 404

    def test_activate_campaign_missing_body_returns_400(self, client):
        """Activation without campaign_id returns 400."""
        resp = client.post(
            "/api/activate-campaign",
            data=json.dumps({}),
            content_type="application/json",
        )
        assert resp.status_code == 400


# ---------------------------------------------------------------------------
# API: Pause / Complete campaign (Stage 6 controls)
# ---------------------------------------------------------------------------


class TestCampaignControls:
    """Test pause and complete controls for active campaigns."""

    def _advance_to_active(self, client):
        """Helper: advance campaign through all stages to ACTIVE."""
        resp = client.post(
            "/api/submit-brief",
            data=json.dumps(SAMPLE_BRIEF),
            content_type="application/json",
        )
        campaign_id = resp.get_json()["campaign_id"]
        client.post(
            "/api/approve-plan",
            data=json.dumps({"campaign_id": campaign_id}),
            content_type="application/json",
        )
        client.post(
            "/api/approve-booking",
            data=json.dumps({"campaign_id": campaign_id}),
            content_type="application/json",
        )
        client.post(
            "/api/approve-creative",
            data=json.dumps({"campaign_id": campaign_id}),
            content_type="application/json",
        )
        client.post(
            "/api/activate-campaign",
            data=json.dumps({"campaign_id": campaign_id}),
            content_type="application/json",
        )
        return campaign_id

    def test_pause_campaign(self, client):
        """POST /api/pause-campaign transitions ACTIVE to PAUSED."""
        campaign_id = self._advance_to_active(client)
        resp = client.post(
            "/api/pause-campaign",
            data=json.dumps({"campaign_id": campaign_id}),
            content_type="application/json",
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["success"] is True
        assert data["status"] == "paused"

        # Verify state
        resp = client.get(f"/api/campaign/{campaign_id}")
        assert resp.get_json()["status"] == "paused"

    def test_complete_campaign(self, client):
        """POST /api/complete-campaign transitions ACTIVE to COMPLETED."""
        campaign_id = self._advance_to_active(client)
        resp = client.post(
            "/api/complete-campaign",
            data=json.dumps({"campaign_id": campaign_id}),
            content_type="application/json",
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["success"] is True
        assert data["status"] == "completed"

        # Verify state
        resp = client.get(f"/api/campaign/{campaign_id}")
        assert resp.get_json()["status"] == "completed"

    def test_pause_nonexistent_campaign_returns_404(self, client):
        """Pausing a non-existent campaign returns 404."""
        resp = client.post(
            "/api/pause-campaign",
            data=json.dumps({"campaign_id": "nonexistent-id"}),
            content_type="application/json",
        )
        assert resp.status_code == 404

    def test_complete_nonexistent_campaign_returns_404(self, client):
        """Completing a non-existent campaign returns 404."""
        resp = client.post(
            "/api/complete-campaign",
            data=json.dumps({"campaign_id": "nonexistent-id"}),
            content_type="application/json",
        )
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# API: Pacing report for active campaign (Stage 6)
# ---------------------------------------------------------------------------


class TestActivePacingReport:
    """Test pacing report endpoint for active campaigns."""

    def _advance_to_active(self, client):
        """Helper: advance campaign through all stages to ACTIVE."""
        resp = client.post(
            "/api/submit-brief",
            data=json.dumps(SAMPLE_BRIEF),
            content_type="application/json",
        )
        campaign_id = resp.get_json()["campaign_id"]
        client.post(
            "/api/approve-plan",
            data=json.dumps({"campaign_id": campaign_id}),
            content_type="application/json",
        )
        client.post(
            "/api/approve-booking",
            data=json.dumps({"campaign_id": campaign_id}),
            content_type="application/json",
        )
        client.post(
            "/api/approve-creative",
            data=json.dumps({"campaign_id": campaign_id}),
            content_type="application/json",
        )
        client.post(
            "/api/activate-campaign",
            data=json.dumps({"campaign_id": campaign_id}),
            content_type="application/json",
        )
        return campaign_id

    def test_pacing_report_returns_active_data(self, client):
        """GET /api/campaign/<id>/report for ACTIVE campaign has pacing data."""
        campaign_id = self._advance_to_active(client)
        resp = client.get(f"/api/campaign/{campaign_id}/report")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["status_summary"]["status"] == "active"
        # Should have non-zero pacing data
        pd = data["pacing_dashboard"]
        assert pd["total_spend"] > 0

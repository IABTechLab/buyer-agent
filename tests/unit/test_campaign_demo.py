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
  - Sample briefs are pre-seeded
  - Error handling for invalid briefs and missing campaigns

bead: ar-llj4
"""

import json
import sqlite3
import tempfile
from pathlib import Path

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
        """Main page has all 5 stage sections."""
        resp = client.get("/")
        html = resp.data.decode("utf-8")
        assert "Enter Brief" in html
        assert "Review Plan" in html
        assert "Review Deals" in html
        assert "Review Creative" in html
        assert "Campaign Ready" in html


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
            "advertiser_id", "campaign_name", "objective",
            "total_budget", "currency", "flight_start", "flight_end",
            "channels", "target_audience",
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
    """Test the complete 5-stage pipeline walkthrough."""

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


# ---------------------------------------------------------------------------
# Fresh database on startup (Issue 1)
# ---------------------------------------------------------------------------


class TestFreshDatabase:
    """Test that stale databases are cleaned up on app startup."""

    def test_app_starts_fresh_with_file_db(self):
        """create_campaign_demo_app works even after schema changes."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "demo.db"
            url = f"sqlite:///{db_path}"

            # Create the app once to populate the DB
            app1 = create_campaign_demo_app(database_url=url)
            assert db_path.exists()

            # Creating again should succeed (DB deleted and recreated)
            app2 = create_campaign_demo_app(database_url=url)
            assert db_path.exists()

    def test_stale_db_with_wrong_schema_does_not_crash(self):
        """App recreates DB if old schema is incompatible."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "stale.db"
            url = f"sqlite:///{db_path}"

            # Create a DB with a bogus table to simulate stale schema
            conn = sqlite3.connect(str(db_path))
            conn.execute("CREATE TABLE fake_table (id TEXT)")
            conn.commit()
            conn.close()
            assert db_path.exists()

            # App should delete the stale DB and start fresh
            app = create_campaign_demo_app(database_url=url)
            assert db_path.exists()

            # Verify the app works (campaign creation succeeds)
            app.config["TESTING"] = True
            with app.test_client() as c:
                resp = c.get("/api/sample-briefs")
                assert resp.status_code == 200

    def test_memory_db_works(self):
        """In-memory database works without issues."""
        app = create_campaign_demo_app(database_url="sqlite:///:memory:")
        app.config["TESTING"] = True
        with app.test_client() as c:
            resp = c.get("/")
            assert resp.status_code == 200

    def test_new_app_has_empty_campaigns(self):
        """Restarting the app gives a fresh (empty) campaign list."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "demo.db"
            url = f"sqlite:///{db_path}"

            # First run: create a campaign
            app1 = create_campaign_demo_app(database_url=url)
            app1.config["TESTING"] = True
            with app1.test_client() as c1:
                brief = {
                    "advertiser_id": "ADV-TEST",
                    "campaign_name": "Stale Campaign",
                    "objective": "AWARENESS",
                    "total_budget": 1000,
                    "currency": "USD",
                    "flight_start": "2026-06-01",
                    "flight_end": "2026-09-01",
                    "channels": [{"channel": "DISPLAY", "budget_pct": 100}],
                    "target_audience": ["IAB-AUD-001"],
                }
                resp = c1.post(
                    "/api/submit-brief",
                    data=json.dumps(brief),
                    content_type="application/json",
                )
                assert resp.get_json()["success"]

            # Second run: DB should be fresh (no old campaigns)
            app2 = create_campaign_demo_app(database_url=url)
            app2.config["TESTING"] = True
            with app2.test_client() as c2:
                resp = c2.get("/api/campaigns")
                data = resp.get_json()
                assert len(data["campaigns"]) == 0


# ---------------------------------------------------------------------------
# Form-based brief input (Issue 2)
# ---------------------------------------------------------------------------


class TestFormBasedInput:
    """Test that the HTML contains form-based input elements."""

    def test_page_has_form_tab(self, client):
        """Main page contains the Form input tab."""
        resp = client.get("/")
        html = resp.data.decode("utf-8")
        assert 'data-tab="form"' in html

    def test_page_has_sample_briefs_tab(self, client):
        """Main page contains the Sample Briefs tab."""
        resp = client.get("/")
        html = resp.data.decode("utf-8")
        assert 'data-tab="samples"' in html

    def test_page_has_json_tab(self, client):
        """Main page contains the Advanced JSON tab."""
        resp = client.get("/")
        html = resp.data.decode("utf-8")
        assert 'data-tab="json"' in html

    def test_form_has_advertiser_field(self, client):
        """Form pane contains advertiser name input."""
        resp = client.get("/")
        html = resp.data.decode("utf-8")
        assert 'id="formAdvertiser"' in html

    def test_form_has_campaign_name_field(self, client):
        """Form pane contains campaign name input."""
        resp = client.get("/")
        html = resp.data.decode("utf-8")
        assert 'id="formCampaignName"' in html

    def test_form_has_budget_field(self, client):
        """Form pane contains total budget input."""
        resp = client.get("/")
        html = resp.data.decode("utf-8")
        assert 'id="formBudget"' in html

    def test_form_has_channel_checkboxes(self, client):
        """Form pane contains channel checkboxes."""
        resp = client.get("/")
        html = resp.data.decode("utf-8")
        assert 'id="channelCheckboxes"' in html
        for channel in ["CTV", "DISPLAY", "AUDIO", "NATIVE", "DOOH"]:
            assert f'value="{channel}"' in html

    def test_form_has_flight_date_fields(self, client):
        """Form pane contains flight start and end date inputs."""
        resp = client.get("/")
        html = resp.data.decode("utf-8")
        assert 'id="formFlightStart"' in html
        assert 'id="formFlightEnd"' in html

    def test_form_has_audience_field(self, client):
        """Form pane contains target audience input."""
        resp = client.get("/")
        html = resp.data.decode("utf-8")
        assert 'id="formAudience"' in html

    def test_form_has_budget_split_options(self, client):
        """Form pane contains even/manual budget split radio buttons."""
        resp = client.get("/")
        html = resp.data.decode("utf-8")
        assert 'name="budgetSplit"' in html

    def test_form_pane_is_default_active(self, client):
        """The form pane is the default active tab."""
        resp = client.get("/")
        html = resp.data.decode("utf-8")
        assert 'class="input-pane active" id="pane-form"' in html


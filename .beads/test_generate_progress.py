#!/usr/bin/env python3
"""Tests for generate_progress.py v3 (epic-driven discovery)."""

import json
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

# Import the module under test
sys.path.insert(0, str(Path(__file__).parent))
import generate_progress as gp


# ---------------------------------------------------------------------------
# Fixtures: synthetic issue data
# ---------------------------------------------------------------------------

def make_issue(id, title, status="open", issue_type="task", priority=2,
               deps=None, closed_at=None):
    """Helper to create a synthetic issue dict."""
    issue = {
        "id": id,
        "title": title,
        "status": status,
        "issue_type": issue_type,
        "priority": priority,
    }
    if deps:
        issue["dependencies"] = [
            {"issue_id": id, "depends_on_id": d, "type": "blocks",
             "created_at": "2026-01-01T00:00:00Z", "created_by": "test"}
            for d in deps
        ]
    if closed_at:
        issue["closed_at"] = closed_at
    return issue


def minimal_phase_issues():
    """A minimal set of issues for testing phase discovery and assignment."""
    return [
        # Phase 1 epic
        make_issue("test-p1", "Phase 1: Seller Interop", issue_type="epic",
                   deps=["test-1a", "test-1b"]),
        make_issue("test-1a", "1A: Auth Client", status="closed",
                   closed_at="2026-01-10"),
        make_issue("test-1b", "1B: Discovery Client", status="closed",
                   closed_at="2026-01-11"),

        # Phase 2 epic with gap beads
        make_issue("test-p2", "Phase 2: Campaign Automation", issue_type="epic",
                   deps=["test-2a", "test-gap1"]),
        make_issue("test-2a", "2A: Orchestration", deps=["test-gap2"]),
        make_issue("test-gap1", "Campaign events"),
        make_issue("test-gap2", "Campaign schema"),  # dep of test-2a only

        # Ungrouped
        make_issue("test-bug", "Bug: something broke"),
    ]


def dealjockey_issues():
    """Issues simulating the DealJockey sub-epic structure (Phase 4)."""
    return [
        # Top epic
        make_issue("dj-top", "Phase 4: DealJockey — Deal Portfolio",
                   issue_type="epic", deps=["dj-4a", "dj-4b"]),
        # Sub-epic 4A
        make_issue("dj-4a", "Phase 4A: MVP DealJockey", issue_type="epic",
                   deps=["dj-leaf1", "dj-leaf2"]),
        # Sub-epic 4B
        make_issue("dj-4b", "Phase 4B: Templates", issue_type="epic",
                   deps=["dj-leaf3", "dj-shared"]),
        # Leaf beads
        make_issue("dj-leaf1", "Extend schema", status="closed",
                   closed_at="2026-03-01"),
        make_issue("dj-leaf2", "CSV import", status="closed",
                   closed_at="2026-03-02"),
        make_issue("dj-leaf3", "Template CRUD"),
        # Shared dep: dep of 4B only but has naming that looks like 4A
        make_issue("dj-shared", "Seller API contract"),
    ]


# ---------------------------------------------------------------------------
# Tests: extract_phase_info
# ---------------------------------------------------------------------------

class TestExtractPhaseInfo:
    def test_simple_phase(self):
        assert gp.extract_phase_info("Phase 1: Seller Interop") == (1, None)

    def test_sub_phase(self):
        assert gp.extract_phase_info("Phase 4A: MVP DealJockey") == (4, "A")

    def test_em_dash(self):
        assert gp.extract_phase_info("Phase 4: DealJockey — Deal Portfolio") == (4, None)

    def test_no_phase(self):
        assert gp.extract_phase_info("Bug: something") == (None, None)

    def test_phase_in_middle(self):
        # Phase regex uses search, not match
        assert gp.extract_phase_info("Epic: Phase 2: Campaign") == (2, None)


# ---------------------------------------------------------------------------
# Tests: discover_epics
# ---------------------------------------------------------------------------

class TestDiscoverEpics:
    def test_finds_phase_epics(self):
        issues = minimal_phase_issues()
        epics = gp.discover_epics(issues)
        assert "test-p1" in epics
        assert "test-p2" in epics
        assert epics["test-p1"]["phase"] == 1
        assert epics["test-p2"]["phase"] == 2

    def test_detects_sub_epics(self):
        issues = dealjockey_issues()
        epics = gp.discover_epics(issues)
        assert epics["dj-4a"]["is_sub_epic"] is True
        assert epics["dj-4b"]["is_sub_epic"] is True
        assert epics["dj-top"]["is_sub_epic"] is False

    def test_ignores_non_epic(self):
        issues = [make_issue("x", "Phase 99: Not an epic", issue_type="task")]
        epics = gp.discover_epics(issues)
        assert len(epics) == 0

    def test_ignores_no_phase_epic(self):
        issues = [make_issue("x", "Epic: Buyer reporting", issue_type="epic")]
        epics = gp.discover_epics(issues)
        assert len(epics) == 0


# ---------------------------------------------------------------------------
# Tests: build_phase_structure
# ---------------------------------------------------------------------------

class TestBuildPhaseStructure:
    def test_flat_phases(self):
        issues = minimal_phase_issues()
        epics = gp.discover_epics(issues)
        phases = gp.build_phase_structure(epics, issues)
        phase_nums = [p["phase"] for p in phases]
        assert 1 in phase_nums
        assert 2 in phase_nums

    def test_sub_phase_structure(self):
        issues = dealjockey_issues()
        epics = gp.discover_epics(issues)
        phases = gp.build_phase_structure(epics, issues)
        assert len(phases) == 1  # Just phase 4
        phase4 = phases[0]
        assert phase4["phase"] == 4
        assert len(phase4["sub_phases"]) == 2
        subs = [s["sub"] for s in phase4["sub_phases"]]
        assert subs == ["A", "B"]

    def test_synthetic_phase_created(self):
        """Phases with numbered-prefix beads but no epic get synthetic phases."""
        issues = [
            make_issue("x", "3A: Some feature"),
            make_issue("y", "3B: Another feature"),
        ]
        epics = gp.discover_epics(issues)
        assert len(epics) == 0  # No epics
        phases = gp.build_phase_structure(epics, issues)
        assert len(phases) == 1
        assert phases[0]["phase"] == 3
        assert phases[0].get("synthetic") is True

    def test_mixed_epic_and_synthetic(self):
        """Epic-based and synthetic phases coexist correctly."""
        issues = [
            make_issue("ep", "Phase 2: Campaign", issue_type="epic",
                       deps=["task1"]),
            make_issue("task1", "2A: Orchestration"),
            make_issue("task2", "1A: Auth Client"),  # No Phase 1 epic
        ]
        epics = gp.discover_epics(issues)
        phases = gp.build_phase_structure(epics, issues)
        phase_nums = [p["phase"] for p in phases]
        assert phase_nums == [1, 2]
        # Phase 1 should be synthetic, Phase 2 should be epic-based
        p1 = [p for p in phases if p["phase"] == 1][0]
        p2 = [p for p in phases if p["phase"] == 2][0]
        assert p1.get("synthetic") is True
        assert p2.get("synthetic", False) is False


# ---------------------------------------------------------------------------
# Tests: assign_beads — no collisions
# ---------------------------------------------------------------------------

class TestAssignBeads:
    def test_numbered_prefix_assignment(self):
        issues = minimal_phase_issues()
        epics = gp.discover_epics(issues)
        phases = gp.build_phase_structure(epics, issues)
        claimed = gp.assign_beads(issues, phases, epics)
        assert claimed["test-1a"] == ("phase", 1, None)
        assert claimed["test-1b"] == ("phase", 1, None)

    def test_epic_dep_assignment(self):
        issues = minimal_phase_issues()
        epics = gp.discover_epics(issues)
        phases = gp.build_phase_structure(epics, issues)
        claimed = gp.assign_beads(issues, phases, epics)
        assert claimed["test-gap1"] == ("phase", 2, None)

    def test_reverse_dep_propagation(self):
        """Gap bead whose only dependents are in phase 2 gets assigned there."""
        issues = minimal_phase_issues()
        epics = gp.discover_epics(issues)
        phases = gp.build_phase_structure(epics, issues)
        claimed = gp.assign_beads(issues, phases, epics)
        # test-gap2 is a dep of test-2a (which is in phase 2)
        assert claimed["test-gap2"] == ("phase", 2, None)

    def test_unclaimed_goes_to_other(self):
        issues = minimal_phase_issues()
        epics = gp.discover_epics(issues)
        phases = gp.build_phase_structure(epics, issues)
        claimed = gp.assign_beads(issues, phases, epics)
        assert claimed["test-bug"] == ("other",)

    def test_no_duplicate_assignments(self):
        """Each bead must appear exactly once."""
        issues = minimal_phase_issues() + dealjockey_issues()
        epics = gp.discover_epics(issues)
        phases = gp.build_phase_structure(epics, issues)
        claimed = gp.assign_beads(issues, phases, epics)

        # Verify no bead is assigned to multiple sections
        # (dict keys are unique, so this is inherently true, but verify
        # every non-epic bead is present)
        non_epic_ids = {i["id"] for i in issues if i["id"] not in epics}
        assert set(claimed.keys()) == non_epic_ids

    def test_first_match_wins_for_shared_deps(self):
        """When a bead is a dep of multiple sub-epics, first in phase order wins."""
        issues = [
            make_issue("top", "Phase 4: DealJockey", issue_type="epic",
                       deps=["sub-a", "sub-b"]),
            make_issue("sub-a", "Phase 4A: MVP", issue_type="epic",
                       deps=["shared-bead", "only-a"]),
            make_issue("sub-b", "Phase 4B: Templates", issue_type="epic",
                       deps=["shared-bead", "only-b"]),
            make_issue("shared-bead", "Shared work"),
            make_issue("only-a", "Only in A"),
            make_issue("only-b", "Only in B"),
        ]
        epics = gp.discover_epics(issues)
        phases = gp.build_phase_structure(epics, issues)
        claimed = gp.assign_beads(issues, phases, epics)
        # shared-bead should go to 4A (first match)
        assert claimed["shared-bead"] == ("phase", 4, "A")
        assert claimed["only-a"] == ("phase", 4, "A")
        assert claimed["only-b"] == ("phase", 4, "B")

    def test_numbered_prefix_takes_priority(self):
        """Numbered prefix beads win over epic dep assignment."""
        issues = [
            make_issue("ep2", "Phase 2: Campaign", issue_type="epic",
                       deps=["bead-2a"]),
            make_issue("bead-2a", "2A: Orchestration"),
        ]
        epics = gp.discover_epics(issues)
        phases = gp.build_phase_structure(epics, issues)
        claimed = gp.assign_beads(issues, phases, epics)
        # Should be assigned by numbered prefix, not dep
        assert claimed["bead-2a"] == ("phase", 2, None)


# ---------------------------------------------------------------------------
# Tests: sub-epic rendering
# ---------------------------------------------------------------------------

class TestSubEpicRendering:
    def test_sub_phases_render_as_h3(self):
        """Phases with sub-epics should produce ### headers, not ## headers."""
        issues = dealjockey_issues()

        with tempfile.TemporaryDirectory() as tmpdir:
            jsonl_path = Path(tmpdir) / "issues.jsonl"
            output_path = Path(tmpdir) / "PROGRESS.md"

            with open(jsonl_path, "w") as f:
                for issue in issues:
                    f.write(json.dumps(issue) + "\n")

            # Patch paths and refresh
            with patch.object(gp, "JSONL_PATH", jsonl_path), \
                 patch.object(gp, "OUTPUT_PATH", output_path), \
                 patch.object(gp, "refresh_jsonl", lambda: None):
                gp.generate()

            content = output_path.read_text()
            assert "## Phase 4" in content
            assert "### Phase 4A" in content
            assert "### Phase 4B" in content

    def test_flat_phase_no_h3(self):
        """Flat phases (no sub-epics) should use ## headers only."""
        issues = minimal_phase_issues()

        with tempfile.TemporaryDirectory() as tmpdir:
            jsonl_path = Path(tmpdir) / "issues.jsonl"
            output_path = Path(tmpdir) / "PROGRESS.md"

            with open(jsonl_path, "w") as f:
                for issue in issues:
                    f.write(json.dumps(issue) + "\n")

            with patch.object(gp, "JSONL_PATH", jsonl_path), \
                 patch.object(gp, "OUTPUT_PATH", output_path), \
                 patch.object(gp, "refresh_jsonl", lambda: None):
                gp.generate()

            content = output_path.read_text()
            assert "## Phase 1" in content
            assert "## Phase 2" in content
            assert "###" not in content  # No sub-phase headers


# ---------------------------------------------------------------------------
# Tests: status icons
# ---------------------------------------------------------------------------

class TestStatusIcons:
    def test_closed(self):
        issue = make_issue("x", "done", status="closed")
        assert gp.status_icon(issue, {"x"}, {"x"}) == "\\[x]"

    def test_open(self):
        issue = make_issue("x", "todo", status="open")
        assert gp.status_icon(issue, set(), {"x"}) == "\\[ ]"

    def test_in_progress(self):
        issue = make_issue("x", "wip", status="in_progress")
        assert gp.status_icon(issue, set(), {"x"}) == "\\[~]"

    def test_blocked(self):
        issue = make_issue("x", "stuck", status="open", deps=["y"])
        assert gp.status_icon(issue, set(), {"x", "y"}) == "\\[!]"

    def test_blocked_resolved(self):
        issue = make_issue("x", "ready", status="open", deps=["y"])
        assert gp.status_icon(issue, {"y"}, {"x", "y"}) == "\\[ ]"


# ---------------------------------------------------------------------------
# Tests: load_issues filtering
# ---------------------------------------------------------------------------

class TestLoadIssues:
    def test_filters_tombstones(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            jsonl_path = Path(tmpdir) / "issues.jsonl"
            with open(jsonl_path, "w") as f:
                f.write(json.dumps(make_issue("live", "Active bead")) + "\n")
                f.write(json.dumps(make_issue("dead", "Tombstoned",
                                              status="tombstone")) + "\n")

            with patch.object(gp, "JSONL_PATH", jsonl_path):
                issues = gp.load_issues()
            assert len(issues) == 1
            assert issues[0]["id"] == "live"

    def test_filters_legacy(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            jsonl_path = Path(tmpdir) / "issues.jsonl"
            with open(jsonl_path, "w") as f:
                f.write(json.dumps(make_issue("new", "Active bead")) + "\n")
                f.write(json.dumps(make_issue("old", "[LEGACY] Old bead")) + "\n")

            with patch.object(gp, "JSONL_PATH", jsonl_path):
                issues = gp.load_issues()
            assert len(issues) == 1
            assert issues[0]["id"] == "new"


# ---------------------------------------------------------------------------
# Tests: refresh_jsonl
# ---------------------------------------------------------------------------

class TestRefreshJsonl:
    def test_skips_in_ci(self):
        """refresh_jsonl should be a no-op when GITHUB_ACTIONS is set."""
        with patch.dict(os.environ, {"GITHUB_ACTIONS": "true"}):
            with patch("subprocess.run") as mock_run:
                gp.refresh_jsonl()
                mock_run.assert_not_called()

    def test_skips_without_bd(self):
        """refresh_jsonl should be a no-op when bd is not installed."""
        with patch.dict(os.environ, {}, clear=True):
            # Remove GITHUB_ACTIONS if set
            os.environ.pop("GITHUB_ACTIONS", None)
            with patch("shutil.which", return_value=None):
                with patch("subprocess.run") as mock_run:
                    gp.refresh_jsonl()
                    mock_run.assert_not_called()


# ---------------------------------------------------------------------------
# Tests: end-to-end with real data
# ---------------------------------------------------------------------------

class TestEndToEnd:
    def test_full_generate_with_real_data(self):
        """Run generate against the actual issues.jsonl and verify key properties."""
        real_jsonl = Path(__file__).parent / "issues.jsonl"
        if not real_jsonl.exists():
            pytest.skip("No issues.jsonl available")

        with tempfile.TemporaryDirectory() as tmpdir:
            output_path = Path(tmpdir) / "PROGRESS.md"

            with patch.object(gp, "JSONL_PATH", real_jsonl), \
                 patch.object(gp, "OUTPUT_PATH", output_path), \
                 patch.object(gp, "refresh_jsonl", lambda: None):
                gp.generate()

            content = output_path.read_text()

            # Phase 1 exists
            assert "## Phase 1" in content
            # Phase 2 exists with description
            assert "## Phase 2 — Campaign Automation" in content
            # Phase 3 exists
            assert "## Phase 3" in content
            # Phase 4 with sub-phases
            assert "## Phase 4 — DealJockey" in content
            assert "### Phase 4A — MVP DealJockey" in content
            assert "### Phase 4B — Templates & Seller Integration" in content

            # All Campaign Automation gap beads are present
            for gap_id in ["buyer-ppi", "buyer-80k", "buyer-lae", "buyer-lna",
                           "buyer-uoz", "buyer-78z", "buyer-80o", "buyer-89g",
                           "buyer-0u9", "buyer-gb2", "buyer-f58"]:
                assert gap_id in content, f"Missing gap bead: {gap_id}"

            # No bead appears twice (check the ID column specifically)
            # Table format: | icon | ID | Task | ... — ID is in column index 2
            import re
            bead_ids = []
            for line in content.split("\n"):
                if not line.startswith("|") or "buyer-" not in line:
                    continue
                cols = [c.strip() for c in line.split("|")]
                if len(cols) >= 3 and cols[2].startswith("buyer-"):
                    bead_ids.append(cols[2])
            assert len(bead_ids) == len(set(bead_ids)), \
                f"Duplicate beads: {[b for b in bead_ids if bead_ids.count(b) > 1]}"

            # Other section exists
            assert "## Other" in content

    def test_no_bead_in_multiple_sections(self):
        """Verify the three-step assignment produces no collisions on real data."""
        real_jsonl = Path(__file__).parent / "issues.jsonl"
        if not real_jsonl.exists():
            pytest.skip("No issues.jsonl available")

        with patch.object(gp, "JSONL_PATH", real_jsonl), \
             patch.object(gp, "refresh_jsonl", lambda: None):
            issues = gp.load_issues()
            epics = gp.discover_epics(issues)
            phases = gp.build_phase_structure(epics, issues)
            claimed = gp.assign_beads(issues, phases, epics)

            # Every non-epic bead should be claimed exactly once
            epic_ids = set(epics.keys())
            non_epic_ids = {i["id"] for i in issues if i["id"] not in epic_ids}
            assert set(claimed.keys()) == non_epic_ids


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

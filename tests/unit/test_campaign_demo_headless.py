"""ar-jzek: campaign demo headless / JSON mode tests."""

import json
import os
import subprocess
import sys
import tempfile

os.environ.setdefault("ANTHROPIC_API_KEY", "test-key-for-unit-tests")


def _run_module(args: list[str]) -> subprocess.CompletedProcess:
    """Run `python -m ad_buyer.demo.campaign_demo <args>` with a fresh tmp DB."""

    tmp_dir = tempfile.mkdtemp(prefix="ar-jzek-")
    db_path = f"sqlite:///{tmp_dir}/demo.db"
    env = {**os.environ, "CAMPAIGN_DEMO_DB": db_path, "PYTHONPATH": "src"}
    return subprocess.run(
        [sys.executable, "-m", "ad_buyer.demo.campaign_demo", *args],
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )


class TestHeadlessMode:
    def test_headless_summary_emits_six_stages(self):
        result = _run_module(["--headless", "--summary"])
        assert result.returncode == 0, result.stderr
        # Look at lines on stdout
        lines = [ln for ln in result.stdout.splitlines() if ln.startswith("[")]
        stages = [ln.split("]")[0].lstrip("[") for ln in lines]
        assert stages == [
            "1-brief",
            "2-plan",
            "3-booking",
            "4-creative",
            "5-activate",
            "6-report",
        ], f"Got: {stages}"

    def test_headless_json_mode_each_line_parses(self):
        result = _run_module(["--headless", "--json"])
        assert result.returncode == 0, result.stderr
        json_lines = [ln for ln in result.stdout.splitlines() if ln.startswith("{")]
        assert len(json_lines) == 6, f"Expected 6 JSON stage lines; got {len(json_lines)}"
        for ln in json_lines:
            obj = json.loads(ln)
            assert "stage" in obj
            assert "status" in obj or "http" in obj

    def test_headless_default_output_is_json(self):
        # No --summary and no --json should default to json
        result = _run_module(["--headless"])
        assert result.returncode == 0, result.stderr
        # First non-INFO/log line should parse as JSON
        for ln in result.stdout.splitlines():
            if ln.startswith("{"):
                json.loads(ln)
                break
        else:
            assert False, "No JSON line found in --headless output"

    def test_headless_invalid_sample_index_returns_nonzero(self):
        result = _run_module(["--headless", "--sample-index", "999"])
        assert result.returncode != 0

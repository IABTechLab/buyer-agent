"""Tests for buyer agent AgentCore CLI deployment artifacts and campaign briefs.

Validates:
- http_main.py follows BedrockAgentCoreApp pattern
- requirements.txt contains bedrock-agentcore
- deploy.sh exists and is executable
- Campaign briefs contain required fields

Validates: Requirements 2.1, 2.2, 8.4
"""

import json
import os
from pathlib import Path

import pytest
import yaml

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
AGENTCORE_DIR = REPO_ROOT / "infra" / "aws" / "agentcore"
ENTRYPOINT = REPO_ROOT / "src" / "ad_buyer" / "interfaces" / "agentcore" / "http_main.py"
CFN_DIR = REPO_ROOT / "infra" / "aws" / "cloudformation"
DATA_DIR = REPO_ROOT / "data"


# ===================================================================
# AgentCore Entrypoint Validation
# ===================================================================


class TestAgentCoreEntrypoint:
    """Validate http_main.py follows BedrockAgentCoreApp pattern."""

    def test_entrypoint_exists(self):
        assert ENTRYPOINT.exists()

    @pytest.fixture
    def source(self):
        return ENTRYPOINT.read_text()

    def test_imports_bedrock_agentcore_app(self, source):
        assert "BedrockAgentCoreApp" in source

    def test_creates_app_instance(self, source):
        assert "app = BedrockAgentCoreApp()" in source

    def test_has_entrypoint_decorator(self, source):
        assert "@app.entrypoint" in source

    def test_has_invoke_function(self, source):
        assert "def invoke(payload, context)" in source

    def test_calls_app_run(self, source):
        assert "app.run()" in source

    def test_adds_src_to_sys_path(self, source):
        assert "sys.path" in source


# ===================================================================
# Requirements Validation
# ===================================================================


class TestAgentCoreRequirements:
    """Validate requirements.txt for AgentCore deployment."""

    def test_requirements_file_exists(self):
        assert (AGENTCORE_DIR / "requirements.txt").exists()

    def test_contains_bedrock_agentcore(self):
        content = (AGENTCORE_DIR / "requirements.txt").read_text()
        assert "bedrock-agentcore" in content

    def test_contains_crewai(self):
        content = (AGENTCORE_DIR / "requirements.txt").read_text()
        assert "crewai" in content


# ===================================================================
# Deploy Script Validation
# ===================================================================


class TestDeployScript:
    """Validate deploy.sh for AgentCore CLI deployment."""

    def test_deploy_script_exists(self):
        assert (AGENTCORE_DIR / "deploy.sh").exists()

    def test_deploy_script_is_executable(self):
        assert os.access(AGENTCORE_DIR / "deploy.sh", os.X_OK)

    @pytest.fixture
    def script_content(self):
        return (AGENTCORE_DIR / "deploy.sh").read_text()

    def test_uses_agentcore_configure(self, script_content):
        assert "agentcore configure" in script_content

    def test_uses_agentcore_deploy(self, script_content):
        assert "agentcore deploy" in script_content

    def test_uses_agentcore_invoke(self, script_content):
        assert "agentcore invoke" in script_content

    def test_references_entrypoint(self, script_content):
        assert "agentcore/http_main.py" in script_content

    def test_accepts_seller_url_param(self, script_content):
        assert "--seller-url" in script_content

    def test_help_flag(self):
        result = __import__("subprocess").run(
            ["bash", str(AGENTCORE_DIR / "deploy.sh"), "--help"],
            capture_output=True, text=True, timeout=10,
        )
        assert result.returncode == 0
        assert "Usage:" in result.stdout


# ===================================================================
# main.yaml — ECS Only
# ===================================================================


class TestMainTemplateECSOnly:
    """Validate main.yaml has no AgentCore conditions."""

    @pytest.fixture(autouse=True)
    def load_template(self):
        def _cfn_loader():
            loader = yaml.SafeLoader
            def _multi_constructor(loader, tag_suffix, node):
                if isinstance(node, yaml.ScalarNode):
                    return loader.construct_scalar(node)
                elif isinstance(node, yaml.SequenceNode):
                    return loader.construct_sequence(node)
                elif isinstance(node, yaml.MappingNode):
                    return loader.construct_mapping(node)
            loader.add_multi_constructor("!", _multi_constructor)
            return loader

        with open(CFN_DIR / "main.yaml") as f:
            self.template = yaml.load(f, Loader=_cfn_loader())

    def test_no_deployment_mode_parameter(self):
        params = self.template.get("Parameters", {})
        assert "DeploymentMode" not in params

    def test_no_agentcore_conditions(self):
        conditions = self.template.get("Conditions", {})
        assert "IsAgentCore" not in conditions


# ===================================================================
# Campaign Briefs Validation
# ===================================================================


class TestCampaignBriefs:
    """Validate campaign_briefs.json structure and content.

    Validates: Requirement 8.4 — 3 pre-configured campaign briefs.
    """

    @pytest.fixture(autouse=True)
    def load_briefs(self):
        briefs_path = DATA_DIR / "campaign_briefs.json"
        assert briefs_path.exists()
        with open(briefs_path) as f:
            self.briefs = json.load(f)

    def test_has_three_briefs(self):
        assert len(self.briefs) == 3

    def test_briefs_have_required_fields(self):
        required = ["id", "vertical", "brand", "budget", "channels",
                     "target_audience", "target_cpm", "max_cpm",
                     "preferred_package", "flight_dates"]
        for brief in self.briefs:
            for field in required:
                assert field in brief, f"Brief {brief.get('id', '?')} missing: {field}"

    def test_brief_ids(self):
        ids = [b["id"] for b in self.briefs]
        assert "BRIEF-AUTO-SPORTS" in ids
        assert "BRIEF-RETAIL-NEWS" in ids
        assert "BRIEF-ENT-STREAMING" in ids

    def test_brief_budgets_are_positive(self):
        for brief in self.briefs:
            assert brief["budget"] > 0

    def test_brief_cpm_ranges_valid(self):
        for brief in self.briefs:
            assert brief["target_cpm"] <= brief["max_cpm"]

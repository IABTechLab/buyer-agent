"""Validate Infrastructure-as-Code template structure and consistency.

These tests verify that:
- CloudFormation templates are valid YAML with required sections
- Terraform files have consistent module wiring
- Docker configuration is well-formed
- All templates reference the same port, project name, etc.
"""

from pathlib import Path

import pytest
import yaml

INFRA_ROOT = Path(__file__).resolve().parents[2] / "infra"
CFN_DIR = INFRA_ROOT / "aws" / "cloudformation"
TF_DIR = INFRA_ROOT / "aws" / "terraform"
DOCKER_DIR = INFRA_ROOT / "docker"


# CloudFormation uses custom YAML tags (!Ref, !Sub, !GetAtt, etc.)
# We need a custom loader that treats them as plain strings.
class CfnLoader(yaml.SafeLoader):
    """YAML loader that handles CloudFormation intrinsic function tags."""

    pass


# Register handlers for all common CloudFormation tags
_CFN_TAGS = [
    "!Ref", "!Sub", "!GetAtt", "!Select", "!GetAZs", "!Join",
    "!If", "!Not", "!Equals", "!And", "!Or", "!FindInMap",
    "!Base64", "!Cidr", "!ImportValue", "!Split", "!Transform",
]

for _tag in _CFN_TAGS:
    CfnLoader.add_multi_constructor(
        _tag,
        lambda loader, suffix, node: loader.construct_mapping(node)
        if isinstance(node, yaml.MappingNode)
        else loader.construct_sequence(node)
        if isinstance(node, yaml.SequenceNode)
        else loader.construct_scalar(node),
    )


def load_cfn_yaml(path: Path) -> dict:
    """Load a CloudFormation YAML template, handling intrinsic function tags."""
    with open(path) as f:
        return yaml.load(f, Loader=CfnLoader)


class TestCloudFormationTemplates:
    """Validate CloudFormation YAML templates."""

    @pytest.fixture(params=["main.yaml", "network.yaml", "storage.yaml", "compute.yaml"])
    def cfn_template(self, request):
        path = CFN_DIR / request.param
        assert path.exists(), f"Missing CFn template: {request.param}"
        return request.param, load_cfn_yaml(path)

    def test_has_format_version(self, cfn_template):
        name, template = cfn_template
        assert "AWSTemplateFormatVersion" in template, (
            f"{name}: missing AWSTemplateFormatVersion"
        )

    def test_has_description(self, cfn_template):
        name, template = cfn_template
        assert "Description" in template, f"{name}: missing Description"

    def test_has_resources(self, cfn_template):
        name, template = cfn_template
        assert "Resources" in template, f"{name}: missing Resources section"

    def test_has_outputs(self, cfn_template):
        name, template = cfn_template
        assert "Outputs" in template, f"{name}: missing Outputs section"

    def test_storage_has_redis(self):
        template = load_cfn_yaml(CFN_DIR / "storage.yaml")
        resources = template["Resources"]
        assert "RedisReplicationGroup" in resources, (
            "storage.yaml should define a Redis replication group"
        )
        assert "RedisSubnetGroup" in resources, (
            "storage.yaml should define a Redis subnet group"
        )

    def test_network_has_redis_security_group(self):
        template = load_cfn_yaml(CFN_DIR / "network.yaml")
        resources = template["Resources"]
        assert "RedisSecurityGroup" in resources, (
            "network.yaml should define a Redis security group"
        )

    def test_main_wires_storage_stack(self):
        template = load_cfn_yaml(CFN_DIR / "main.yaml")
        resources = template["Resources"]
        assert "StorageStack" in resources, (
            "main.yaml should include a StorageStack"
        )
        assert "NetworkStack" in resources
        assert "ComputeStack" in resources

    def test_compute_accepts_redis_params(self):
        template = load_cfn_yaml(CFN_DIR / "compute.yaml")
        params = template["Parameters"]
        assert "RedisEndpoint" in params, (
            "compute.yaml should accept RedisEndpoint parameter"
        )
        assert "RedisPort" in params, (
            "compute.yaml should accept RedisPort parameter"
        )


class TestTerraformModules:
    """Validate Terraform module structure."""

    def test_root_main_exists(self):
        assert (TF_DIR / "main.tf").exists()

    def test_root_variables_exists(self):
        assert (TF_DIR / "variables.tf").exists()

    def test_root_outputs_exists(self):
        assert (TF_DIR / "outputs.tf").exists()

    def test_tfvars_example_exists(self):
        assert (TF_DIR / "terraform.tfvars.example").exists()

    @pytest.mark.parametrize("module_name", ["network", "compute", "storage"])
    def test_module_has_required_files(self, module_name):
        module_dir = TF_DIR / "modules" / module_name
        assert module_dir.is_dir(), f"Module directory missing: {module_name}"
        assert (module_dir / "main.tf").exists(), f"{module_name}/main.tf missing"
        assert (module_dir / "variables.tf").exists(), f"{module_name}/variables.tf missing"
        assert (module_dir / "outputs.tf").exists(), f"{module_name}/outputs.tf missing"

    def test_root_main_references_storage_module(self):
        content = (TF_DIR / "main.tf").read_text()
        assert 'module "storage"' in content, (
            "Root main.tf should reference the storage module"
        )
        assert "./modules/storage" in content

    def test_root_outputs_include_redis(self):
        content = (TF_DIR / "outputs.tf").read_text()
        assert "redis_endpoint" in content
        assert "redis_port" in content

    def test_root_variables_include_redis_node_type(self):
        content = (TF_DIR / "variables.tf").read_text()
        assert "redis_node_type" in content


class TestDockerConfiguration:
    """Validate Docker files."""

    def test_dockerfile_exists(self):
        assert (DOCKER_DIR / "Dockerfile").exists()

    def test_dockerignore_exists(self):
        assert (DOCKER_DIR / ".dockerignore").exists()

    def test_docker_compose_exists(self):
        assert (DOCKER_DIR / "docker-compose.yml").exists()

    def test_docker_compose_has_redis_service(self):
        with open(DOCKER_DIR / "docker-compose.yml") as f:
            compose = yaml.safe_load(f)
        services = compose.get("services", {})
        assert "redis" in services, "docker-compose should include a redis service"
        assert "app" in services, "docker-compose should include an app service"

    def test_docker_compose_app_depends_on_redis(self):
        with open(DOCKER_DIR / "docker-compose.yml") as f:
            compose = yaml.safe_load(f)
        app = compose["services"]["app"]
        depends = app.get("depends_on", {})
        assert "redis" in depends, "app service should depend on redis"

    def test_docker_compose_sets_redis_url(self):
        with open(DOCKER_DIR / "docker-compose.yml") as f:
            compose = yaml.safe_load(f)
        app_env = compose["services"]["app"].get("environment", {})
        assert "REDIS_URL" in app_env, "app should have REDIS_URL environment variable"
        assert "redis://" in app_env["REDIS_URL"]


class TestGitHubActionsWorkflows:
    """Validate GitHub Actions workflows."""

    WORKFLOWS_DIR = Path(__file__).resolve().parents[2] / ".github" / "workflows"

    def test_ci_workflow_exists(self):
        assert (self.WORKFLOWS_DIR / "ci.yml").exists()

    def test_deploy_workflow_exists(self):
        assert (self.WORKFLOWS_DIR / "deploy.yml").exists()

    def test_ci_workflow_has_jobs(self):
        content = (self.WORKFLOWS_DIR / "ci.yml").read_text()
        assert "jobs:" in content
        assert "lint" in content or "test" in content

    def test_deploy_workflow_has_jobs(self):
        content = (self.WORKFLOWS_DIR / "deploy.yml").read_text()
        assert "jobs:" in content
        assert "deploy" in content


class TestInfraReadme:
    """Validate infra README exists."""

    def test_readme_exists(self):
        assert (INFRA_ROOT / "README.md").exists()

    def test_readme_has_content(self):
        content = (INFRA_ROOT / "README.md").read_text()
        assert len(content) > 500, "README should have substantial content"
        assert "Terraform" in content
        assert "CloudFormation" in content
        assert "Docker" in content
        assert "Redis" in content

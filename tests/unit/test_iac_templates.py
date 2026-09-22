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
    "!Ref",
    "!Sub",
    "!GetAtt",
    "!Select",
    "!GetAZs",
    "!Join",
    "!If",
    "!Not",
    "!Equals",
    "!And",
    "!Or",
    "!FindInMap",
    "!Base64",
    "!Cidr",
    "!ImportValue",
    "!Split",
    "!Transform",
]

for _tag in _CFN_TAGS:
    CfnLoader.add_multi_constructor(
        _tag,
        lambda loader, suffix, node: (
            loader.construct_mapping(node)
            if isinstance(node, yaml.MappingNode)
            else loader.construct_sequence(node)
            if isinstance(node, yaml.SequenceNode)
            else loader.construct_scalar(node)
        ),
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
        assert "AWSTemplateFormatVersion" in template, f"{name}: missing AWSTemplateFormatVersion"

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
        assert "RedisSubnetGroup" in resources, "storage.yaml should define a Redis subnet group"

    def test_network_has_redis_security_group(self):
        template = load_cfn_yaml(CFN_DIR / "network.yaml")
        resources = template["Resources"]
        assert "RedisSecurityGroup" in resources, (
            "network.yaml should define a Redis security group"
        )

    def test_main_wires_storage_stack(self):
        template = load_cfn_yaml(CFN_DIR / "main.yaml")
        resources = template["Resources"]
        assert "StorageStack" in resources, "main.yaml should include a StorageStack"
        assert "NetworkStack" in resources
        assert "ComputeStack" in resources

    def test_compute_accepts_redis_params(self):
        template = load_cfn_yaml(CFN_DIR / "compute.yaml")
        params = template["Parameters"]
        assert "RedisEndpoint" in params, "compute.yaml should accept RedisEndpoint parameter"
        assert "RedisPort" in params, "compute.yaml should accept RedisPort parameter"


class TestBuyerDurablePostgres:
    """Group 10 / Req 12 — buyer Aurora Serverless v2 + Secrets-Manager + VPC path."""

    AGENTCORE_DIR = INFRA_ROOT / "aws" / "agentcore"

    # -- storage.yaml: Aurora Serverless v2 + RDS-managed secret + scale-to-zero --
    def test_storage_has_aurora_cluster(self):
        t = load_cfn_yaml(CFN_DIR / "storage.yaml")
        r = t["Resources"]
        assert "AuroraCluster" in r and r["AuroraCluster"]["Type"] == "AWS::RDS::DBCluster"
        assert "AuroraInstance1" in r and r["AuroraInstance1"]["Type"] == "AWS::RDS::DBInstance"
        assert r["AuroraCluster"]["Properties"]["Engine"] == "aurora-postgresql"
        # Redis must remain (additive change)
        assert "RedisReplicationGroup" in r

    def test_aurora_uses_rds_managed_secret_not_plaintext(self):
        props = load_cfn_yaml(CFN_DIR / "storage.yaml")["Resources"]["AuroraCluster"]["Properties"]
        assert props.get("ManageMasterUserPassword") is True, (
            "Aurora must use RDS-managed Secrets Manager password (Req 12.2)"
        )
        # No plaintext MasterUserPassword field
        assert "MasterUserPassword" not in props, "must not set a plaintext MasterUserPassword"

    def test_aurora_serverless_v2_scale_to_zero_default(self):
        params = load_cfn_yaml(CFN_DIR / "storage.yaml")["Parameters"]
        assert params["MinACU"]["Default"] == 0, "MinACU default must be 0 (scale-to-zero)"
        inst = load_cfn_yaml(CFN_DIR / "storage.yaml")["Resources"]["AuroraInstance1"]["Properties"]
        assert inst["DBInstanceClass"] == "db.serverless"

    def test_aurora_engine_supports_min_zero(self):
        ver = load_cfn_yaml(CFN_DIR / "storage.yaml")["Resources"]["AuroraCluster"]["Properties"][
            "EngineVersion"
        ]
        major, minor = (int(x) for x in str(ver).split(".")[:2])
        # MinCapacity=0 needs Aurora PG >= 16.3 (or >=15.7 etc.)
        assert (major, minor) >= (16, 3), f"engine {ver} does not support MinCapacity=0"

    def test_storage_exports_secret_and_connection(self):
        o = load_cfn_yaml(CFN_DIR / "storage.yaml")["Outputs"]
        for k in ("AuroraSecretArn", "AuroraEndpoint", "AuroraPort", "AuroraDatabaseName"):
            assert k in o, f"storage.yaml must export {k}"

    # -- network-agentcore.yaml: endpoints + 443 self-ingress --
    def test_agentcore_network_endpoints_and_self_ingress(self):
        r = load_cfn_yaml(self.AGENTCORE_DIR / "network-agentcore.yaml")["Resources"]
        wanted = {
            "BedrockAgentCoreEndpoint": "bedrock-agentcore",
            "BedrockRuntimeEndpoint": "bedrock-runtime",
            "StsEndpoint": "sts",
            "SecretsManagerEndpoint": "secretsmanager",
        }
        for lid, svc in wanted.items():
            assert lid in r and r[lid]["Type"] == "AWS::EC2::VPCEndpoint"
            assert svc in str(r[lid]["Properties"]["ServiceName"])
        # the dark-container fix
        assert "AgentCoreHttpsSelfIngress" in r, "443 self-ingress rule required"

    # -- main-agentcore.yaml: orchestrator creates the Aurora SG in-stack --
    def test_main_agentcore_creates_aurora_sg_option1(self):
        r = load_cfn_yaml(self.AGENTCORE_DIR / "main-agentcore.yaml")["Resources"]
        assert "DatabaseSecurityGroup" in r, (
            "main-agentcore.yaml must create the Aurora SG in-stack (Option 1)"
        )
        for stack in ("NetworkStack", "StorageStack", "AgentCoreNetworkStack"):
            assert stack in r


class TestDeployPostgresPath:
    """deploy.sh --storage postgres branch + secret grant wiring (Req 12.4)."""

    DEPLOY = INFRA_ROOT / "aws" / "agentcore" / "deploy.sh"

    @pytest.fixture
    def script(self):
        return self.DEPLOY.read_text()

    def test_has_storage_arg(self, script):
        assert "--storage)" in script and 'STORAGE_TYPE_ARG="sqlite"' in script

    def test_postgres_deploys_infra(self, script):
        assert "deploy_postgres_infrastructure" in script
        assert "main-agentcore.yaml" in script

    def test_postgres_uses_vpc_mode(self, script):
        assert "--vpc" in script and "--security-groups" in script

    def test_postgres_forwards_non_secret_env_only(self, script):
        assert "STORAGE_TYPE=hybrid" in script
        assert "DB_SECRET_ARN=" in script
        # the password must NEVER be interpolated into env
        assert "DB_PASSWORD" not in script

    def test_grants_secret_read_to_runtime_role(self, script):
        assert "grant_secret_access" in script
        assert "secretsmanager:GetSecretValue" in script

    def test_default_stays_sqlite(self, script):
        assert "STORAGE_TYPE=sqlite" in script


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
        assert 'module "storage"' in content, "Root main.tf should reference the storage module"
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

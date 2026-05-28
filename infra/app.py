"""bulwark-cloud CDK application entry point."""
import os

import aws_cdk as cdk
from bulwark_cloud_infra.api_stack import ApiStack
from bulwark_cloud_infra.compute_stack import ComputeStack
from bulwark_cloud_infra.frontend_stack import FrontendStack
from bulwark_cloud_infra.network_stack import NetworkStack
from bulwark_cloud_infra.observability_stack import ObservabilityStack
from bulwark_cloud_infra.storage_stack import StorageStack

app = cdk.App()

account = os.environ.get("CDK_DEFAULT_ACCOUNT", os.environ.get("AWS_ACCOUNT_ID", "000000000000"))
region = os.environ.get("CDK_DEFAULT_REGION", "eu-north-1")
env = cdk.Environment(account=account, region=region)

# ── Stack dependency order ──────────────────────────────────────────────────
#
#   NetworkStack  ─┐
#   StorageStack  ─┴─► ComputeStack (ECS + SFN merged) ─► ApiStack
#                                                         ObservabilityStack
#
# ComputeStack now owns both ECS/Fargate compute and the Step Functions state
# machine (previously OrchestrationStack). Merging eliminated a cross-stack
# CloudFormation export of the ECS task definition ARN, which changed on
# every image deploy and caused CF "export in use" failures.

network = NetworkStack(app, "BulwarkCloudNetwork", env=env)
storage = StorageStack(app, "BulwarkCloudStorage", env=env)

compute = ComputeStack(
    app,
    "BulwarkCloudCompute",
    env=env,
    vpc=network.vpc,
    bucket=storage.bucket,
    table=storage.table,
    secrets=storage.secrets,
    events_topic=storage.events_topic,
)

ApiStack(
    app,
    "BulwarkCloudApi",
    env=env,
    state_machine=compute.state_machine,
    table=storage.table,
    bucket=storage.bucket,
)

ObservabilityStack(
    app,
    "BulwarkCloudObservability",
    env=env,
    events_topic=storage.events_topic,
)

FrontendStack(app, "BulwarkCloudFrontend", env=env)

app.synth()

"""bulwark-cloud CDK application entry point."""
import os

import aws_cdk as cdk

from bulwark_cloud_infra.api_stack import ApiStack
from bulwark_cloud_infra.compute_stack import ComputeStack
from bulwark_cloud_infra.network_stack import NetworkStack
from bulwark_cloud_infra.observability_stack import ObservabilityStack
from bulwark_cloud_infra.orchestration_stack import OrchestrationStack
from bulwark_cloud_infra.storage_stack import StorageStack

app = cdk.App()

account = os.environ.get("CDK_DEFAULT_ACCOUNT", os.environ.get("AWS_ACCOUNT_ID", ""))
region = os.environ.get("CDK_DEFAULT_REGION", "eu-central-1")
env = cdk.Environment(account=account, region=region)

# ── Stack dependency order ──────────────────────────────────────────────────
#
#   NetworkStack  ─┐
#   StorageStack  ─┤─► ComputeStack ─► OrchestrationStack ─► ApiStack
#                  └──────────────────────────────────────────────────►┘
#                                                          ObservabilityStack
#
# ApiStack is the only stack that references both OrchestrationStack (state
# machine ARN) and ComputeStack (common Lambda layer). Keeping the API Lambda
# creation there avoids a CDK cross-stack dependency cycle.

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
)

orch = OrchestrationStack(
    app,
    "BulwarkCloudOrchestration",
    env=env,
    cluster=compute.cluster,
    task_definition=compute.task_definition,
    private_subnets=network.private_subnets,
    task_sg=compute.task_sg,
    submit_lambda=compute.submit_lambda,
    index_lambda=compute.index_findings_lambda,
    mark_failed_lambda=compute.mark_failed_lambda,
    events_topic=storage.events_topic,
)

ApiStack(
    app,
    "BulwarkCloudApi",
    env=env,
    state_machine=orch.state_machine,
    table=storage.table,
    bucket=storage.bucket,
    common_layer=compute.common_layer,
)

ObservabilityStack(
    app,
    "BulwarkCloudObservability",
    env=env,
    events_topic=storage.events_topic,
)

app.synth()

"""ECS Fargate cluster, task definition, and Step Functions Lambdas for bulwark-cloud.

NOTE: The API Lambda lives in ApiStack (not here) to avoid a CDK cross-stack
dependency cycle:
  OrchestrationStack -> ComputeStack (submit Lambda ARN in state machine)
  ComputeStack -> OrchestrationStack (state machine ARN in api Lambda IAM policy)
The API Lambda belongs to ApiStack which is downstream of both.
"""
from __future__ import annotations

import aws_cdk as cdk
from aws_cdk import aws_dynamodb as dynamodb
from aws_cdk import aws_ec2 as ec2
from aws_cdk import aws_ecr as ecr
from aws_cdk import aws_ecs as ecs
from aws_cdk import aws_iam as iam
from aws_cdk import aws_lambda as lambda_
from aws_cdk import aws_logs as logs
from aws_cdk import aws_s3 as s3
from aws_cdk import aws_secretsmanager as secretsmanager
from constructs import Construct

DEFAULT_IMAGE_TAG = "latest"
TASK_CPU = 4096    # 4 vCPU — Pass 2 runs 3 parallel Claude sessions
TASK_MEM = 16384   # 16 GB — Halmos spikes to ~6 GB; Foundry compilation ~4 GB


class ComputeStack(cdk.Stack):
    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        *,
        vpc: ec2.Vpc,
        bucket: s3.Bucket,
        table: dynamodb.Table,
        secrets: dict[str, secretsmanager.Secret],
        **kwargs: object,
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)

        # ── ECR repository ─────────────────────────────────────────────────
        ecr_repo_name = "bulwark-cloud-orchestrator"
        self.repo = ecr.Repository(
            self,
            "WorkerRepo",
            repository_name=ecr_repo_name,
            removal_policy=cdk.RemovalPolicy.RETAIN,
            lifecycle_rules=[
                ecr.LifecycleRule(
                    description="Keep last 10 images",
                    max_image_count=10,
                    tag_status=ecr.TagStatus.ANY,
                )
            ],
        )

        # ── ECS Cluster ────────────────────────────────────────────────────
        self.cluster = ecs.Cluster(
            self,
            "Cluster",
            cluster_name="bulwark-cloud",
            vpc=vpc,
        )

        # ── CloudWatch log group ───────────────────────────────────────────
        worker_log_group = logs.LogGroup(
            self,
            "WorkerLogs",
            log_group_name="/ecs/bulwark-cloud",
            retention=logs.RetentionDays.ONE_MONTH,
            removal_policy=cdk.RemovalPolicy.DESTROY,
        )

        # ── IAM: task execution role (ECS control plane) ───────────────────
        execution_role = iam.Role(
            self,
            "TaskExecutionRole",
            role_name="bulwark-cloud-task-execution",
            assumed_by=iam.ServicePrincipal("ecs-tasks.amazonaws.com"),
            managed_policies=[
                iam.ManagedPolicy.from_aws_managed_policy_name(
                    "service-role/AmazonECSTaskExecutionRolePolicy"
                )
            ],
        )
        execution_role.add_to_policy(
            iam.PolicyStatement(
                sid="ReadAnthropicSecretExec",
                actions=["secretsmanager:GetSecretValue"],
                resources=[secrets["anthropic"].secret_arn],
            )
        )

        # ── IAM: task role (workload inside the container) ─────────────────
        task_role = iam.Role(
            self,
            "TaskRole",
            role_name="bulwark-cloud-task",
            assumed_by=iam.ServicePrincipal("ecs-tasks.amazonaws.com"),
        )
        task_role.add_to_policy(
            iam.PolicyStatement(
                sid="WriteJobArtefacts",
                actions=["s3:PutObject", "s3:GetObject", "s3:DeleteObject", "s3:ListBucket"],
                resources=[bucket.bucket_arn, f"{bucket.bucket_arn}/*"],
            )
        )
        task_role.add_to_policy(
            iam.PolicyStatement(
                sid="WriteJobState",
                actions=[
                    "dynamodb:UpdateItem",
                    "dynamodb:PutItem",
                    "dynamodb:GetItem",
                    "dynamodb:Query",
                ],
                resources=[table.table_arn, f"{table.table_arn}/index/*"],
            )
        )
        task_role.add_to_policy(
            iam.PolicyStatement(
                sid="ReadAnthropicSecretTask",
                actions=["secretsmanager:GetSecretValue"],
                resources=[secrets["anthropic"].secret_arn],
            )
        )
        task_role.add_to_policy(
            iam.PolicyStatement(
                sid="EmitMetrics",
                actions=["cloudwatch:PutMetricData"],
                resources=["*"],
                conditions={"StringEquals": {"cloudwatch:namespace": "BulwarkCloud"}},
            )
        )

        # ── ECS Task Definition ────────────────────────────────────────────
        self.task_definition = ecs.FargateTaskDefinition(
            self,
            "WorkerTaskDef",
            family="bulwark-cloud-worker",
            cpu=TASK_CPU,
            memory_limit_mib=TASK_MEM,
            execution_role=execution_role,
            task_role=task_role,
        )

        image_tag = self.node.try_get_context("orchestratorImageTag") or DEFAULT_IMAGE_TAG

        # add_container() sets this as default_container, referenced by OrchestrationStack
        # via task_definition.default_container for the container env override.
        self.task_definition.add_container(
            "bulwark",
            image=ecs.ContainerImage.from_ecr_repository(self.repo, tag=image_tag),
            logging=ecs.LogDrivers.aws_logs(
                stream_prefix="worker",
                log_group=worker_log_group,
            ),
            # Static env vars baked into the task definition.
            # Per-job vars (JOB_ID, TARGET_REPO, etc.) are injected as Step Functions
            # container overrides so each execution is independently parameterised.
            environment={
                "AWS_REGION": self.region,
                "DYNAMO_TABLE": table.table_name,
                "S3_BUCKET": bucket.bucket_name,
                "SECRET_ARN_ANTHROPIC": secrets["anthropic"].secret_arn,
            },
            stop_timeout=cdk.Duration.seconds(120),
            ulimits=[
                ecs.Ulimit(
                    name=ecs.UlimitName.NOFILE,
                    soft_limit=65536,
                    hard_limit=65536,
                )
            ],
        )

        # ── Security group for Fargate tasks ──────────────────────────────
        self.task_sg = ec2.SecurityGroup(
            self,
            "TaskSg",
            vpc=vpc,
            security_group_name="bulwark-cloud-task-sg",
            description="Fargate audit tasks — outbound HTTPS only",
            allow_all_outbound=False,
        )
        self.task_sg.add_egress_rule(
            ec2.Peer.any_ipv4(),
            ec2.Port.tcp(443),
            "HTTPS outbound for git clone + Anthropic API",
        )

        # ── Lambda shared layer ────────────────────────────────────────────
        # Docker bundling installs the shared package + its deps (pydantic)
        # into the layer's python/ directory so Lambda can import it.
        self.common_layer = lambda_.LayerVersion(
            self,
            "CommonLayer",
            layer_version_name="bulwark-cloud-common",
            code=lambda_.Code.from_asset(
                "../shared",
                bundling=cdk.BundlingOptions(
                    image=lambda_.Runtime.PYTHON_3_12.bundling_image,
                    command=[
                        "bash",
                        "-c",
                        "pip install /asset-input -t /asset-output/python --quiet",
                    ],
                ),
            ),
            compatible_runtimes=[lambda_.Runtime.PYTHON_3_12],
            description="Shared Pydantic models and utilities",
        )

        # Shared env for the three Step Functions Lambdas
        sfn_env = {
            "DYNAMO_TABLE": table.table_name,
            "S3_BUCKET": bucket.bucket_name,
        }

        def _sfn_lambda(lid: str, code_path: str, memory: int, timeout: int) -> lambda_.Function:
            fn_log_group = logs.LogGroup(
                self,
                f"{lid}Logs",
                log_group_name=f"/aws/lambda/bulwark-cloud-{lid.lower()}",
                retention=logs.RetentionDays.ONE_MONTH,
                removal_policy=cdk.RemovalPolicy.DESTROY,
            )
            fn = lambda_.Function(
                self,
                lid,
                function_name=f"bulwark-cloud-{lid.lower().replace('_', '-')}",
                runtime=lambda_.Runtime.PYTHON_3_12,
                handler="handler.handler",
                code=lambda_.Code.from_asset(code_path),
                memory_size=memory,
                timeout=cdk.Duration.seconds(timeout),
                environment=sfn_env,
                # No layer: SFN Lambdas only use boto3 (pre-installed in runtime)
                log_group=fn_log_group,
            )
            # Use add_to_role_policy (not table.grant_*) to avoid CDK generating a
            # DynamoDB resource-based policy in StorageStack that would cause a cycle.
            fn.add_to_role_policy(
                iam.PolicyStatement(
                    sid="DynamoReadWrite",
                    actions=[
                        "dynamodb:BatchGetItem",
                        "dynamodb:GetItem",
                        "dynamodb:Query",
                        "dynamodb:Scan",
                        "dynamodb:BatchWriteItem",
                        "dynamodb:PutItem",
                        "dynamodb:UpdateItem",
                        "dynamodb:DeleteItem",
                        "dynamodb:DescribeTable",
                    ],
                    resources=[table.table_arn, f"{table.table_arn}/index/*"],
                )
            )
            # S3 access granted per-lambda below (avoids cross-stack bucket policy)
            return fn

        self.submit_lambda = _sfn_lambda("Submit", "../lambdas/submit", 512, 60)
        self.index_findings_lambda = _sfn_lambda(
            "IndexFindings", "../lambdas/index_findings", 1024, 300
        )
        # IndexFindings reads the final-report.json from S3
        self.index_findings_lambda.add_to_role_policy(
            iam.PolicyStatement(
                sid="ReadJobReport",
                actions=["s3:GetObject"],
                resources=[f"{bucket.bucket_arn}/*"],
            )
        )
        self.mark_failed_lambda = _sfn_lambda(
            "MarkFailed", "../lambdas/mark_failed", 512, 60
        )

        cdk.CfnOutput(self, "ClusterArn", value=self.cluster.cluster_arn)
        cdk.CfnOutput(
            self, "TaskDefinitionArn", value=self.task_definition.task_definition_arn
        )
        cdk.CfnOutput(self, "EcrRepoUri", value=self.repo.repository_uri)

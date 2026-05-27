"""ECS Fargate cluster, task definition, and Lambda functions for bulwark-cloud."""
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
from aws_cdk import aws_sns as sns
from constructs import Construct

IMAGE_TAG = "v0.1.0"
TASK_CPU = 4096    # 4 vCPU — Pass 2 runs 3 parallel Claude sessions
TASK_MEM = 16384   # 16 GB — Halmos can spike to 6 GB; Foundry ~4 GB


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
        self.repo = ecr.Repository(
            self,
            "WorkerRepo",
            repository_name="bulwark-cloud",
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
            container_insights=True,
        )

        # ── CloudWatch log group ───────────────────────────────────────────
        log_group = logs.LogGroup(
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
        # Allow execution role to pull the Anthropic secret for injection
        secrets["anthropic"].grant_read(execution_role)

        # ── IAM: task role (workload) ──────────────────────────────────────
        task_role = iam.Role(
            self,
            "TaskRole",
            role_name="bulwark-cloud-task",
            assumed_by=iam.ServicePrincipal("ecs-tasks.amazonaws.com"),
        )

        # S3: write artefacts for any job prefix (scoped at runtime via tag condition)
        task_role.add_to_policy(
            iam.PolicyStatement(
                sid="WriteJobArtefacts",
                actions=["s3:PutObject", "s3:GetObject", "s3:DeleteObject", "s3:ListBucket"],
                resources=[bucket.bucket_arn, f"{bucket.bucket_arn}/*"],
            )
        )

        # DynamoDB: update job state
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

        # Secrets Manager: read Anthropic key
        secrets["anthropic"].grant_read(task_role)

        # CloudWatch: emit custom metrics
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

        container = self.task_definition.add_container(
            "bulwark",
            image=ecs.ContainerImage.from_ecr_repository(self.repo, tag=IMAGE_TAG),
            logging=ecs.LogDrivers.aws_logs(
                stream_prefix="worker",
                log_group=log_group,
            ),
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
        _ = container  # referenced implicitly by task_definition

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
        common_layer = lambda_.LayerVersion(
            self,
            "CommonLayer",
            layer_version_name="bulwark-cloud-common",
            code=lambda_.Code.from_asset("../shared"),
            compatible_runtimes=[lambda_.Runtime.PYTHON_3_12],
            description="Shared Pydantic models and utilities",
        )

        lambda_env = {
            "DYNAMO_TABLE": table.table_name,
            "S3_BUCKET": bucket.bucket_name,
            "AWS_ACCOUNT_ID": self.account,
            "ECS_CLUSTER_ARN": self.cluster.cluster_arn,
            "TASK_DEFINITION_ARN": self.task_definition.task_definition_arn,
        }

        def _make_lambda(lid: str, code_path: str, memory: int, timeout: int) -> lambda_.Function:
            fn = lambda_.Function(
                self,
                lid,
                function_name=f"bulwark-cloud-{lid.lower().replace('_', '-')}",
                runtime=lambda_.Runtime.PYTHON_3_12,
                handler="handler.handler",
                code=lambda_.Code.from_asset(code_path),
                memory_size=memory,
                timeout=cdk.Duration.seconds(timeout),
                environment=lambda_env,
                layers=[common_layer],
                log_retention=logs.RetentionDays.ONE_MONTH,
            )
            table.grant_read_write_data(fn)
            bucket.grant_read_write(fn)
            return fn

        self.api_lambda = _make_lambda("Api", "../api", 1024, 30)
        self.submit_lambda = _make_lambda("Submit", "../lambdas/submit", 512, 60)
        self.index_findings_lambda = _make_lambda(
            "IndexFindings", "../lambdas/index_findings", 1024, 300
        )
        self.mark_failed_lambda = _make_lambda(
            "MarkFailed", "../lambdas/mark_failed", 512, 60
        )

        cdk.CfnOutput(self, "ClusterArn", value=self.cluster.cluster_arn)
        cdk.CfnOutput(
            self, "TaskDefinitionArn", value=self.task_definition.task_definition_arn
        )
        cdk.CfnOutput(self, "EcrRepoUri", value=self.repo.repository_uri)

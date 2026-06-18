"""ECS Fargate cluster, task definition, Step Functions Lambdas, and state machine.

OrchestrationStack was merged into this stack to eliminate the cross-stack
CloudFormation export of the ECS task definition ARN. Task def ARNs include
a revision number that changes on every deploy; CloudFormation refuses to
update a cross-stack export while it is imported by another stack.

The API Lambda still lives in ApiStack (downstream of this stack) to avoid
the original cycle: this stack needs the submit/index/mark-failed Lambda ARNs
for the state machine, and ApiStack needs the state machine ARN.
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
from aws_cdk import aws_sns as sns
from aws_cdk import aws_stepfunctions as sfn
from aws_cdk import aws_stepfunctions_tasks as tasks
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
        events_topic: sns.Topic,
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

        # add_container() sets this as default_container, referenced by the SFN task below.
        self.task_definition.add_container(
            "bulwark",
            image=ecs.ContainerImage.from_ecr_repository(self.repo, tag=image_tag),
            logging=ecs.LogDrivers.aws_logs(
                stream_prefix="worker",
                log_group=worker_log_group,
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

        # ── Security group for Fargate tasks ──────────────────────────────
        self.task_sg = ec2.SecurityGroup(
            self,
            "TaskSg",
            vpc=vpc,
            security_group_name="bulwark-cloud-task-sg",
            description="Fargate audit tasks - outbound HTTPS only",
            allow_all_outbound=False,
        )
        self.task_sg.add_egress_rule(
            ec2.Peer.any_ipv4(),
            ec2.Port.tcp(443),
            "HTTPS outbound for git clone + Anthropic API",
        )

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
                log_group=fn_log_group,
            )
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
            return fn

        submit_lambda = _sfn_lambda("Submit", "../lambdas/submit", 512, 60)
        index_findings_lambda = _sfn_lambda(
            "IndexFindings", "../lambdas/index_findings", 1024, 300
        )
        index_findings_lambda.add_to_role_policy(
            iam.PolicyStatement(
                sid="ReadJobReport",
                actions=["s3:GetObject"],
                resources=[f"{bucket.bucket_arn}/*"],
            )
        )
        mark_failed_lambda = _sfn_lambda("MarkFailed", "../lambdas/mark_failed", 512, 60)

        # ── Step Functions state machine (merged from OrchestrationStack) ──

        private_subnets = vpc.select_subnets(
            subnet_type=ec2.SubnetType.PRIVATE_WITH_EGRESS
        ).subnets

        submit_job = tasks.LambdaInvoke(
            self,
            "SubmitJob",
            lambda_function=submit_lambda,
            payload=sfn.TaskInput.from_json_path_at("$"),
            result_path="$.submitResult",
            retry_on_service_exceptions=True,
        )
        submit_job.add_retry(
            errors=["States.TaskFailed"],
            interval=cdk.Duration.seconds(2),
            max_attempts=3,
            backoff_rate=2.0,
        )

        run_audit = tasks.EcsRunTask(
            self,
            "RunAudit",
            cluster=self.cluster,
            task_definition=self.task_definition,
            launch_target=tasks.EcsFargateLaunchTarget(
                platform_version=ecs.FargatePlatformVersion.LATEST
            ),
            assign_public_ip=False,
            subnets=ec2.SubnetSelection(subnets=private_subnets),
            security_groups=[self.task_sg],
            container_overrides=[
                tasks.ContainerOverride(
                    container_definition=self.task_definition.default_container,  # type: ignore[arg-type]
                    environment=[
                        tasks.TaskEnvironmentVariable(
                            name="JOB_ID",
                            value=sfn.JsonPath.string_at("$.job_id"),
                        ),
                        tasks.TaskEnvironmentVariable(
                            name="TARGET_REPO",
                            value=sfn.JsonPath.string_at("$.repo"),
                        ),
                        tasks.TaskEnvironmentVariable(
                            name="TARGET_BRANCH",
                            value=sfn.JsonPath.string_at("$.branch"),
                        ),
                        tasks.TaskEnvironmentVariable(
                            name="TARGET_SCOPE",
                            value=sfn.JsonPath.json_to_string(
                                sfn.JsonPath.object_at("$.scope")
                            ),
                        ),
                        tasks.TaskEnvironmentVariable(
                            name="TARGET_CORE_CONTRACTS",
                            value=sfn.JsonPath.json_to_string(
                                sfn.JsonPath.object_at("$.core_contracts")
                            ),
                        ),
                        tasks.TaskEnvironmentVariable(
                            name="PRE_BUILD_CMD",
                            value=sfn.JsonPath.string_at("$.pre_build_cmd"),
                        ),
                        tasks.TaskEnvironmentVariable(
                            name="BULWARK_MODEL",
                            value=sfn.JsonPath.string_at("$.model"),
                        ),
                    ],
                )
            ],
            result_path="$.runResult",
            task_timeout=sfn.Timeout.duration(cdk.Duration.hours(2)),
            integration_pattern=sfn.IntegrationPattern.RUN_JOB,
        )
        run_audit.add_retry(
            errors=["States.TaskFailed"],
            interval=cdk.Duration.seconds(30),
            max_attempts=2,
            backoff_rate=4.0,
        )

        index_findings = tasks.LambdaInvoke(
            self,
            "IndexFindingsStep",
            lambda_function=index_findings_lambda,
            payload=sfn.TaskInput.from_object({"job_id": sfn.JsonPath.string_at("$.job_id")}),
            result_path="$.indexResult",
        )
        index_findings.add_retry(
            errors=["States.TaskFailed"],
            interval=cdk.Duration.seconds(5),
            max_attempts=3,
            backoff_rate=2.0,
        )

        notify_complete = tasks.SnsPublish(
            self,
            "NotifyComplete",
            topic=events_topic,
            subject="Audit complete",
            message=sfn.TaskInput.from_json_path_at("$"),
        )

        mark_failed = tasks.LambdaInvoke(
            self,
            "MarkFailedStep",
            lambda_function=mark_failed_lambda,
            payload=sfn.TaskInput.from_json_path_at("$"),
            result_path="$.markFailedResult",
        )
        notify_failed = tasks.SnsPublish(
            self,
            "NotifyFailed",
            topic=events_topic,
            subject="Audit failed",
            message=sfn.TaskInput.from_json_path_at("$"),
        )
        failure_chain = mark_failed.next(notify_failed)

        for state in [submit_job, run_audit, index_findings]:
            state.add_catch(
                failure_chain,
                errors=["States.ALL"],
                result_path="$.error",
            )

        definition = submit_job.next(run_audit).next(index_findings).next(notify_complete)

        self.state_machine = sfn.StateMachine(
            self,
            "AuditPipeline",
            state_machine_name="bulwark-cloud-audit-pipeline",
            definition_body=sfn.DefinitionBody.from_chainable(definition),
            timeout=cdk.Duration.hours(3),
            logs=sfn.LogOptions(
                destination=logs.LogGroup(
                    self,
                    "SfnLogs",
                    log_group_name="/aws/states/bulwark-cloud",
                    retention=logs.RetentionDays.ONE_MONTH,
                    removal_policy=cdk.RemovalPolicy.DESTROY,
                ),
                level=sfn.LogLevel.ERROR,
            ),
        )

        cdk.CfnOutput(self, "ClusterArn", value=self.cluster.cluster_arn)
        cdk.CfnOutput(
            self, "TaskDefinitionArn", value=self.task_definition.task_definition_arn
        )
        cdk.CfnOutput(self, "EcrRepoUri", value=self.repo.repository_uri)
        cdk.CfnOutput(self, "StateMachineArn", value=self.state_machine.state_machine_arn)

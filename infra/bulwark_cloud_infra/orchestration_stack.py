"""Step Functions state machine for the bulwark-cloud audit pipeline."""
from __future__ import annotations

import json

import aws_cdk as cdk
from aws_cdk import aws_ec2 as ec2
from aws_cdk import aws_ecs as ecs
from aws_cdk import aws_iam as iam
from aws_cdk import aws_lambda as lambda_
from aws_cdk import aws_sns as sns
from aws_cdk import aws_stepfunctions as sfn
from aws_cdk import aws_stepfunctions_tasks as tasks
from constructs import Construct


class OrchestrationStack(cdk.Stack):
    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        *,
        cluster: ecs.Cluster,
        task_definition: ecs.FargateTaskDefinition,
        private_subnets: list[ec2.ISubnet],
        task_sg: ec2.SecurityGroup,
        submit_lambda: lambda_.Function,
        index_lambda: lambda_.Function,
        mark_failed_lambda: lambda_.Function,
        events_topic: sns.Topic,
        **kwargs: object,
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)

        # ── Step 1: SubmitJob ──────────────────────────────────────────────
        submit_job = tasks.LambdaInvoke(
            self,
            "SubmitJob",
            lambda_function=submit_lambda,
            payload=sfn.TaskInput.from_json_path_at("$"),
            result_path="$.submitResult",
            retry_on_service_exceptions=True,
        ).add_retry(
            errors=["States.TaskFailed"],
            interval=cdk.Duration.seconds(2),
            max_attempts=3,
            backoff_rate=2.0,
        )

        # ── Step 2: RunAudit (ECS RunTask .sync) ──────────────────────────
        subnet_ids = [s.subnet_id for s in private_subnets]

        run_audit = tasks.EcsRunTask(
            self,
            "RunAudit",
            cluster=cluster,
            task_definition=task_definition,
            launch_target=tasks.EcsFargateLaunchTarget(
                platform_version=ecs.FargatePlatformVersion.LATEST
            ),
            assign_public_ip=False,
            subnets=ec2.SubnetSelection(subnets=private_subnets),
            security_groups=[task_sg],
            container_overrides=[
                tasks.ContainerOverride(
                    container_definition=task_definition.default_container,  # type: ignore[arg-type]
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
                            value=sfn.JsonPath.string_at("States.JsonToString($.scope)"),
                        ),
                        tasks.TaskEnvironmentVariable(
                            name="BULWARK_MODEL",
                            value=sfn.JsonPath.string_at("$.model"),
                        ),
                    ],
                )
            ],
            result_path="$.runResult",
            timeout=cdk.Duration.hours(2),  # Hard cap; typical audit is 45 min
            integration_pattern=sfn.IntegrationPattern.RUN_JOB,
        ).add_retry(
            errors=["States.TaskFailed"],
            interval=cdk.Duration.seconds(30),
            max_attempts=2,
            backoff_rate=4.0,
        )

        # ── Step 3: IndexFindings ──────────────────────────────────────────
        index_findings = tasks.LambdaInvoke(
            self,
            "IndexFindings",
            lambda_function=index_lambda,
            payload=sfn.TaskInput.from_object({"job_id": sfn.JsonPath.string_at("$.job_id")}),
            result_path="$.indexResult",
        ).add_retry(
            errors=["States.TaskFailed"],
            interval=cdk.Duration.seconds(5),
            max_attempts=3,
            backoff_rate=2.0,
        )

        # ── Step 4: NotifyComplete ─────────────────────────────────────────
        notify_complete = tasks.SnsPublish(
            self,
            "NotifyComplete",
            topic=events_topic,
            subject="Audit complete",
            message=sfn.TaskInput.from_json_path_at("$"),
        )

        # ── Failure branch ─────────────────────────────────────────────────
        mark_failed = tasks.LambdaInvoke(
            self,
            "MarkFailed",
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

        # ── Wire states ────────────────────────────────────────────────────
        for state in [submit_job, run_audit, index_findings]:
            state.add_catch(
                failure_chain,
                errors=["States.ALL"],
                result_path="$.error",
            )

        definition = submit_job.next(run_audit).next(index_findings).next(notify_complete)

        # ── State machine ──────────────────────────────────────────────────
        self.state_machine = sfn.StateMachine(
            self,
            "AuditPipeline",
            state_machine_name="bulwark-cloud-audit-pipeline",
            definition_body=sfn.DefinitionBody.from_chainable(definition),
            timeout=cdk.Duration.hours(3),
            logs=sfn.LogOptions(
                destination=cdk.aws_logs.LogGroup(
                    self,
                    "SfnLogs",
                    log_group_name="/aws/states/bulwark-cloud",
                    retention=cdk.aws_logs.RetentionDays.ONE_MONTH,
                ),
                level=sfn.LogLevel.ERROR,
            ),
        )

        cdk.CfnOutput(self, "StateMachineArn", value=self.state_machine.state_machine_arn)

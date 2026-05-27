"""API Gateway REST API and FastAPI Lambda handler for bulwark-cloud.

The API Lambda is created here (not in ComputeStack) to break the CDK
cross-stack dependency cycle. ApiStack is downstream of both ComputeStack and
OrchestrationStack so it can reference both safely.
"""
from __future__ import annotations

import aws_cdk as cdk
from aws_cdk import aws_apigateway as apigw
from aws_cdk import aws_dynamodb as dynamodb
from aws_cdk import aws_iam as iam
from aws_cdk import aws_lambda as lambda_
from aws_cdk import aws_logs as logs
from aws_cdk import aws_s3 as s3
from aws_cdk import aws_stepfunctions as sfn
from constructs import Construct


class ApiStack(cdk.Stack):
    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        *,
        state_machine: sfn.StateMachine,
        table: dynamodb.Table,
        bucket: s3.Bucket,
        common_layer: lambda_.LayerVersion,
        **kwargs: object,
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)

        # ── API Lambda (FastAPI via Mangum) ────────────────────────────────
        api_log_group = logs.LogGroup(
            self,
            "ApiLambdaLogs",
            log_group_name="/aws/lambda/bulwark-cloud-api",
            retention=logs.RetentionDays.ONE_MONTH,
            removal_policy=cdk.RemovalPolicy.DESTROY,
        )

        # Bundle: pip-install fastapi/mangum/etc. + copy bulwark_cloud_api source.
        # The shared layer (common_layer) provides bulwark_cloud_shared + pydantic.
        # requirements.txt lists all deps except the workspace shared package.
        api_lambda = lambda_.Function(
            self,
            "ApiLambda",
            function_name="bulwark-cloud-api",
            runtime=lambda_.Runtime.PYTHON_3_12,
            handler="bulwark_cloud_api.handler.handler",
            code=lambda_.Code.from_asset(
                "../api",
                bundling=cdk.BundlingOptions(
                    image=lambda_.Runtime.PYTHON_3_12.bundling_image,
                    command=[
                        "bash",
                        "-c",
                        "pip install -r /asset-input/requirements.txt -t /asset-output --quiet"
                        " && cp -r /asset-input/src/. /asset-output/",
                    ],
                ),
            ),
            memory_size=1024,
            timeout=cdk.Duration.seconds(30),
            environment={
                "DYNAMO_TABLE": table.table_name,
                "S3_BUCKET": bucket.bucket_name,
                "STATE_MACHINE_ARN": state_machine.state_machine_arn,
            },
            layers=[common_layer],
            log_group=api_log_group,
        )

        # Use add_to_role_policy instead of grant_* to avoid CDK creating resource
        # policies in StorageStack/OrchestrationStack that would form a cycle.
        api_lambda.add_to_role_policy(
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
        api_lambda.add_to_role_policy(
            iam.PolicyStatement(
                sid="S3ReadArtefacts",
                actions=["s3:GetObject", "s3:ListBucket"],
                resources=[bucket.bucket_arn, f"{bucket.bucket_arn}/*"],
            )
        )
        api_lambda.add_to_role_policy(
            iam.PolicyStatement(
                sid="SfnStartExecution",
                actions=[
                    "states:StartExecution",
                    "states:ListExecutions",
                    "states:StopExecution",
                ],
                resources=[state_machine.state_machine_arn],
            )
        )

        # ── API Gateway REST API ───────────────────────────────────────────
        access_log_group = logs.LogGroup(
            self,
            "ApiGwLogs",
            log_group_name="/aws/apigateway/bulwark-cloud",
            retention=logs.RetentionDays.ONE_MONTH,
            removal_policy=cdk.RemovalPolicy.DESTROY,
        )

        api = apigw.RestApi(
            self,
            "Api",
            rest_api_name="bulwark-cloud",
            description="bulwark-cloud public API",
            deploy_options=apigw.StageOptions(
                stage_name="v1",
                throttling_rate_limit=100,
                throttling_burst_limit=50,
                logging_level=apigw.MethodLoggingLevel.ERROR,
                data_trace_enabled=False,
                metrics_enabled=True,
                access_log_destination=apigw.LogGroupLogDestination(access_log_group),
            ),
            default_cors_preflight_options=apigw.CorsOptions(
                allow_origins=apigw.Cors.ALL_ORIGINS,
                allow_methods=apigw.Cors.ALL_METHODS,
                allow_headers=["Content-Type", "x-api-key", "Authorization"],
            ),
        )

        # All routes proxy to the FastAPI Lambda; Mangum handles routing internally
        integration = apigw.LambdaIntegration(api_lambda, proxy=True)

        audits = api.root.add_resource("audits")
        audits.add_method("GET", integration, api_key_required=True)
        audits.add_method("POST", integration, api_key_required=True)

        audit = audits.add_resource("{job_id}")
        audit.add_method("GET", integration, api_key_required=True)
        audit.add_method("DELETE", integration, api_key_required=True)
        audit.add_resource("cancel").add_method("POST", integration, api_key_required=True)

        findings = audit.add_resource("findings")
        findings.add_method("GET", integration, api_key_required=True)
        findings.add_resource("{finding_id}").add_method(
            "GET", integration, api_key_required=True
        )

        audit.add_resource("report").add_method("GET", integration, api_key_required=True)

        # Health check is unauthenticated
        api.root.add_resource("health").add_method("GET", integration)

        # ── Usage plan + API key ───────────────────────────────────────────
        plan = api.add_usage_plan(
            "DefaultPlan",
            name="bulwark-cloud-default",
            throttle=apigw.ThrottleSettings(rate_limit=10, burst_limit=20),
            quota=apigw.QuotaSettings(limit=1000, period=apigw.Period.DAY),
        )
        plan.add_api_stage(stage=api.deployment_stage)

        api_key = api.add_api_key("DefaultApiKey", api_key_name="bulwark-cloud-default")
        plan.add_api_key(api_key)

        cdk.CfnOutput(self, "ApiUrl", value=api.url)
        cdk.CfnOutput(
            self,
            "ApiKeyId",
            value=api_key.key_id,
            description="Get value: aws apigateway get-api-key --api-key {id} --include-value",
        )

"""API Gateway REST API wired to the FastAPI Lambda handler."""
from __future__ import annotations

import aws_cdk as cdk
from aws_cdk import aws_apigateway as apigw
from aws_cdk import aws_dynamodb as dynamodb
from aws_cdk import aws_lambda as lambda_
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
        api_lambda: lambda_.Function,
        **kwargs: object,
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)

        # Grant the API Lambda permission to start Step Functions executions
        state_machine.grant_start_execution(api_lambda)
        api_lambda.add_environment("STATE_MACHINE_ARN", state_machine.state_machine_arn)

        # ── API Gateway REST API ───────────────────────────────────────────
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
            ),
            default_cors_preflight_options=apigw.CorsOptions(
                allow_origins=apigw.Cors.ALL_ORIGINS,
                allow_methods=apigw.Cors.ALL_METHODS,
                allow_headers=["Content-Type", "x-api-key", "Authorization"],
            ),
        )

        # All routes proxy to the FastAPI Lambda (Mangum handles routing internally)
        integration = apigw.LambdaIntegration(api_lambda, proxy=True)

        # /v1/audits
        audits = api.root.add_resource("audits")
        audits.add_method("GET", integration, api_key_required=True)
        audits.add_method("POST", integration, api_key_required=True)

        # /v1/audits/{job_id}
        audit = audits.add_resource("{job_id}")
        audit.add_method("GET", integration, api_key_required=True)
        audit.add_method("DELETE", integration, api_key_required=True)

        # /v1/audits/{job_id}/cancel
        audit.add_resource("cancel").add_method("POST", integration, api_key_required=True)

        # /v1/audits/{job_id}/findings
        findings = audit.add_resource("findings")
        findings.add_method("GET", integration, api_key_required=True)
        findings.add_resource("{finding_id}").add_method("GET", integration, api_key_required=True)

        # /v1/audits/{job_id}/report
        audit.add_resource("report").add_method("GET", integration, api_key_required=True)

        # /v1/health (no auth)
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
            description="Retrieve value: aws apigateway get-api-key --api-key {id} --include-value",
        )

"""Static frontend: S3 bucket + CloudFront distribution."""
from __future__ import annotations

import aws_cdk as cdk
from aws_cdk import aws_cloudfront as cloudfront
from aws_cdk import aws_cloudfront_origins as origins
from aws_cdk import aws_s3 as s3
from aws_cdk import aws_s3_deployment as s3deploy
from constructs import Construct


class FrontendStack(cdk.Stack):
    def __init__(self, scope: Construct, construct_id: str, **kwargs: object) -> None:
        super().__init__(scope, construct_id, **kwargs)

        bucket = s3.Bucket(
            self,
            "FrontendBucket",
            removal_policy=cdk.RemovalPolicy.DESTROY,
            auto_delete_objects=True,
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
            enforce_ssl=True,
        )

        distribution = cloudfront.Distribution(
            self,
            "Cdn",
            default_behavior=cloudfront.BehaviorOptions(
                origin=origins.S3BucketOrigin.with_origin_access_control(bucket),
                viewer_protocol_policy=cloudfront.ViewerProtocolPolicy.REDIRECT_TO_HTTPS,
                # No caching: dashboard needs fresh data; add TTL when assets are versioned.
                cache_policy=cloudfront.CachePolicy.CACHING_DISABLED,
                allowed_methods=cloudfront.AllowedMethods.ALLOW_GET_HEAD,
            ),
            default_root_object="index.html",
            # Route unknown paths back to index.html (single-page app)
            error_responses=[
                cloudfront.ErrorResponse(
                    http_status=403,
                    response_page_path="/index.html",
                    response_http_status=200,
                ),
                cloudfront.ErrorResponse(
                    http_status=404,
                    response_page_path="/index.html",
                    response_http_status=200,
                ),
            ],
        )

        s3deploy.BucketDeployment(
            self,
            "Deploy",
            sources=[s3deploy.Source.asset("../frontend")],
            destination_bucket=bucket,
            distribution=distribution,
            distribution_paths=["/*"],
            memory_limit=256,
        )

        cdk.CfnOutput(
            self,
            "DashboardUrl",
            value=f"https://{distribution.distribution_domain_name}",
            description="Bulwark Cloud dashboard URL",
        )

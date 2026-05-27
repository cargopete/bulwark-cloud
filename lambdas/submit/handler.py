"""Step Functions SubmitJob Lambda.

Transitions the job from PENDING to PROVISIONING and returns the ECS task
parameters (subnets, security groups, env vars) for the RunAudit state.

Input:  { job_id, repo, branch, scope, model }
Output: { job_id, ..., subnets: [...], securityGroups: [...], environment: [...] }
"""
from __future__ import annotations

import json
import os
from datetime import UTC, datetime

import boto3


def handler(event: dict, context: object) -> dict:
    job_id: str = event["job_id"]
    table_name = os.environ["DYNAMO_TABLE"]
    region = os.environ.get("AWS_REGION", "eu-central-1")
    subnet_ids = os.environ["PRIVATE_SUBNET_IDS"].split(",")
    sg_id = os.environ["TASK_SG_ID"]
    secret_arn = os.environ["SECRET_ARN_ANTHROPIC"]
    s3_bucket = os.environ["S3_BUCKET"]

    dynamo = boto3.resource("dynamodb", region_name=region).Table(table_name)
    dynamo.update_item(
        Key={"PK": f"JOB#{job_id}", "SK": "METADATA"},
        UpdateExpression="SET #s = :s, GSI2PK = :gpk",
        ExpressionAttributeNames={"#s": "status"},
        ExpressionAttributeValues={":s": "PROVISIONING", ":gpk": "STATUS#PROVISIONING"},
    )

    # Return parameters for the ECS RunTask state
    return {
        **event,
        "subnets": subnet_ids,
        "securityGroups": [sg_id],
        "environment": [
            {"name": "JOB_ID", "value": job_id},
            {"name": "TARGET_REPO", "value": event["repo"]},
            {"name": "TARGET_BRANCH", "value": event.get("branch", "main")},
            {"name": "TARGET_SCOPE", "value": json.dumps(event.get("scope", []))},
            {"name": "BULWARK_MODEL", "value": event.get("model", "haiku")},
            {"name": "S3_BUCKET", "value": s3_bucket},
            {"name": "DYNAMO_TABLE", "value": table_name},
            {"name": "SECRET_ARN_ANTHROPIC", "value": secret_arn},
            {"name": "AWS_REGION", "value": region},
        ],
    }

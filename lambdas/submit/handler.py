"""Step Functions SubmitJob Lambda.

Transitions the job from PENDING to PROVISIONING. Network parameters (subnets,
security groups) are hardcoded in the CDK EcsRunTask construct, not returned here.

Input:  { job_id, repo, branch, scope, model }
Output: { job_id, repo, branch, scope, model }  (pass-through, result_path=$.submitResult)
"""
from __future__ import annotations

import os
from typing import Any

import boto3


def handler(event: dict[str, Any], context: object) -> dict[str, Any]:
    job_id: str = event["job_id"]
    table_name = os.environ["DYNAMO_TABLE"]
    region = os.environ.get("AWS_REGION", "eu-north-1")

    dynamo = boto3.resource("dynamodb", region_name=region).Table(table_name)
    dynamo.update_item(
        Key={"PK": f"JOB#{job_id}", "SK": "METADATA"},
        UpdateExpression="SET #s = :s, GSI2PK = :gpk",
        ExpressionAttributeNames={"#s": "status"},
        ExpressionAttributeValues={":s": "PROVISIONING", ":gpk": "STATUS#PROVISIONING"},
    )

    return {"job_id": job_id}

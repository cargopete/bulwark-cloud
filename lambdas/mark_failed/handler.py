"""Step Functions MarkFailed Lambda.

Called on the failure branch of the state machine. Transitions the job to
FAILED and writes a structured error record for operator diagnosis.

Input:  { job_id, error: { Cause, Error } }
Output: { job_id, status: FAILED }
"""
from __future__ import annotations

import os
from datetime import UTC, datetime
from typing import Any

import boto3


def handler(event: dict[str, Any], context: object) -> dict[str, Any]:
    job_id: str = event["job_id"]
    table_name = os.environ["DYNAMO_TABLE"]
    region = os.environ.get("AWS_REGION", "eu-north-1")

    error = event.get("error", {})
    cause = error.get("Cause", "unknown")
    error_type = error.get("Error", "States.ALL")

    dynamo = boto3.resource("dynamodb", region_name=region).Table(table_name)
    dynamo.update_item(
        Key={"PK": f"JOB#{job_id}", "SK": "METADATA"},
        UpdateExpression=(
            "SET #s = :s, completed_at = :t, failure_reason = :r, "
            "failure_detail = :d, GSI2PK = :gpk"
        ),
        ExpressionAttributeNames={"#s": "status"},
        ExpressionAttributeValues={
            ":s": "FAILED",
            ":t": datetime.now(UTC).isoformat(),
            ":r": error_type,
            ":d": cause[:2000],  # DynamoDB item size limit protection
            ":gpk": "STATUS#FAILED",
        },
    )

    return {"job_id": job_id, "status": "FAILED", "error": error_type}

"""Step Functions IndexFindings Lambda.

Reads final-report.json from S3, writes one DynamoDB item per finding,
updates job status to COMPLETED, and returns a findings count summary.

Input:  { job_id }
Output: { job_id, findings_count: {CRITICAL: n, ...} }
"""
from __future__ import annotations

import json
import os
from collections import Counter
from datetime import UTC, datetime

import boto3


def handler(event: dict, context: object) -> dict:
    job_id: str = event["job_id"]
    table_name = os.environ["DYNAMO_TABLE"]
    s3_bucket = os.environ["S3_BUCKET"]
    region = os.environ.get("AWS_REGION", "eu-north-1")

    s3 = boto3.client("s3", region_name=region)
    dynamo = boto3.resource("dynamodb", region_name=region).Table(table_name)

    # ── Read report ────────────────────────────────────────────────────────
    key = f"{job_id}/report/final-report.json"
    try:
        obj = s3.get_object(Bucket=s3_bucket, Key=key)
        report = json.loads(obj["Body"].read())
    except s3.exceptions.NoSuchKey:
        # Report not present — audit may have produced no findings or failed
        report = {"findings": []}

    findings = report.get("findings", [])

    def _normalise_severity(raw: str) -> str:
        """Map bulwark title-case severities → uppercase DynamoDB literals."""
        s = raw.upper()
        return "INFO" if s in ("INFORMATIONAL", "INFO") else s

    # ── Write findings to DynamoDB ─────────────────────────────────────────
    with dynamo.batch_writer() as batch:
        for idx, f in enumerate(findings, start=1):
            finding_id = f.get("id", f"F-{idx:03d}")
            severity = _normalise_severity(f.get("severity", "LOW"))
            batch.put_item(
                Item={
                    "PK": f"JOB#{job_id}",
                    "SK": f"FINDING#{finding_id}",
                    "type": "Finding",
                    "finding_id": finding_id,
                    "title": f.get("title", ""),
                    "severity": severity,
                    "source_pass": f.get("source_pass", 2),
                    "poc_validated": f.get("poc_validated", False),
                    "formal_verified": f.get("formal_verified", False),
                    "contract": f.get("contract"),
                    "function": f.get("function"),
                    "description": f.get("description", ""),
                    "report_anchor": f.get("report_anchor"),
                    "GSI1PK": f"SEVERITY#{severity}",
                    "GSI1SK": f"JOB#{job_id}#FINDING#{finding_id}",
                }
            )

    # ── Mark job completed ─────────────────────────────────────────────────
    now = datetime.now(UTC).isoformat()
    dynamo.update_item(
        Key={"PK": f"JOB#{job_id}", "SK": "METADATA"},
        UpdateExpression="SET #s = :s, completed_at = :t, GSI2PK = :gpk",
        ExpressionAttributeNames={"#s": "status"},
        ExpressionAttributeValues={
            ":s": "COMPLETED",
            ":t": now,
            ":gpk": "STATUS#COMPLETED",
        },
    )

    counts = Counter(_normalise_severity(f.get("severity", "LOW")) for f in findings)
    return {"job_id": job_id, "findings_count": dict(counts)}

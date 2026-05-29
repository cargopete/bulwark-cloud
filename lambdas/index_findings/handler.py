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
from typing import Any

import boto3

# Kept in sync with bulwark_cloud_shared.models.VALID_SEVERITIES. Inlined because
# the SFN lambdas are bundled from their own directory only — no shared package.
VALID_SEVERITIES = frozenset({"CRITICAL", "HIGH", "MEDIUM", "LOW", "INFO"})


def _normalise_severity(raw: str) -> str | None:
    """Map a bulwark severity to a valid DynamoDB literal, or None if invalid.

    bulwark emits title-case severities ("Critical") and occasionally overloads
    the field with a status ("INVALID - FALSE POSITIVE") for dismissed findings.
    Returns None for anything that isn't a real severity so the caller can skip it.
    """
    s = raw.strip().upper()
    if s in ("INFORMATIONAL", "INFO"):
        return "INFO"
    return s if s in VALID_SEVERITIES else None


def handler(event: dict[str, Any], context: object) -> dict[str, Any]:
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

    # ── Write findings to DynamoDB ─────────────────────────────────────────
    indexed = 0
    with dynamo.batch_writer() as batch:
        for f in findings:
            severity = _normalise_severity(f.get("severity", "LOW"))
            if severity is None:
                # Not a real finding (e.g. dismissed false positive) — skip.
                continue
            indexed += 1
            finding_id = f.get("id", f"F-{indexed:03d}")
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

    counts = Counter(
        sev
        for f in findings
        if (sev := _normalise_severity(f.get("severity", "LOW"))) is not None
    )
    return {"job_id": job_id, "findings_count": dict(counts)}

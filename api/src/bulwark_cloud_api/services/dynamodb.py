"""DynamoDB adapter for the API Lambda.

All reads go here; writes from the API are limited to job creation, status
updates (cancel/delete), and finding creation via IndexFindings Lambda.
"""
from __future__ import annotations

import base64
import json
from datetime import UTC, datetime
from typing import Any, cast

import boto3
from boto3.dynamodb.conditions import Key
from bulwark_cloud_shared.models import (
    PASS_NAMES,
    VALID_SEVERITIES,
    Audit,
    AuditSubmit,
    AuditSummary,
    Finding,
    FindingSummary,
    PassProgress,
)

from ..settings import Settings


class DynamoService:
    def __init__(self, settings: Settings) -> None:
        self._table = boto3.resource("dynamodb", region_name=settings.aws_region).Table(
            settings.dynamo_table
        )

    # ── Job operations ─────────────────────────────────────────────────────

    def create_job(self, *, job_id: str, body: AuditSubmit, created_at: datetime) -> None:
        now = created_at.isoformat()
        self._table.put_item(
            Item={
                "PK": f"JOB#{job_id}",
                "SK": "METADATA",
                "type": "Job",
                "job_id": job_id,
                "tenant_id": "anon",
                "status": "PENDING",
                "repo": body.repo,
                "branch": body.branch,
                "scope": body.scope,
                "model": body.model,
                "created_at": now,
                "GSI2PK": "STATUS#PENDING",
                "GSI2SK": now,
            }
        )

    def get_job(self, job_id: str) -> Audit | None:
        resp = self._table.get_item(Key={"PK": f"JOB#{job_id}", "SK": "METADATA"})
        raw = resp.get("Item")
        if not raw or raw.get("deleted_at"):
            return None
        item = cast("dict[str, Any]", raw)

        passes = self._get_passes(job_id)
        findings_count = self._get_findings_count(job_id)

        return Audit(
            job_id=job_id,
            status=item["status"],
            repo=item["repo"],
            branch=item["branch"],
            scope=item.get("scope", []),
            model=item.get("model", "haiku"),
            created_at=item["created_at"],
            started_at=item.get("started_at"),
            completed_at=item.get("completed_at"),
            passes=passes,
            findings_count=cast("Any", findings_count),
            failure_reason=item.get("failure_reason"),
        )

    def list_jobs(
        self, *, status: str | None, limit: int, cursor: str | None
    ) -> tuple[list[AuditSummary], str | None]:
        kwargs: dict[str, Any] = {"Limit": limit}
        if cursor:
            kwargs["ExclusiveStartKey"] = json.loads(base64.b64decode(cursor))

        if status:
            resp = self._table.query(
                IndexName="GSI2",
                KeyConditionExpression=Key("GSI2PK").eq(f"STATUS#{status}"),
                ScanIndexForward=False,
                **kwargs,
            )
        else:
            # Can't begins_with on a partition key — scan with SK filter instead.
            # Fine for M3 scale; replace with multi-status parallel queries at M5.
            from boto3.dynamodb.conditions import Attr

            kwargs.pop("ScanIndexForward", None)
            resp = self._table.scan(
                FilterExpression=Attr("SK").eq("METADATA") & Attr("deleted_at").not_exists(),
                **kwargs,
            )

        items = [
            AuditSummary(
                job_id=i["job_id"],
                status=i["status"],
                repo=i["repo"],
                branch=i.get("branch", "main"),
                model=i.get("model", "haiku"),
                created_at=i["GSI2SK"],
                completed_at=i.get("completed_at"),
            )
            for i in (cast("dict[str, Any]", x) for x in resp.get("Items", []))
        ]
        last_key = resp.get("LastEvaluatedKey")
        next_cursor = base64.b64encode(json.dumps(last_key).encode()).decode() if last_key else None
        return items, next_cursor

    def update_status(self, job_id: str, status: str) -> None:
        self._table.update_item(
            Key={"PK": f"JOB#{job_id}", "SK": "METADATA"},
            UpdateExpression="SET #s = :s, GSI2PK = :gpk",
            ExpressionAttributeNames={"#s": "status"},
            ExpressionAttributeValues={":s": status, ":gpk": f"STATUS#{status}"},
        )

    def soft_delete(self, job_id: str) -> None:
        self._table.update_item(
            Key={"PK": f"JOB#{job_id}", "SK": "METADATA"},
            UpdateExpression="SET deleted_at = :t",
            ExpressionAttributeValues={":t": datetime.now(UTC).isoformat()},
        )

    # ── Findings operations ────────────────────────────────────────────────

    def list_findings(
        self,
        *,
        job_id: str,
        severity: str | None,
        validated_only: bool,
        limit: int,
        cursor: str | None,
    ) -> tuple[list[FindingSummary], str | None]:
        kwargs: dict[str, Any] = {
            "KeyConditionExpression": Key("PK").eq(f"JOB#{job_id}")
            & Key("SK").begins_with("FINDING#"),
            "Limit": limit,
        }
        if cursor:
            kwargs["ExclusiveStartKey"] = json.loads(base64.b64decode(cursor))

        resp = self._table.query(**kwargs)
        items: list[dict[str, Any]] = [
            cast("dict[str, Any]", x) for x in resp.get("Items", [])
        ]

        # Drop items whose stored severity isn't a real severity — they can't be
        # represented as FindingSummary and shouldn't surface as findings anyway.
        items = [i for i in items if str(i.get("severity", "")).upper() in VALID_SEVERITIES]
        if severity:
            items = [i for i in items if i.get("severity") == severity]
        if validated_only:
            items = [i for i in items if i.get("poc_validated")]

        summaries = [
            FindingSummary(
                finding_id=i["finding_id"],
                title=i["title"],
                severity=i["severity"],
                source_pass=i["source_pass"],
                poc_validated=i.get("poc_validated", False),
                formal_verified=i.get("formal_verified", False),
                contract=i.get("contract"),
                function=i.get("function"),
            )
            for i in items
        ]
        last_key = resp.get("LastEvaluatedKey")
        next_cursor = base64.b64encode(json.dumps(last_key).encode()).decode() if last_key else None
        return summaries, next_cursor

    def get_finding(self, *, job_id: str, finding_id: str) -> Finding | None:
        resp = self._table.get_item(
            Key={"PK": f"JOB#{job_id}", "SK": f"FINDING#{finding_id}"}
        )
        raw = resp.get("Item")
        if not raw:
            return None
        item = cast("dict[str, Any]", raw)
        return Finding(
            finding_id=item["finding_id"],
            title=item["title"],
            severity=item["severity"],
            source_pass=item["source_pass"],
            poc_validated=item.get("poc_validated", False),
            formal_verified=item.get("formal_verified", False),
            contract=item.get("contract"),
            function=item.get("function"),
            description=item.get("description", ""),
            poc_source=item.get("poc_source"),
            formal_result=item.get("formal_result"),
            report_anchor=item.get("report_anchor"),
        )

    # ── Private helpers ────────────────────────────────────────────────────

    def _get_passes(self, job_id: str) -> list[PassProgress]:
        resp = self._table.query(
            KeyConditionExpression=Key("PK").eq(f"JOB#{job_id}")
            & Key("SK").begins_with("PASS#"),
        )
        result = []
        for raw in resp.get("Items", []):
            i: dict[str, Any] = dict(raw)
            pass_number: int = i.get("pass_number") or int(str(i["SK"]).split("#")[1])
            result.append(
                PassProgress(
                    pass_number=pass_number,
                    name=PASS_NAMES.get(pass_number, "unknown"),
                    status=i.get("status", "PENDING"),
                    started_at=i.get("started_at"),
                    completed_at=i.get("completed_at"),
                    duration_seconds=i.get("duration_seconds"),
                    findings_emitted=i.get("findings_emitted"),
                    anthropic_tokens=i.get("anthropic_tokens"),
                )
            )
        return result

    def _get_findings_count(self, job_id: str) -> dict[str, int]:
        resp = self._table.query(
            KeyConditionExpression=Key("PK").eq(f"JOB#{job_id}")
            & Key("SK").begins_with("FINDING#"),
            ProjectionExpression="severity",
        )
        counts: dict[str, int] = {}
        for raw in resp.get("Items", []):
            item: dict[str, Any] = dict(raw)
            sev: str = str(item.get("severity", "LOW")).upper()
            # Ignore non-severity values (e.g. a dismissed finding stored with a
            # status string); they must not break the Audit model's Severity dict.
            if sev not in VALID_SEVERITIES:
                continue
            counts[sev] = counts.get(sev, 0) + 1
        return counts

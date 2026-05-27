"""DynamoDB state management for the Fargate task."""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import boto3
import structlog
from bulwark_cloud_shared.models import PASS_NAMES

log = structlog.get_logger()


def _now() -> str:
    return datetime.now(UTC).isoformat()


class DynamoWriter:
    def __init__(self, session: boto3.Session, table_name: str, job_id: str) -> None:
        self._table = session.resource("dynamodb").Table(table_name)
        self._pk = f"JOB#{job_id}"
        self._job_id = job_id

    def mark_running(self) -> None:
        self._table.update_item(
            Key={"PK": self._pk, "SK": "METADATA"},
            UpdateExpression="SET #s = :s, started_at = :t, GSI2PK = :gpk",
            ExpressionAttributeNames={"#s": "status"},
            ExpressionAttributeValues={
                ":s": "RUNNING",
                ":t": _now(),
                ":gpk": "STATUS#RUNNING",
            },
        )

    def mark_completed(self) -> None:
        now = _now()
        self._table.update_item(
            Key={"PK": self._pk, "SK": "METADATA"},
            UpdateExpression="SET #s = :s, completed_at = :t, GSI2PK = :gpk",
            ExpressionAttributeNames={"#s": "status"},
            ExpressionAttributeValues={
                ":s": "COMPLETED",
                ":t": now,
                ":gpk": "STATUS#COMPLETED",
            },
        )

    def mark_failed(self, *, reason: str, detail: str = "") -> None:
        self._table.update_item(
            Key={"PK": self._pk, "SK": "METADATA"},
            UpdateExpression="SET #s = :s, completed_at = :t, failure_reason = :r, failure_detail = :d, GSI2PK = :gpk",
            ExpressionAttributeNames={"#s": "status"},
            ExpressionAttributeValues={
                ":s": "FAILED",
                ":t": _now(),
                ":r": reason,
                ":d": detail,
                ":gpk": "STATUS#FAILED",
            },
        )

    def update_pass(
        self,
        pass_number: int,
        *,
        status: str,
        duration_seconds: int | None = None,
        findings_emitted: int | None = None,
        anthropic_tokens: dict[str, int] | None = None,
    ) -> None:
        expr_parts = ["#s = :s", "pass_number = :pn", "pass_name = :nm"]
        attr_names: dict[str, str] = {"#s": "status"}
        attr_values: dict[str, Any] = {
            ":s": status,
            ":pn": pass_number,
            ":nm": PASS_NAMES.get(pass_number, "unknown"),
        }

        if status == "RUNNING":
            expr_parts.append("started_at = :t")
            attr_values[":t"] = _now()
        elif status == "COMPLETED":
            expr_parts.append("completed_at = :t")
            attr_values[":t"] = _now()

        if duration_seconds is not None:
            expr_parts.append("duration_seconds = :dur")
            attr_values[":dur"] = duration_seconds

        if findings_emitted is not None:
            expr_parts.append("findings_emitted = :fe")
            attr_values[":fe"] = findings_emitted

        if anthropic_tokens is not None:
            expr_parts.append("anthropic_tokens = :at")
            attr_values[":at"] = anthropic_tokens

        self._table.update_item(
            Key={"PK": self._pk, "SK": f"PASS#{pass_number:02d}"},
            UpdateExpression="SET " + ", ".join(expr_parts),
            ExpressionAttributeNames=attr_names,
            ExpressionAttributeValues=attr_values,
        )
        log.info("pass_updated", pass_number=pass_number, status=status)

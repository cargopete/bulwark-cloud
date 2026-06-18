"""Step Functions operations for the API Lambda."""
from __future__ import annotations

import json

import boto3
from bulwark_cloud_shared.models import AuditSubmit

from ..settings import Settings


class SfnService:
    def __init__(self, settings: Settings) -> None:
        self._client = boto3.client("stepfunctions", region_name=settings.aws_region)
        self._state_machine_arn = settings.state_machine_arn

    def start_execution(self, *, job_id: str, body: AuditSubmit) -> str:
        resp = self._client.start_execution(
            stateMachineArn=self._state_machine_arn,
            name=job_id,
            input=json.dumps(
                {
                    "job_id": job_id,
                    "repo": body.repo,
                    "branch": body.branch,
                    "scope": body.scope,
                    "core_contracts": body.core_contracts,
                    "pre_build_cmd": body.pre_build_cmd,
                    "model": body.model,
                }
            ),
        )
        return resp["executionArn"]

    def stop_execution(self, *, job_id: str) -> None:
        # List executions to find the ARN for this job_id (name == job_id)
        resp = self._client.list_executions(
            stateMachineArn=self._state_machine_arn,
            statusFilter="RUNNING",
        )
        for execution in resp.get("executions", []):
            if execution["name"] == job_id:
                self._client.stop_execution(
                    executionArn=execution["executionArn"],
                    cause="Cancelled by user",
                )
                return

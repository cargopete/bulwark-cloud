"""Basic smoke tests for audit endpoints."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from bulwark_cloud_api.main import app

client = TestClient(app)


def test_health():
    resp = client.get("/v1/health")
    assert resp.status_code == 200


@patch("bulwark_cloud_api.routes.audits.DynamoService")
@patch("bulwark_cloud_api.routes.audits.SfnService")
def test_submit_audit(mock_sfn_cls, mock_dynamo_cls):
    mock_dynamo = MagicMock()
    mock_sfn = MagicMock()
    mock_dynamo_cls.return_value = mock_dynamo
    mock_sfn_cls.return_value = mock_sfn
    mock_sfn.start_execution.return_value = "arn:aws:states:eu-central-1:123:execution/test"

    resp = client.post(
        "/v1/audits",
        json={
            "repo": "https://github.com/graphprotocol/contracts.git",
            "branch": "main",
            "scope": ["packages/horizon"],
            "model": "haiku",
        },
    )
    assert resp.status_code == 201
    data = resp.json()
    assert "job_id" in data
    assert data["status"] == "PENDING"


@patch("bulwark_cloud_api.routes.audits.DynamoService")
def test_get_audit_not_found(mock_dynamo_cls):
    mock_dynamo = MagicMock()
    mock_dynamo.get_job.return_value = None
    mock_dynamo_cls.return_value = mock_dynamo

    resp = client.get("/v1/audits/NONEXISTENT")
    assert resp.status_code == 404


@patch("bulwark_cloud_api.routes.audits.DynamoService")
def test_list_audits_no_filter(mock_dynamo_cls):
    from bulwark_cloud_shared.models import AuditSummary
    from datetime import datetime, UTC

    mock_dynamo = MagicMock()
    mock_dynamo.list_jobs.return_value = (
        [
            AuditSummary(
                job_id="01JTEST",
                status="COMPLETED",
                repo="https://github.com/foo/bar",
                branch="main",
                model="haiku",
                created_at=datetime(2025, 1, 1, tzinfo=UTC),
            )
        ],
        None,
    )
    mock_dynamo_cls.return_value = mock_dynamo

    resp = client.get("/v1/audits")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["items"]) == 1
    assert data["next_cursor"] is None
    mock_dynamo.list_jobs.assert_called_once_with(status=None, limit=20, cursor=None)


@patch("bulwark_cloud_api.routes.audits.DynamoService")
@patch("bulwark_cloud_api.routes.audits.SfnService")
def test_cancel_audit_running(mock_sfn_cls, mock_dynamo_cls):
    from bulwark_cloud_shared.models import Audit
    from datetime import datetime, UTC

    mock_dynamo = MagicMock()
    mock_sfn = MagicMock()
    mock_dynamo_cls.return_value = mock_dynamo
    mock_sfn_cls.return_value = mock_sfn
    mock_dynamo.get_job.return_value = Audit(
        job_id="01JTEST",
        status="RUNNING",
        repo="https://github.com/foo/bar",
        branch="main",
        scope=[],
        model="haiku",
        created_at=datetime(2025, 1, 1, tzinfo=UTC),
    )

    resp = client.post("/v1/audits/01JTEST/cancel")
    assert resp.status_code == 202
    assert resp.json()["status"] == "CANCELLING"
    mock_sfn.stop_execution.assert_called_once_with(job_id="01JTEST")
    mock_dynamo.update_status.assert_called_once_with("01JTEST", "CANCELLING")


@patch("bulwark_cloud_api.routes.audits.DynamoService")
@patch("bulwark_cloud_api.routes.audits.SfnService")
def test_cancel_audit_already_completed(mock_sfn_cls, mock_dynamo_cls):
    from bulwark_cloud_shared.models import Audit
    from datetime import datetime, UTC

    mock_dynamo = MagicMock()
    mock_sfn = MagicMock()
    mock_dynamo_cls.return_value = mock_dynamo
    mock_sfn_cls.return_value = mock_sfn
    mock_dynamo.get_job.return_value = Audit(
        job_id="01JTEST",
        status="COMPLETED",
        repo="https://github.com/foo/bar",
        branch="main",
        scope=[],
        model="haiku",
        created_at=datetime(2025, 1, 1, tzinfo=UTC),
    )

    resp = client.post("/v1/audits/01JTEST/cancel")
    assert resp.status_code == 409
    mock_sfn.stop_execution.assert_not_called()


@patch("bulwark_cloud_api.routes.audits.DynamoService")
def test_delete_audit(mock_dynamo_cls):
    mock_dynamo = MagicMock()
    mock_dynamo_cls.return_value = mock_dynamo

    resp = client.delete("/v1/audits/01JTEST")
    assert resp.status_code == 202
    assert resp.json()["deleted"] is True
    mock_dynamo.soft_delete.assert_called_once_with("01JTEST")


def test_submit_audit_invalid_repo():
    resp = client.post(
        "/v1/audits",
        json={"repo": "ssh://git@github.com/foo/bar", "branch": "main"},
    )
    assert resp.status_code == 422

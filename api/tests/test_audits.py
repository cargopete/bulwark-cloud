"""Basic smoke tests for audit endpoints."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

from bulwark_cloud_api.main import app
from fastapi.testclient import TestClient

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
    mock_sfn.start_execution.return_value = "arn:aws:states:eu-north-1:123:execution/test"

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
    from datetime import UTC, datetime

    from bulwark_cloud_shared.models import AuditSummary

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
    from datetime import UTC, datetime

    from bulwark_cloud_shared.models import Audit

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
    from datetime import UTC, datetime

    from bulwark_cloud_shared.models import Audit

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


@patch("bulwark_cloud_api.routes.reports.DynamoService")
@patch("bulwark_cloud_api.routes.reports.S3Service")
def test_get_report_completed(mock_s3_cls, mock_dynamo_cls):
    from datetime import UTC, datetime

    from bulwark_cloud_shared.models import Audit

    mock_dynamo = MagicMock()
    mock_s3 = MagicMock()
    mock_dynamo_cls.return_value = mock_dynamo
    mock_s3_cls.return_value = mock_s3
    mock_dynamo.get_job.return_value = Audit(
        job_id="01JTEST",
        status="COMPLETED",
        repo="https://github.com/foo/bar",
        branch="main",
        scope=[],
        model="haiku",
        created_at=datetime(2025, 1, 1, tzinfo=UTC),
    )
    mock_s3.presign.return_value = "https://s3.example.com/signed"

    resp = client.get("/v1/audits/01JTEST/report")
    assert resp.status_code == 200
    data = resp.json()
    assert data["url"] == "https://s3.example.com/signed"
    assert data["format"] == "md"
    assert data["expires_in"] == 300
    mock_s3.presign.assert_called_once_with("01JTEST/report/final-report.md", expires_in=300)


@patch("bulwark_cloud_api.routes.reports.DynamoService")
@patch("bulwark_cloud_api.routes.reports.S3Service")
def test_get_report_not_completed(mock_s3_cls, mock_dynamo_cls):
    from datetime import UTC, datetime

    from bulwark_cloud_shared.models import Audit

    mock_dynamo = MagicMock()
    mock_s3 = MagicMock()
    mock_dynamo_cls.return_value = mock_dynamo
    mock_s3_cls.return_value = mock_s3
    mock_dynamo.get_job.return_value = Audit(
        job_id="01JTEST",
        status="RUNNING",
        repo="https://github.com/foo/bar",
        branch="main",
        scope=[],
        model="haiku",
        created_at=datetime(2025, 1, 1, tzinfo=UTC),
    )

    resp = client.get("/v1/audits/01JTEST/report")
    assert resp.status_code == 409
    mock_s3.presign.assert_not_called()


@patch("bulwark_cloud_api.routes.findings.DynamoService")
def test_list_findings(mock_dynamo_cls):
    from bulwark_cloud_shared.models import FindingSummary

    mock_dynamo = MagicMock()
    mock_dynamo_cls.return_value = mock_dynamo
    mock_dynamo.list_findings.return_value = (
        [
            FindingSummary(
                finding_id="F-001",
                title="Reentrancy in withdraw",
                severity="HIGH",
                source_pass=3,
                poc_validated=True,
                formal_verified=False,
                contract="Vault",
                function="withdraw",
            )
        ],
        None,
    )

    resp = client.get("/v1/audits/01JTEST/findings")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["items"]) == 1
    assert data["items"][0]["finding_id"] == "F-001"
    assert data["items"][0]["poc_validated"] is True

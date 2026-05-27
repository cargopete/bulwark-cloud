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

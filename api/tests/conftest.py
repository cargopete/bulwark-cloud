"""Pytest fixtures for API tests using moto for AWS mocking."""
from __future__ import annotations

import os

import boto3
import pytest
from moto import mock_aws

# Set dummy AWS credentials before any boto3 calls
os.environ.setdefault("AWS_DEFAULT_REGION", "eu-north-1")
os.environ.setdefault("AWS_ACCESS_KEY_ID", "testing")
os.environ.setdefault("AWS_SECRET_ACCESS_KEY", "testing")
os.environ.setdefault("AWS_SECURITY_TOKEN", "testing")
os.environ.setdefault("AWS_SESSION_TOKEN", "testing")

# Service env vars used by the API
os.environ.setdefault("DYNAMO_TABLE", "bulwark-cloud-state")
os.environ.setdefault("S3_BUCKET", "bulwark-cloud-artefacts-test")
os.environ.setdefault("STATE_MACHINE_ARN", "arn:aws:states:eu-north-1:123456789012:stateMachine:test")
os.environ.setdefault("AWS_REGION", "eu-north-1")


@pytest.fixture
def aws_credentials():
    """Mocked AWS credentials for moto."""
    os.environ["AWS_ACCESS_KEY_ID"] = "testing"
    os.environ["AWS_SECRET_ACCESS_KEY"] = "testing"
    os.environ["AWS_SECURITY_TOKEN"] = "testing"
    os.environ["AWS_SESSION_TOKEN"] = "testing"


@pytest.fixture
def dynamo_table(aws_credentials):
    with mock_aws():
        client = boto3.client("dynamodb", region_name="eu-north-1")
        client.create_table(
            TableName="bulwark-cloud-state",
            KeySchema=[
                {"AttributeName": "PK", "KeyType": "HASH"},
                {"AttributeName": "SK", "KeyType": "RANGE"},
            ],
            AttributeDefinitions=[
                {"AttributeName": "PK", "AttributeType": "S"},
                {"AttributeName": "SK", "AttributeType": "S"},
                {"AttributeName": "GSI2PK", "AttributeType": "S"},
                {"AttributeName": "GSI2SK", "AttributeType": "S"},
            ],
            GlobalSecondaryIndexes=[
                {
                    "IndexName": "GSI2",
                    "KeySchema": [
                        {"AttributeName": "GSI2PK", "KeyType": "HASH"},
                        {"AttributeName": "GSI2SK", "KeyType": "RANGE"},
                    ],
                    "Projection": {"ProjectionType": "ALL"},
                }
            ],
            BillingMode="PAY_PER_REQUEST",
        )
        yield boto3.resource("dynamodb", region_name="eu-north-1").Table("bulwark-cloud-state")

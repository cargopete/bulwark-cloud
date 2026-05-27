"""Environment variable parsing for the ECS task."""
from __future__ import annotations

import json
import os

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings


class JobEnv(BaseSettings):
    """All environment variables injected by Step Functions / ECS."""

    job_id: str = Field(alias="JOB_ID")
    s3_bucket: str = Field(alias="S3_BUCKET")
    target_repo: str = Field(alias="TARGET_REPO")
    target_branch: str = Field(default="main", alias="TARGET_BRANCH")
    target_scope: list[str] = Field(default_factory=list, alias="TARGET_SCOPE")
    dynamo_table: str = Field(alias="DYNAMO_TABLE")
    secret_arn_anthropic: str = Field(alias="SECRET_ARN_ANTHROPIC")
    model: str = Field(default="haiku", alias="BULWARK_MODEL")
    aws_region: str = Field(alias="AWS_REGION")

    model_config = {"populate_by_name": True}

    @model_validator(mode="before")
    @classmethod
    def parse_scope(cls, values: dict[str, object]) -> dict[str, object]:
        scope = values.get("TARGET_SCOPE")
        if isinstance(scope, str):
            values["TARGET_SCOPE"] = json.loads(scope)
        return values

    @classmethod
    def from_env(cls) -> "JobEnv":
        return cls(**{k: v for k, v in os.environ.items()})

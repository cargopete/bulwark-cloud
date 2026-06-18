"""Environment variable parsing for the ECS task."""
from __future__ import annotations

import json

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings


class JobEnv(BaseSettings):
    """All environment variables injected by Step Functions / ECS."""

    job_id: str = Field(alias="JOB_ID")
    s3_bucket: str = Field(alias="S3_BUCKET")
    target_repo: str = Field(alias="TARGET_REPO")
    target_branch: str = Field(default="main", alias="TARGET_BRANCH")
    target_scope: list[str] = Field(default_factory=list, alias="TARGET_SCOPE")
    target_core_contracts: list[str] = Field(
        default_factory=list, alias="TARGET_CORE_CONTRACTS"
    )
    pre_build_cmd: str = Field(default="", alias="PRE_BUILD_CMD")
    dynamo_table: str = Field(alias="DYNAMO_TABLE")
    secret_arn_anthropic: str = Field(alias="SECRET_ARN_ANTHROPIC")
    model: str = Field(default="haiku", alias="BULWARK_MODEL")
    aws_region: str = Field(alias="AWS_REGION")

    model_config = {"populate_by_name": True}

    @model_validator(mode="before")
    @classmethod
    def parse_json_lists(cls, values: dict[str, object]) -> dict[str, object]:
        # Step Functions injects list fields as JSON-encoded strings.
        for key in ("TARGET_SCOPE", "TARGET_CORE_CONTRACTS"):
            raw = values.get(key)
            if isinstance(raw, str) and raw:
                values[key] = json.loads(raw)
        return values

    @classmethod
    def from_env(cls) -> JobEnv:
        # pydantic-settings populates all fields from environment variables via
        # their aliases; mypy can't see that, hence the call-arg ignore.
        return cls()  # type: ignore[call-arg]

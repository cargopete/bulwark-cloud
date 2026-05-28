"""API Lambda configuration from environment variables."""
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    dynamo_table: str = "bulwark-cloud-state"
    s3_bucket: str = ""
    state_machine_arn: str = ""
    aws_region: str = "eu-north-1"
    stage: str = "dev"
    api_url: str = ""  # Set by CDK; used to build self-referential URLs in responses

    model_config = {"env_prefix": "", "case_sensitive": False}


_settings: Settings | None = None


def get_settings() -> Settings:
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings

"""API Lambda configuration from environment variables."""
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    dynamo_table: str = "bulwark-cloud-state"
    s3_bucket: str = ""
    state_machine_arn: str = ""
    aws_region: str = "eu-central-1"
    stage: str = "dev"

    model_config = {"env_prefix": "", "case_sensitive": False}


_settings: Settings | None = None


def get_settings() -> Settings:
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings

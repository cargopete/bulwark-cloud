"""S3 operations for the API Lambda (signed URLs only — no direct writes)."""
from __future__ import annotations

import boto3

from ..settings import Settings


class S3Service:
    def __init__(self, settings: Settings) -> None:
        self._client = boto3.client("s3", region_name=settings.aws_region)
        self._bucket = settings.s3_bucket

    def presign(self, key: str, *, expires_in: int = 300) -> str:
        return self._client.generate_presigned_url(
            "get_object",
            Params={"Bucket": self._bucket, "Key": key},
            ExpiresIn=expires_in,
        )

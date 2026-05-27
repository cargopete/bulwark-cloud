"""S3 upload logic for audit workspace artefacts."""
from __future__ import annotations

from pathlib import Path

import boto3
import structlog

log = structlog.get_logger()

# Files larger than this will use multipart upload (boto3 handles automatically
# via TransferConfig, but we set the threshold explicitly for clarity)
MULTIPART_THRESHOLD = 10 * 1024 * 1024  # 10 MB


class S3Uploader:
    def __init__(self, session: boto3.Session, bucket: str, job_id: str) -> None:
        self._s3 = session.client("s3")
        self._bucket = bucket
        self._job_id = job_id

    def upload_workspace(self, workspace: Path) -> int:
        """Upload all files under workspace/ to s3://{bucket}/{job_id}/workspace/.

        Keys: {job_id}/workspace/recon/entry-points.json, etc.
        Returns number of files uploaded.
        """
        count = 0
        for path in sorted(workspace.rglob("*")):
            if not path.is_file():
                continue
            relative = path.relative_to(workspace)
            key = f"{self._job_id}/workspace/{relative}"
            self._upload_file(path, key)
            count += 1

        log.info("s3_upload_complete", files=count, bucket=self._bucket, prefix=self._job_id)
        return count

    def upload_report(self, workspace: Path) -> None:
        """Upload just the final report files to a dedicated report/ prefix for quick access."""
        for name in ("final-report.md", "final-report.json"):
            src = workspace / name
            if src.exists():
                key = f"{self._job_id}/report/{name}"
                self._upload_file(src, key)

    def get_signed_url(self, key: str, expires_in: int = 300) -> str:
        """Generate a pre-signed GET URL valid for `expires_in` seconds (default 5 min)."""
        return self._s3.generate_presigned_url(
            "get_object",
            Params={"Bucket": self._bucket, "Key": key},
            ExpiresIn=expires_in,
        )

    def _upload_file(self, path: Path, key: str) -> None:
        log.debug("s3_upload", key=key, size=path.stat().st_size)
        self._s3.upload_file(
            str(path),
            self._bucket,
            key,
            ExtraArgs={"ServerSideEncryption": "AES256"},
        )

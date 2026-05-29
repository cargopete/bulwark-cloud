"""Report download endpoint — returns a short-lived S3 signed URL."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query

from ..services.dynamodb import DynamoService
from ..services.s3 import S3Service
from ..settings import Settings, get_settings

router = APIRouter(tags=["reports"])


def _dynamo(settings: Settings = Depends(get_settings)) -> DynamoService:
    return DynamoService(settings)


def _s3(settings: Settings = Depends(get_settings)) -> S3Service:
    return S3Service(settings)


@router.get("/audits/{job_id}/report")
async def get_report(
    job_id: str,
    format: str = Query(default="md", pattern="^(md|json)$"),
    dynamo: DynamoService = Depends(_dynamo),
    s3: S3Service = Depends(_s3),
) -> dict[str, Any]:
    """Return a 5-minute pre-signed S3 URL for the final report.

    Returns JSON so browser clients can open it in a new tab. The URL itself
    is unauthenticated (signed), so no x-api-key is needed to follow it.
    """
    audit = dynamo.get_job(job_id)
    if audit is None:
        raise HTTPException(status_code=404, detail="Audit not found")
    if audit.status != "COMPLETED":
        raise HTTPException(
            status_code=409, detail=f"Report not yet available (status={audit.status})"
        )

    key = f"{job_id}/report/final-report.{format}"
    url = s3.presign(key, expires_in=300)
    return {"job_id": job_id, "format": format, "url": url, "expires_in": 300}

"""Report download endpoint — returns a short-lived S3 signed URL."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import RedirectResponse

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
) -> RedirectResponse:
    audit = dynamo.get_job(job_id)
    if audit is None:
        raise HTTPException(status_code=404, detail="Audit not found")
    if audit.status != "COMPLETED":
        raise HTTPException(status_code=409, detail=f"Audit not yet complete (status={audit.status})")

    filename = f"final-report.{format}"
    key = f"{job_id}/report/{filename}"
    signed_url = s3.presign(key, expires_in=300)

    return RedirectResponse(url=signed_url, status_code=302)

"""Audit CRUD endpoints: submit, list, get, cancel, delete."""
from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from ulid import ULID

from bulwark_cloud_shared.models import Audit, AuditCreated, AuditSubmit, AuditSummary

from ..services.dynamodb import DynamoService
from ..services.step_functions import SfnService
from ..settings import Settings, get_settings

router = APIRouter(tags=["audits"])


def _dynamo(settings: Settings = Depends(get_settings)) -> DynamoService:
    return DynamoService(settings)


def _sfn(settings: Settings = Depends(get_settings)) -> SfnService:
    return SfnService(settings)


@router.post("/audits", status_code=201, response_model=AuditCreated)
async def submit_audit(
    body: AuditSubmit,
    settings: Settings = Depends(get_settings),
    dynamo: DynamoService = Depends(_dynamo),
    sfn: SfnService = Depends(_sfn),
) -> AuditCreated:
    job_id = str(ULID())
    now = datetime.now(UTC)

    dynamo.create_job(job_id=job_id, body=body, created_at=now)
    sfn.start_execution(job_id=job_id, body=body)

    api_base = settings.api_url.rstrip("/") if settings.api_url else ""
    base_url = f"{api_base}/audits/{job_id}"
    return AuditCreated(
        job_id=job_id,
        status="PENDING",
        created_at=now,
        status_url=base_url,
    )


@router.get("/audits", response_model=dict)
async def list_audits(
    status: str | None = Query(default=None),
    limit: int = Query(default=20, ge=1, le=100),
    cursor: str | None = Query(default=None),
    dynamo: DynamoService = Depends(_dynamo),
) -> dict:
    items, next_cursor = dynamo.list_jobs(status=status, limit=limit, cursor=cursor)
    return {"items": [i.model_dump() for i in items], "next_cursor": next_cursor}


@router.get("/audits/{job_id}", response_model=Audit)
async def get_audit(
    job_id: str,
    dynamo: DynamoService = Depends(_dynamo),
) -> Audit:
    audit = dynamo.get_job(job_id)
    if audit is None:
        raise HTTPException(status_code=404, detail="Audit not found")
    return audit


@router.post("/audits/{job_id}/cancel", status_code=202)
async def cancel_audit(
    job_id: str,
    dynamo: DynamoService = Depends(_dynamo),
    sfn: SfnService = Depends(_sfn),
) -> dict:
    audit = dynamo.get_job(job_id)
    if audit is None:
        raise HTTPException(status_code=404, detail="Audit not found")
    if audit.status in ("COMPLETED", "FAILED", "CANCELLED"):
        raise HTTPException(status_code=409, detail=f"Cannot cancel audit in status {audit.status}")

    sfn.stop_execution(job_id=job_id)
    dynamo.update_status(job_id, "CANCELLING")
    return {"job_id": job_id, "status": "CANCELLING"}


@router.delete("/audits/{job_id}", status_code=202)
async def delete_audit(
    job_id: str,
    dynamo: DynamoService = Depends(_dynamo),
) -> dict:
    dynamo.soft_delete(job_id)
    return {"job_id": job_id, "deleted": True}

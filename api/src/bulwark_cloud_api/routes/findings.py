"""Findings retrieval endpoints."""
from __future__ import annotations

from bulwark_cloud_shared.models import Finding
from fastapi import APIRouter, Depends, HTTPException, Query

from ..services.dynamodb import DynamoService
from ..settings import Settings, get_settings

router = APIRouter(tags=["findings"])


def _dynamo(settings: Settings = Depends(get_settings)) -> DynamoService:
    return DynamoService(settings)


@router.get("/audits/{job_id}/findings", response_model=dict)
async def list_findings(
    job_id: str,
    severity: str | None = Query(default=None),
    validated_only: bool = Query(default=False),
    limit: int = Query(default=50, ge=1, le=200),
    cursor: str | None = Query(default=None),
    dynamo: DynamoService = Depends(_dynamo),
) -> dict:
    items, next_cursor = dynamo.list_findings(
        job_id=job_id,
        severity=severity,
        validated_only=validated_only,
        limit=limit,
        cursor=cursor,
    )
    return {"items": [i.model_dump() for i in items], "next_cursor": next_cursor}


@router.get("/audits/{job_id}/findings/{finding_id}", response_model=Finding)
async def get_finding(
    job_id: str,
    finding_id: str,
    dynamo: DynamoService = Depends(_dynamo),
) -> Finding:
    finding = dynamo.get_finding(job_id=job_id, finding_id=finding_id)
    if finding is None:
        raise HTTPException(status_code=404, detail="Finding not found")
    return finding

"""Shared Pydantic models for bulwark-cloud.

These are the canonical data types used across the API, orchestrator, and
Lambda functions. Import from here, not from component-specific modules.
"""
from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

# ── Type aliases ───────────────────────────────────────────────────────────
Severity = Literal["CRITICAL", "HIGH", "MEDIUM", "LOW", "INFO"]
JobStatus = Literal[
    "PENDING", "PROVISIONING", "RUNNING", "COMPLETED", "FAILED", "CANCELLED", "CANCELLING"
]
PassStatus = Literal["PENDING", "RUNNING", "COMPLETED", "FAILED", "SKIPPED"]
Model = Literal["haiku", "sonnet", "opus"]

PASS_NAMES: dict[int, str] = {
    1: "reconnaissance",
    2: "multi-agent-analysis",
    3: "poc-gate",
    4: "fuzzing",
    5: "formal-verification",
    6: "adversarial-review",
}


# ── Request models ─────────────────────────────────────────────────────────

class AuditSubmit(BaseModel):
    repo: str = Field(
        ...,
        pattern=r"^https://github\.com/.+",
        examples=["https://github.com/graphprotocol/contracts"],
    )
    branch: str = Field(default="main")
    scope: list[str] = Field(
        default_factory=list,
        examples=[["packages/horizon", "packages/subgraph-service"]],
    )
    core_contracts: list[str] = Field(
        default_factory=list,
        examples=[["HorizonStaking", "GraphPayments"]],
    )
    model: Model = "haiku"


# ── Response models ────────────────────────────────────────────────────────

class AuditCreated(BaseModel):
    """Returned immediately on POST /v1/audits."""
    job_id: str
    status: JobStatus = "PENDING"
    created_at: datetime
    status_url: str
    estimated_duration_seconds: int = 2700  # ~45 min typical


class PassProgress(BaseModel):
    pass_number: int = Field(ge=1, le=6)
    name: str
    status: PassStatus
    started_at: datetime | None = None
    completed_at: datetime | None = None
    duration_seconds: int | None = None
    findings_emitted: int | None = None
    anthropic_tokens: dict[str, int] | None = None


class Finding(BaseModel):
    finding_id: str = Field(pattern=r"^F-\d{3}$")
    title: str
    severity: Severity
    source_pass: int
    poc_validated: bool
    formal_verified: bool
    contract: str | None = None
    function: str | None = None
    description: str
    poc_source: str | None = None          # raw .t.sol content, if available
    formal_result: str | None = None       # VERIFIED / VIOLATED / TIMEOUT
    report_anchor: str | None = None


class FindingSummary(BaseModel):
    """Lightweight finding for list responses."""
    finding_id: str
    title: str
    severity: Severity
    source_pass: int
    poc_validated: bool
    formal_verified: bool
    contract: str | None = None
    function: str | None = None


class Audit(BaseModel):
    """Full audit detail returned by GET /v1/audits/{job_id}."""
    job_id: str
    status: JobStatus
    repo: str
    branch: str
    scope: list[str]
    model: Model
    created_at: datetime
    started_at: datetime | None = None
    completed_at: datetime | None = None
    duration_seconds: int | None = None
    passes: list[PassProgress] = Field(default_factory=list)
    findings_count: dict[Severity, int] = Field(default_factory=dict)
    report_url: str | None = None
    anthropic_cost_usd: float | None = None
    failure_reason: str | None = None


class AuditSummary(BaseModel):
    """Lightweight audit for list responses."""
    job_id: str
    status: JobStatus
    repo: str
    branch: str
    model: Model
    created_at: datetime
    completed_at: datetime | None = None
    findings_count: dict[Severity, int] = Field(default_factory=dict)


# ── DynamoDB item shapes ───────────────────────────────────────────────────

class JobItem(BaseModel):
    """DynamoDB item: PK=JOB#{id} SK=METADATA."""
    PK: str
    SK: str = "METADATA"
    type: str = "Job"
    job_id: str
    tenant_id: str = "anon"
    status: JobStatus
    repo: str
    branch: str
    scope: list[str]
    model: Model
    created_at: str
    started_at: str | None = None
    completed_at: str | None = None
    task_arn: str | None = None
    failure_reason: str | None = None
    failure_detail: str | None = None
    GSI2PK: str = "STATUS#PENDING"
    GSI2SK: str = ""


class PassItem(BaseModel):
    """DynamoDB item: PK=JOB#{id} SK=PASS#{n:02d}."""
    PK: str
    SK: str
    type: str = "PassStatus"
    pass_number: int
    pass_name: str
    status: PassStatus = "PENDING"
    started_at: str | None = None
    completed_at: str | None = None
    duration_seconds: int | None = None
    findings_emitted: int | None = None
    anthropic_tokens: dict[str, int] | None = None


class FindingItem(BaseModel):
    """DynamoDB item: PK=JOB#{id} SK=FINDING#{n:03d}."""
    PK: str
    SK: str
    type: str = "Finding"
    finding_id: str
    title: str
    severity: Severity
    source_pass: int
    poc_validated: bool
    formal_verified: bool
    contract: str | None = None
    function: str | None = None
    description: str
    report_anchor: str | None = None
    GSI1PK: str = ""   # SEVERITY#{sev}
    GSI1SK: str = ""   # JOB#{id}#FINDING#{n}

# RFC BC-001: bulwark-cloud

**Title**: bulwark-cloud — AWS-native platform for AI-augmented smart-contract auditing
**RFC number**: BC-001
**Author**: Petko Pavlovski (`@cargopete`)
**Status**: Draft
**Created**: 2026-05-27
**Last updated**: 2026-05-27
**Target implementation**: v0.1 in 4–6 weeks

---

## Table of contents

1. [Summary](#1-summary)
2. [Motivation](#2-motivation)
3. [Goals and non-goals](#3-goals-and-non-goals)
4. [Background](#4-background)
5. [Architecture overview](#5-architecture-overview)
6. [Detailed design](#6-detailed-design)
7. [API specification](#7-api-specification)
8. [Data model](#8-data-model)
9. [Operational concerns](#9-operational-concerns)
10. [Security model](#10-security-model)
11. [Failure modes and recovery](#11-failure-modes-and-recovery)
12. [Cost model](#12-cost-model)
13. [Implementation plan](#13-implementation-plan)
14. [Alternatives considered](#14-alternatives-considered)
15. [Open questions](#15-open-questions)
16. [Future work](#16-future-work)
17. [Appendices](#17-appendices)

---

## 1. Summary

This RFC proposes **bulwark-cloud**: an AWS-native platform that wraps the existing Bulwark CLI
(Rust + Halmos + Slither + Foundry + Claude Code) to provide on-demand smart-contract auditing
as a service.

Bulwark today is a single-machine Docker container that runs a 6-pass audit pipeline
end-to-end in approximately 45 minutes. It produces validated findings backed by formal
verification but is not addressable as a service: there is no submission API, no multi-tenancy,
no shared state, no asynchronous notification, and no horizontal scaling.

bulwark-cloud closes that gap. Audits are submitted via a public HTTPS API, orchestrated by AWS
Step Functions, executed as parameterised ECS Fargate tasks against the Bulwark container image,
and surfaced through a customer-facing dashboard and JSON API. Job state and findings persist in
DynamoDB; intermediate artifacts and final reports land in S3 with signed-URL access. The
platform is Python-first, AWS-native, and deployable from a single CDK stack.

This RFC specifies the architecture in full, including data model, API surface, IAM model,
failure semantics, cost projections, and a phased implementation plan targeting a 4–6 week
timeline.

---

## 2. Motivation

### 2.1 Why a cloud platform now

Bulwark proves the audit pipeline works. A single audit on The Graph Protocol Horizon contracts
produced one validated Critical finding (slash front-run, P-10 violation), end-to-end with no
human in the loop, in 45 minutes. The pipeline composition — deterministic recon, multi-agent
adversarial LLM analysis, PoC validation gate, fuzzing, formal verification, adversarial review
— is sound.

What the project lacks is everything between *"the binary works on my laptop"* and *"customers
run audits on demand"*:

- **No remote submission.** Every audit requires SSH access to a machine with the container running.
- **No multi-tenancy.** Concurrent audits would clobber `audit-workspace/` artefacts.
- **No durability.** Findings live on the host filesystem until manually copied.
- **No async notification.** A 45-minute audit blocks whatever terminal initiated it.
- **No scheduling or queueing.** Two simultaneous requests serialise behind the same container.
- **No observability.** Failures are diagnosed by reading container logs by hand.
- **No security posture.** The `ANTHROPIC_API_KEY` lives in a `.env` file; there is no isolation
  between audit jobs from different sources.

These gaps are not Bulwark's failures — Bulwark is a tool. They are the gaps the **platform
layer** is meant to fill.

### 2.2 Architectural parallel to industrial verification platforms

Production verification platforms — Certora's Prover, SymCC, Manticore-as-a-service, Trail of
Bits' internal tooling — share the same shape:

1. A computationally expensive engine (solver / prover / auditor) that runs as a containerised binary
2. An orchestration layer that schedules jobs, fans out parallel work, retries on failure, and persists results
3. A customer-facing API and dashboard
4. Multi-tenant isolation, billing-grade aggregates, and audit trails

bulwark-cloud is this pattern applied to Bulwark. The engine (Bulwark) stays as-is; everything
else is platform.

### 2.3 Why this is the right shape for the project

The decision to wrap rather than rewrite is deliberate (see [§14.1](#141-rewriting-bulwark-in-python)).
Bulwark today is 64 commits of working Rust, with integrations into Slither, Foundry, Halmos,
Claude Code, Trail of Bits skills, and forefy `/.context` skills. Reproducing that surface in
Python would take months; doing so under deadline would ship a worse product than the original.

The platform layer is where Python and AWS belong: API surfaces, orchestration, data lifecycle,
customer-facing reporting. The audit engine layer is where Rust + Halmos + Foundry already belong.

---

## 3. Goals and non-goals

### 3.1 Goals

**G1.** Submit an audit job via HTTPS in under one second.
**G2.** Run audits asynchronously on ECS Fargate without blocking the submitter.
**G3.** Persist all intermediate and final artefacts durably in S3.
**G4.** Surface findings, status, and reports via a JSON API and minimal dashboard.
**G5.** Isolate concurrent audits from different submitters at the storage, IAM, and network layers.
**G6.** Retry transient failures automatically; fail fast on terminal failures with structured error reporting.
**G7.** Observe per-pass duration, success rate, cost, and Anthropic token consumption from CloudWatch.
**G8.** Deploy the entire stack from a single CDK command targeting any AWS account.
**G9.** Run a complete audit from API submission to final report for under $25 of cloud + LLM cost at default model (Haiku).
**G10.** Achieve p95 audit completion within 75 minutes for The Graph Horizon-scale targets.

### 3.2 Non-goals

**NG1.** This RFC does not propose changes to the Bulwark binary itself. Bulwark is consumed as a containerised black box.
**NG2.** No human-in-the-loop review workflow. Findings are surfaced as Bulwark produces them; triage by human auditors is future work.
**NG3.** No billing, subscription, or rate-limiting beyond IAM-enforced API key throttling.
**NG4.** No support for non-EVM chains in this RFC. Bulwark currently targets Solidity / EVM; expansion to Solana, Stellar, etc. follows Bulwark itself.
**NG5.** No on-premises deployment. AWS-native is a deliberate constraint to leverage Step Functions, IAM, and managed services.
**NG6.** No real-time progress streaming. Status is polled via API; SSE / WebSocket is future work.
**NG7.** Bulwark image build and publication are out of scope. This RFC assumes a published `bulwark-cli:vX.Y` image in ECR.

---

## 4. Background

### 4.1 Bulwark today

Bulwark is a Rust CLI invoked inside a Docker container that runs a 6-pass smart-contract audit
pipeline. Each pass produces structured JSON artefacts that the next pass consumes.

```
Pass 1: Reconnaissance        ~1 min    Slither + forge inspect + Rust analysis
Pass 2: Multi-agent analysis  ~16 min   3x parallel Claude (RED/BLUE/GOLD)
Pass 3: PoC validation gate   ~5-15 min Forge test compilation + execution
Pass 4: Fuzzing campaign      ~8 min    Foundry invariant tests
Pass 5: Formal verification   ~12 min   Halmos bounded model checking
Pass 6: Adversarial review    ~3 min    Fresh Claude session challenges all
```

The CLI accepts configuration via `bulwark.toml`, reads `ANTHROPIC_API_KEY` from the
environment, clones a target repository, and writes all output to a per-run `audit-workspace/`
directory. A successful run produces `final-report.md` and `final-report.json` alongside
per-pass JSON artefacts.

The container image bundles Slither, Foundry (`forge`, `cast`), Halmos, Claude Code, ~70 audit
skills from Trail of Bits and forefy, and the Rust `bulwark` binary itself. The image is
approximately 1.8 GB.

### 4.2 What's missing

The Bulwark CLI is single-tenant by design. Bulwark-cloud's job is to add:

| Capability | Missing today | Provided by bulwark-cloud |
|---|---|---|
| Remote submission | SSH / local CLI only | HTTPS API + CLI client |
| Async execution | Synchronous, blocks the submitter | Step Functions + ECS, returns `job_id` |
| Multi-tenant isolation | Single workspace per host | Per-job S3 prefix, DynamoDB scoping, IAM scoping |
| Durable artefacts | Local filesystem | S3 with lifecycle policies |
| Findings indexing | Flat JSON file | DynamoDB with severity GSI |
| Status tracking | `bulwark status` requires container access | API endpoint reading DynamoDB |
| Notification | None | SNS topic for completion / failure events |
| Cost & token observability | None | CloudWatch metrics, custom Anthropic token metric |
| Secret management | `.env` file | AWS Secrets Manager with rotation hooks |
| Authorisation | None | API Gateway API keys (v0.1), Cognito (v0.2) |
| Retries | Manual | Step Functions retry policy |

---

## 5. Architecture overview

### 5.1 High-level diagram

```
        +------------------+
        |  CLI / Dashboard |
        +--------+---------+
                 |  HTTPS
                 v
        +--------------------------------------+
        |  API Gateway (Regional, REST)        |
        |  + Lambda (Python 3.12, FastAPI)     |
        +--------+-----------------------------+
                 |
                 |  StartExecution
                 v
        +--------------------------------------+
        |  Step Functions State Machine        |
        |                                      |
        |  +- SubmitJob (Lambda)               |
        |  +- RunAudit (ECS RunTask .sync)     |
        |  +- IndexFindings (Lambda)           |
        |  +- NotifyComplete (SNS Publish)     |
        +--------+-----------------------------+
                 |
                 |  RunTask
                 v
        +--------------------------------------+
        |  ECS Fargate Task                    |
        |  +--------------------------------+  |
        |  |  bulwark-cloud:latest          |  |
        |  |                                |  |
        |  |  orchestrator/entrypoint.py    |  |
        |  |  -> subprocess: bulwark run    |  |
        |  |  -> upload artefacts to S3     |  |
        |  |  -> write findings to Dynamo   |  |
        |  +--------------------------------+  |
        +--------+-----------+----------------+
                 |           |
                 v           v
        +------------+ +--------------+
        |     S3     | |   DynamoDB   |
        | (artefacts)| | (job state + |
        |            | |  findings)   |
        +------------+ +--------------+

        Supporting: Secrets Manager, ECR, CloudWatch, SNS, EventBridge
```

### 5.2 Component summary

| Component | AWS service | Purpose |
|---|---|---|
| Public API | API Gateway + Lambda (FastAPI) | Job submission, status, findings retrieval |
| Workflow orchestration | Step Functions | Pipeline state machine, retries, timeouts |
| Audit execution | ECS on Fargate | Containerised Bulwark with Python wrapper |
| Image registry | ECR | `bulwark-cloud:vX.Y` images |
| Artefact storage | S3 | Per-job prefix, lifecycle to Glacier after 90 days |
| State + findings | DynamoDB (single-table) | Job metadata, pass status, findings indexed by severity |
| Secrets | Secrets Manager | `ANTHROPIC_API_KEY`, GitHub token (optional) |
| Notifications | SNS | Audit completion / failure events |
| Observability | CloudWatch Logs + Metrics + Dashboards | Per-pass duration, success rate, cost |
| Networking | VPC + private subnets + NAT Gateway | Fargate tasks egress for git clone + Anthropic |
| Frontend | CloudFront + S3 (static Next.js export) | Customer dashboard |

### 5.3 Data flow: submit-to-report

```
1. Client -> API Gateway: POST /v1/audits { repo, branch, scope }
2. API Gateway -> submit Lambda (FastAPI handler):
     - Validate input (Pydantic)
     - Generate job_id (ULID)
     - PutItem in DynamoDB: JOB#{id} SK=METADATA status=PENDING
     - StartExecution on Step Functions with { job_id, repo, branch, scope }
     - Return { job_id, status: PENDING, status_url, report_url } in 201 Created
3. Step Functions state machine executes:
     a. SubmitJob (Lambda): UpdateItem: status=PROVISIONING
     b. RunAudit (ECS RunTask .sync): Fargate launches entrypoint.py
          - Pulls ANTHROPIC_API_KEY from Secrets Manager
          - git-clones target into /tmp
          - Writes /workspace/bulwark.toml
          - subprocess.run("bulwark run")
          - Each pass: UpdateItem on PASS#{n} sub-record
          - On exit 0: upload audit-workspace/ to S3
     c. IndexFindings (Lambda): Parse final-report.json, write findings to DynamoDB
     d. NotifyComplete (SNS): Publish event { job_id, status, findings_count }
4. Client polls GET /v1/audits/{job_id} -> reads DynamoDB
5. Client GET /v1/audits/{job_id}/report -> S3 signed URL
```

---

## 6. Detailed design

### 6.1 Container model

#### 6.1.1 Image composition

```dockerfile
FROM cargopete/bulwark-cli:v0.1.0 AS bulwark

FROM bulwark
LABEL component=bulwark-cloud
LABEL version=0.1.0

WORKDIR /app
COPY orchestrator/ /app/orchestrator/
COPY requirements.txt /app/
RUN pip install --no-cache-dir -r /app/requirements.txt

USER bulwark
ENTRYPOINT ["python", "/app/orchestrator/entrypoint.py"]
```

Image size budget: <= 2.2 GB compressed.

#### 6.1.2 entrypoint.py responsibilities

1. Parse environment variables (JOB_ID, S3_BUCKET, TARGET_REPO, TARGET_BRANCH, TARGET_SCOPE,
   DYNAMO_TABLE, SECRET_ARN_ANTHROPIC, BULWARK_MODEL, AWS_REGION)
2. Fetch ANTHROPIC_API_KEY from Secrets Manager via task IAM role
3. Update DynamoDB: status=RUNNING
4. Prepare workspace: mkdir, git clone, render bulwark.toml
5. Invoke Bulwark as subprocess; stream stdout to CloudWatch via structlog
6. Parse per-pass progress from stdout; update DynamoDB PASS#{n} sub-records
7. On Bulwark exit: upload artefacts to S3
8. Update DynamoDB: status=COMPLETED or FAILED
9. Exit with same code as Bulwark

#### 6.1.3 Exit code convention

| Code | Meaning | Step Functions retry |
|------|---------|----------------------|
| 0 | Success | n/a |
| 1-9 | Job-level failure (e.g. compile error) | No |
| 10-19 | Transient infrastructure failure | Yes (2x) |
| 20-29 | Terminal infrastructure failure | No |

### 6.2 Step Functions state machine

States: SubmitJob -> RunAudit (.sync, 7200s timeout) -> IndexFindings -> NotifyComplete

Failure branch: any state -> MarkFailed -> NotifyFailed

Retry: RunAudit retries 2x on exit codes 10-19 with 30s / 120s back-off.

Full ASL JSON: see [Appendix B](#appendix-b--step-functions-definition-asl-json).

### 6.3 Storage layer

S3 bucket: `bulwark-cloud-artefacts-{account_id}-{region}`

Per-job prefix: `{job_id}/` (input/, workspace/, report/)

Lifecycle: Intelligent-Tiering at 30 days, workspace/* expires at 90 days, report/ retained.

DynamoDB single-table: `bulwark-cloud-state`

Item types: Job (METADATA), PassStatus (PASS#{n}), Finding (FINDING#{n})

GSI1: severity-indexed findings (`SEVERITY#{sev}`)
GSI2: recent-jobs-by-status (`STATUS#{status}`)

Full schema: see [§8](#8-data-model).

### 6.4 Compute layer

ECS Fargate task: 4 vCPU / 16 GB RAM. Rationale: Pass 2 runs 3 parallel Claude sessions;
Pass 4 fuzzing is CPU-heavy; Pass 5 Halmos needs ~6 GB RAM on large contracts.

Three Lambda functions:
- `bulwark-cloud-api` (1024 MB, 30s) — FastAPI via Mangum
- `bulwark-cloud-submit` (512 MB, 60s) — Step Functions initial state
- `bulwark-cloud-index-findings` (1024 MB, 300s) — parse report, write DynamoDB

### 6.5 API layer

FastAPI on Lambda via Mangum, fronted by API Gateway REST API.

Project structure: `api/src/bulwark_cloud_api/{main,handler,routes/,models/,services/,settings}.py`

### 6.6 Networking

VPC: /16 CIDR, 3 private + 3 public subnets, NAT Gateway.

VPC endpoints (mandatory): S3 (gateway), DynamoDB (gateway), ECR API+DKR (interface),
Secrets Manager (interface), CloudWatch Logs (interface).

### 6.7 IAM model

Roles: task-execution, task (write S3/DynamoDB, scoped by JobId tag), step-functions,
lambda-api, lambda-submit, lambda-index.

Critical: task role uses `aws:RequestTag/JobId` condition to scope per-job access.

### 6.8 Secrets management

`bulwark-cloud/anthropic` in Secrets Manager, injected as Fargate task secret (not env var
in task definition). Never logged.

### 6.9 Observability

Structured JSON logs via structlog -> CloudWatch Logs `/ecs/bulwark-cloud`.

Custom metrics namespace `BulwarkCloud`: audit.completed, audit.duration, pass.duration,
findings.emitted, anthropic.tokens, anthropic.cost_usd.

Two dashboards: Operational + Cost.

Alarms: audit duration p99 > 90min, task launch failures, Lambda errors > 1%, Anthropic spend > $50/day.

---

## 7. API specification

All endpoints versioned `/v1`. Authentication: API key in `x-api-key` header (v0.1).

### POST /v1/audits

Submit audit. Returns 201 with `{ job_id, status: PENDING, status_url }`.

### GET /v1/audits

List audits with optional `?status=` filter. Cursor-paginated.

### GET /v1/audits/{job_id}

Full audit detail with per-pass progress, findings count, Anthropic cost.

### GET /v1/audits/{job_id}/findings

List findings. Optional `?severity=CRITICAL&validated_only=true`.

### GET /v1/audits/{job_id}/findings/{finding_id}

Full finding with PoC source and formal verification result.

### GET /v1/audits/{job_id}/report

302 redirect to S3 signed URL (5 min TTL). `?format=json` for JSON report.

### POST /v1/audits/{job_id}/cancel

Cancel running audit. Returns 202 CANCELLING. Idempotent.

### DELETE /v1/audits/{job_id}

Soft-delete with 7-day S3 retention window.

### GET /v1/health

200 OK, no body.

---

## 8. Data model

### 8.1 Pydantic models

```python
from datetime import datetime
from typing import Literal
from pydantic import BaseModel, Field

Severity = Literal["CRITICAL", "HIGH", "MEDIUM", "LOW", "INFO"]
JobStatus = Literal["PENDING", "PROVISIONING", "RUNNING", "COMPLETED", "FAILED", "CANCELLED", "CANCELLING"]
PassStatus = Literal["PENDING", "RUNNING", "COMPLETED", "FAILED", "SKIPPED"]
Model = Literal["haiku", "sonnet", "opus"]

class AuditSubmit(BaseModel):
    repo: str = Field(..., pattern=r"^https://github\.com/.+\.git$")
    branch: str = Field(default="main")
    scope: list[str] = Field(default_factory=list)
    core_contracts: list[str] = Field(default_factory=list)
    model: Model = "haiku"

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
    report_anchor: str | None = None

class Audit(BaseModel):
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
    anthropic_cost_usd: float | None = None
```

### 8.2 DynamoDB item types

**Job metadata** — `PK=JOB#{id} SK=METADATA`
**Pass progress** — `PK=JOB#{id} SK=PASS#{n}`
**Finding** — `PK=JOB#{id} SK=FINDING#{n}`, GSI1PK=`SEVERITY#{sev}`

Access patterns: get job, list passes for job, list findings for job, list all criticals
across jobs (GSI1), list recent jobs by status (GSI2).

---

## 9. Operational concerns

Single CDK deploy command: `cdk deploy --all`

Stacks: network -> storage -> compute -> orchestration -> api -> observability

Three environments: dev (spot, no alarms), staging (prod mirror), prod (full SLOs).

RPO: 1 minute (DynamoDB PITR). RTO: 1 hour (CDK re-deploy).

---

## 10. Security model

Assets: customer source code, audit findings, Anthropic API key, per-tenant reports.

Key mitigations:
- T2 (compromised target repo): task role has no privileges outside its job prefix
- T3 (cross-job artefact access): `aws:RequestTag/JobId` IAM condition (see §6.8.2 in original RFC)
- T4 (API key leaked): secret masked in all logs; injected via Fargate secrets mechanism
- T5 (cross-tenant findings): all DynamoDB queries scoped by tenant_id from API key
- T8 (denial of wallet): daily token quota + spend alarm at $50/day

---

## 11. Failure modes and recovery

Key failure categories and responses:

| Failure | Exit code | Retry |
|---------|-----------|-------|
| Bulwark compile error | 1 (job-level) | No |
| Anthropic API down | 11 (transient) | Yes, 2x |
| S3 upload failure | 11 (transient) | Yes, 2x |
| Container OOM | 137 -> no retry | Alert ops |
| Task timeout (>2h) | Step Functions terminates | No |
| Target repo unreachable | 2 (job-level) | No |

Orphan recovery: EventBridge scans every 15 min for RUNNING jobs > 2h with no live task.

---

## 12. Cost model

### Per-audit cost (Haiku, Graph Horizon-scale)

| Component | Subtotal |
|-----------|----------|
| Fargate (4 vCPU / 16 GB, 50 min) | $1.99 |
| Anthropic Claude Haiku (~600k tokens) | ~$1.50 |
| NAT Gateway egress (~1 GB) | $0.045 |
| CloudWatch logs (100 MB) | $0.05 |
| S3, DynamoDB, Lambda, Step Functions | ~$0.01 |
| **Total (Haiku)** | **~$3.60** |

Same audit: Sonnet ~$22-27, Opus ~$85-125.

### Fixed monthly infrastructure

~$89/month (NAT Gateway $32, VPC endpoints $36, Lambda provisioned concurrency $7, misc $14).

### Projected monthly total

| Audits/month | Total (Haiku) |
|---|---|
| 10 | $125 |
| 100 | $449 |
| 1000 | $3,689 |

---

## 13. Implementation plan

**Phase 0 — Foundation (week 1, ~10 h)**: CDK scaffold, storage stack, network stack, LocalStack CI.
**Phase 1 — Single-pass MVP (week 2, ~15 h)**: entrypoint.py, Dockerfile, Pass 1 on Fargate.
**Phase 2 — Full pipeline (week 3, ~12 h)**: `bulwark run` end-to-end, IndexFindings Lambda.
**Phase 3 — API surface (week 4, ~10 h)**: FastAPI + API Gateway, all endpoints.
**Phase 4 — Observability (week 5, ~6 h)**: CloudWatch dashboards, alarms, cost tracking.
**Phase 5 — Hardening (week 6, ~8 h)**: IAM audit, failure dry-runs, docs, demo.

Critical path: network -> storage -> compute -> orchestration -> api/observability (parallel).

---

## 14. Alternatives considered

### 14.1 Rewriting Bulwark in Python
Rejected. 64 commits of working Rust; estimated 200+ hours to reproduce. Worse product under deadline.

### 14.2 Bulwark as long-running HTTP service
Rejected. CLI, not HTTP server; 45-min long-polling is anti-pattern; weaker isolation.

### 14.3 Lambda-only pipeline
Rejected. Pass 2 exceeds Lambda 15-min hard limit; 10 GB memory insufficient for Halmos.

### 14.4 AWS Batch
Rejected for v0.1. Overkill for 45-min jobs; Step Functions + ECS is simpler and more observable.

### 14.5 EKS
Rejected. $73/month minimum control plane; Fargate is the right tier at this scale.

### 14.6 SQS + EC2 workers
Rejected. Step Functions provides better workflow expression; EC2 fleet management overhead.

### 14.7 Synchronous API
Rejected. 45-minute HTTP requests fail at any load balancer; async + polling is industry standard.

---

## 15. Open questions

**Q1**: Should Bulwark expose machine-readable progress via `--progress-file`?
Tentative: yes, small Bulwark change, clean contract (v0.1).

**Q2**: File-upload of source code instead of git URL?
Tentative: v0.2 (adds significant API complexity).

**Q3**: Repeat audits of same repo+commit share results?
Tentative: no deduplication in v0.1.

**Q4**: RED/BLUE/GOLD agents as separate ECS tasks?
Tentative: no in v0.1; current in-container parallelism sufficient.

**Q5**: Default model Haiku or Sonnet?
Tentative: Haiku default; customers opt into Sonnet.

**Q6**: Auto-escalate to stronger model on zero findings?
Tentative: no — customer opts in; auto-escalation has misaligned incentives.

**Q7**: Region strategy?
Tentative: eu-central-1 in v0.1; multi-region in v0.2.

---

## 16. Future work

- Per-pass parallel ECS tasks for finer-grained scaling
- Cognito authentication for multi-tenant SaaS
- Billing integration (Stripe metered usage)
- GitHub App for automated audits on PRs
- Diff audits (findings introduced between two commits)
- Cross-region replication
- Webhook delivery for completion events
- Comparison view in dashboard
- Custom security skills uploaded by customers
- Findings deduplication across audits of same project
- Auto-PR creation with suggested fixes
- Halmos timeout auto-tuning based on contract complexity
- Audit findings explorer with full-text search (OpenSearch Serverless)
- VS Code extension

---

## 17. Appendices

### Appendix A — CDK stack skeleton

```python
# infra/app.py
import aws_cdk as cdk
from bulwark_cloud_infra.network_stack import NetworkStack
from bulwark_cloud_infra.storage_stack import StorageStack
from bulwark_cloud_infra.compute_stack import ComputeStack
from bulwark_cloud_infra.orchestration_stack import OrchestrationStack
from bulwark_cloud_infra.api_stack import ApiStack
from bulwark_cloud_infra.observability_stack import ObservabilityStack

app = cdk.App()
env = cdk.Environment(account="...", region="eu-central-1")

network = NetworkStack(app, "BulwarkCloudNetwork", env=env)
storage = StorageStack(app, "BulwarkCloudStorage", env=env)
compute = ComputeStack(app, "BulwarkCloudCompute", env=env,
    vpc=network.vpc, bucket=storage.bucket, table=storage.table, secrets=storage.secrets)
orch = OrchestrationStack(app, "BulwarkCloudOrchestration", env=env,
    cluster=compute.cluster, task_definition=compute.task_definition,
    index_lambda=compute.index_findings_lambda)
api = ApiStack(app, "BulwarkCloudApi", env=env,
    state_machine=orch.state_machine, table=storage.table, bucket=storage.bucket)
observability = ObservabilityStack(app, "BulwarkCloudObservability", env=env)

app.synth()
```

### Appendix B — Step Functions ASL JSON

See `infra/bulwark_cloud_infra/orchestration_stack.py` for the embedded ASL definition
used by the CDK construct.

### Appendix C — entrypoint.py skeleton

See `orchestrator/src/bulwark_cloud_orchestrator/entrypoint.py`.

### Appendix D — Repository structure

See [README.md](README.md).

### Appendix E — Glossary

| Term | Meaning |
|---|---|
| Bulwark | The existing Rust CLI audit pipeline (cargopete/bulwark) |
| bulwark-cloud | The platform proposed in this RFC |
| Pass | One of the 6 stages in Bulwark's audit pipeline |
| Finding | A vulnerability or issue identified during audit |
| PoC validation | Pass 3's gate — every finding must compile a working exploit |
| Halmos | Symbolic execution engine used in Pass 5 |
| Slither | Static analyser used in Pass 1 |
| Foundry / forge | Solidity build & test toolchain |
| CDK | AWS Cloud Development Kit, infrastructure as code in Python |
| GSI | DynamoDB Global Secondary Index |
| ULID | Universally Unique Lexicographically Sortable Identifier |
| ASL | Amazon States Language (Step Functions definition schema) |

---

## Sign-off

This RFC is open for review. Comments via PR or inline annotations welcome.

**Author**: Petko Pavlovski (`@cargopete`)
**Date**: 2026-05-27

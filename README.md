# bulwark-cloud

AWS-native platform for AI-augmented smart-contract auditing, built on top of [Bulwark](https://github.com/cargopete/bulwark).

## What it is

**Bulwark** proves the audit pipeline works. **bulwark-cloud** makes it a service.

Submit a contract repo via HTTPS, get a structured audit report with validated findings,
PoC exploits, fuzzing results, and formal verification — all asynchronously on AWS.

```
POST /v1/audits { repo, branch, scope }
→ { job_id, status: PENDING }

GET /v1/audits/{job_id}
→ { status: COMPLETED, passes: [...], findings_count: {CRITICAL: 1, ...} }

GET /v1/audits/{job_id}/report
→ 302 → S3 signed URL → final-report.md
```

## Architecture

```
API Gateway + Lambda (FastAPI)
    │
    └─ Step Functions state machine
           │
           └─ ECS Fargate task (bulwark-cloud image)
                  │
                  ├─ subprocess: bulwark run (6-pass pipeline)
                  ├─ uploads artefacts → S3
                  └─ writes state + findings → DynamoDB
```

Full design: see [RFC.md](RFC.md).

## Repository layout

```
infra/          CDK stacks (Python) — deploy the full AWS platform
orchestrator/   ECS task wrapper (Python) — runs inside the Fargate container
api/            FastAPI handler (Lambda) — public HTTPS API
lambdas/        Step Functions Lambdas (submit, index-findings, mark-failed)
shared/         Pydantic models shared across all components
docs/           Runbook, API reference, decision records
```

## Quick start

### Prerequisites

- AWS account with CDK bootstrapped (`cdk bootstrap`)
- `ANTHROPIC_API_KEY` stored in Secrets Manager as `bulwark-cloud/anthropic`
- `bulwark-cloud:v0.1.0` image pushed to ECR (see `orchestrator/Dockerfile`)
- Python 3.12, [uv](https://github.com/astral-sh/uv), AWS CDK v2

### Deploy

```bash
cd infra
uv sync
cdk deploy --all
```

### Submit an audit

```bash
curl -X POST https://{api-id}.execute-api.{region}.amazonaws.com/v1/audits \
  -H "x-api-key: YOUR_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "repo": "https://github.com/graphprotocol/contracts.git",
    "branch": "main",
    "scope": ["packages/horizon"],
    "model": "haiku"
  }'
```

## Development

```bash
# Install all workspace deps
uv sync

# Lint + type check
uv run ruff check .
uv run mypy .

# Tests
uv run pytest
```

## Cost

~$3.60 per audit at default Haiku model. See [RFC §12](RFC.md#12-cost-model) for the full breakdown.

## License

[MIT](LICENSE)

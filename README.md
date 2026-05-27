# bulwark-cloud

AWS-native platform for AI-augmented smart-contract auditing, built on top of [Bulwark](https://github.com/cargopete/bulwark).

## What it is

**Bulwark** runs the audit pipeline. **bulwark-cloud** makes it a service.

Submit a GitHub repo via HTTPS, get a structured audit report with validated findings,
PoC exploits, fuzzing results, and formal verification — all asynchronously on AWS.

```
POST /v1/audits { repo, branch, scope, model }
→ { job_id, status: PENDING }

GET /v1/audits/{job_id}
→ { status: COMPLETED, passes: [...], findings_count: {CRITICAL: 1, HIGH: 3} }

GET /v1/audits/{job_id}/report?format=md
→ { url: "https://s3.../signed-url", expires_in: 300 }

GET /v1/audits/{job_id}/findings
→ { items: [{ finding_id, title, severity, poc_validated, ... }] }
```

## Architecture

```
Browser (CloudFront + S3)
    │
    └─ API Gateway + Lambda (FastAPI/Mangum)
           │
           └─ Step Functions state machine
                  │
                  ├─ SubmitJob Lambda        (PENDING → PROVISIONING)
                  ├─ ECS Fargate task        (runs bulwark CLI, streams pass progress)
                  │      └─ S3               (artefacts + final-report.json)
                  │      └─ DynamoDB         (job state + pass records)
                  ├─ IndexFindings Lambda    (final-report.json → DynamoDB findings)
                  └─ MarkFailed Lambda       (on error branch)
```

Full design: [RFC.md](RFC.md).

## Repository layout

```
infra/          CDK stacks (7 stacks: Network, Storage, Compute, Orchestration, Api, Observability, Frontend)
orchestrator/   ECS task wrapper — Python process that drives the bulwark CLI
api/            FastAPI Lambda — public HTTPS API (audits, findings, reports)
lambdas/        Step Functions Lambdas (submit, index-findings, mark-failed)
shared/         Pydantic models shared across all components
frontend/       Single-page dashboard (vanilla HTML/JS, served via CloudFront)
scripts/        Operational scripts (get-api-key.sh, test-run.sh)
docs/           Runbook, API reference, ADRs
```

## Current status

| Milestone | Description | Status |
|-----------|-------------|--------|
| M1 | CDK synth passes; test suite green | **Done** |
| M2 | ECS infra, Dockerfile, CI deploy workflow | **Done** |
| M3 | Full 6-pass pipeline + API correctness | **Done** |
| M4 | Dashboard (CloudFront SPA), report endpoint, API key script | **Done** |
| M5 | Live end-to-end smoke test (`curl POST /v1/audits` → report in S3) | TODO |
| M6 | CloudWatch dashboards, alarms, cost tracking | TODO |

## Quick start

### Prerequisites

- AWS account with CDK bootstrapped (`cdk bootstrap`)
- Python 3.12, [uv](https://github.com/astral-sh/uv), AWS CDK v2, Docker (for bundling)
- GitHub Actions secrets: `AWS_ROLE_ARN`, `AWS_REGION`

### Deploy

```bash
cd infra
uv sync
cdk deploy --all
```

### Get your API key

```bash
./scripts/get-api-key.sh
# Prints: API Key Value, API URL, Dashboard URL
```

### Submit an audit via curl

```bash
API_URL="https://{api-id}.execute-api.{region}.amazonaws.com/v1"
API_KEY="your-key"

curl -X POST "$API_URL/audits" \
  -H "x-api-key: $API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "repo": "https://github.com/owner/repo",
    "branch": "main",
    "scope": ["src/"],
    "model": "haiku"
  }'
```

### Dashboard

Open the `DashboardUrl` CloudFormation output in your browser. Enter the API URL and
API key from `get-api-key.sh` in the Settings panel.

## Development

```bash
# Install all workspace deps
uv sync --all-packages

# Run tests
uv run pytest

# Lint
uv run ruff check .

# CDK synth (no AWS credentials needed)
cd infra && uv run cdk synth --quiet
```

## Cost

~$3.60 per audit at the default Haiku model. See [RFC §12](RFC.md#12-cost-model) for the
full breakdown.

## License

[MIT](LICENSE)

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
           └─ Step Functions state machine  ─┐
                  │                           └─ CloudWatch dashboards + alarms
                  ├─ SubmitJob Lambda        (PENDING → PROVISIONING)
                  ├─ ECS Fargate task        (generates per-target context, runs bulwark, streams progress)
                  │      └─ S3               (artefacts + final-report.json)
                  │      └─ DynamoDB         (job state + pass records)
                  ├─ IndexFindings Lambda    (final-report.json → DynamoDB findings)
                  └─ MarkFailed Lambda       (on error branch)
```

Bulwark expects a human to hand-write `context/` files (protocol overview, security
properties, known issues) before a run. Since bulwark-cloud audits arbitrary submitted
repos, the Fargate task generates these automatically: a headless Claude session reads
the in-scope contracts and writes target-specific `AUDIT_CONTEXT.md`, `PROPERTIES.md`,
and `KNOWN_ISSUES.md`, and the generated property IDs are wired into the formal pass.

Full design: [RFC.md](RFC.md). New to the codebase (or to Python/AWS)? Start with the
[field manual](docs/HOW-IT-WORKS.md) — a ground-up walkthrough of how bulwark and bulwark-cloud work.

## Repository layout

```
infra/          CDK stacks (6 stacks: Network, Storage, Compute, Api, Observability, Frontend)
orchestrator/   ECS task wrapper — Python process that drives the bulwark CLI
api/            FastAPI Lambda — public HTTPS API (audits, findings, reports)
lambdas/        Step Functions Lambdas (submit, index-findings, mark-failed)
shared/         Pydantic models shared across all components
frontend/       Single-page dashboard (vanilla HTML/JS, served via CloudFront)
scripts/        Operational scripts (get-api-key.sh, smoke-test.sh, test-run.sh)
docs/           Runbook, API reference, ADRs
```

## Current status

| Milestone | Description | Status |
|-----------|-------------|--------|
| M1 | CDK synth passes; test suite green | **Done** |
| M2 | ECS infra, Dockerfile, CI deploy workflow | **Done** |
| M3 | Full 6-pass pipeline + API correctness | **Done** |
| M4 | Dashboard (CloudFront SPA), report endpoint, API key script | **Done** |
| M5 | Live end-to-end smoke test (`curl POST /v1/audits` → report in S3) | **Done** |
| M6 | CloudWatch dashboards, alarms, cost tracking | **Done** |

## Quick start

### Prerequisites

- AWS account (free tier works)
- [uv](https://github.com/astral-sh/uv) and AWS CLI installed locally (for one-time bootstrap only)

### First-time bootstrap

Run once to create the GitHub OIDC provider, deploy IAM role, and CDK bootstrap stack:

```bash
./scripts/bootstrap-aws.sh <aws-account-id> eu-north-1
```

Then add these to GitHub → Settings → Secrets and variables → Actions:

| Type | Name | Value |
|------|------|-------|
| Secret | `AWS_DEPLOY_ROLE_ARN` | `arn:aws:iam::<account>:role/BulwarkCloudDeployRole` |
| Secret | `AWS_ACCOUNT_ID` | your 12-digit account ID |
| Variable | `AWS_REGION` | `eu-north-1` |

### Set your Anthropic API key

After the first deploy, store your key in Secrets Manager:

```bash
aws secretsmanager put-secret-value \
  --secret-id bulwark-cloud/anthropic \
  --secret-string "sk-ant-api03-..." \
  --region eu-north-1
```

### Deploy

Push to `main` — CI runs tests, builds the Docker image, and deploys all 6 stacks automatically.

### Get your API key

```bash
./scripts/get-api-key.sh
# Prints: API Key Value, API URL, Dashboard URL
```

### Run the smoke test

```bash
./scripts/smoke-test.sh
# Submits a real audit, polls to completion, verifies report + findings endpoints

# Or target any public repo:  smoke-test.sh <repo-url> <branch> <scope-json>
./scripts/smoke-test.sh https://github.com/yearn/tokenized-strategy master '["src/"]'
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

# CDK synth (requires Docker for Lambda layer bundling)
cd infra && uv run cdk synth --quiet
```

## Cost

~$3.60 per audit at the default Haiku model. See [RFC §12](RFC.md#12-cost-model) for the
full breakdown.

## License

[MIT](LICENSE)

# Roadmap

See [RFC BC-001](RFC.md) for the full design.

## v0.1 — Target: 4–6 weeks from 2026-05-27

| Phase | Description | Week | Status |
|-------|-------------|------|--------|
| 0 | Foundation: CDK scaffold, S3/DynamoDB/VPC stacks, LocalStack CI | 1 | TODO |
| 1 | Single-pass MVP: Pass 1 on Fargate, artefacts in S3 | 2 | TODO |
| 2 | Full 6-pass pipeline: `bulwark run` on Fargate, findings indexed | 3 | TODO |
| 3 | API surface: FastAPI + API Gateway, all endpoints live | 4 | TODO |
| 4 | Observability: CloudWatch dashboards, alarms, cost tracking | 5 | TODO |
| 5 | Hardening: IAM audit, failure dry-runs, docs, demo | 6 | TODO |

### Milestones

- **M1**: `cdk deploy --all` succeeds on a clean account
- **M2**: Pass 1 artefacts appear in S3 after a Fargate task run
- **M3**: Full audit produces `final-report.json` in S3
- **M4**: `curl POST /v1/audits` triggers an end-to-end audit
- **M5**: CloudWatch dashboard shows per-pass duration + cost
- **M6**: Threat model dry-run passes; demo video recorded

## v0.2 — Future

- Cognito authentication (multi-tenant SaaS)
- FARGATE_SPOT for non-urgent audits (70% compute cost reduction)
- GitHub App integration (automated audits on PRs)
- Diff audits (find findings introduced between two commits)
- Cross-region replication
- Webhook delivery for completion events
- Billing integration (Stripe metered usage)

Full future work list: [RFC §16](RFC.md#16-future-work).

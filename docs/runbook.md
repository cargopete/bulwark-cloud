# Operational Runbook

## Common procedures

### Check a stuck job

```bash
# Get job state from DynamoDB
aws dynamodb get-item \
  --table-name bulwark-cloud-state \
  --key '{"PK": {"S": "JOB#<job_id>"}, "SK": {"S": "METADATA"}}'

# Find the ECS task ARN and check its status
aws ecs describe-tasks \
  --cluster bulwark-cloud \
  --tasks <task_arn>

# Check Step Functions execution
aws stepfunctions describe-execution \
  --execution-arn arn:aws:states:eu-central-1:<account>:execution/bulwark-cloud-audit-pipeline/<job_id>
```

### Manually mark a job as failed

```bash
aws dynamodb update-item \
  --table-name bulwark-cloud-state \
  --key '{"PK": {"S": "JOB#<job_id>"}, "SK": {"S": "METADATA"}}' \
  --update-expression "SET #s = :s, failure_reason = :r, completed_at = :t, GSI2PK = :gpk" \
  --expression-attribute-names '{"#s": "status"}' \
  --expression-attribute-values '{
    ":s": {"S": "FAILED"},
    ":r": {"S": "MANUAL_INTERVENTION"},
    ":t": {"S": "'$(date -u +%Y-%m-%dT%H:%M:%SZ)'"},
    ":gpk": {"S": "STATUS#FAILED"}
  }'
```

### Tail live logs for a running job

```bash
aws logs tail /ecs/bulwark-cloud \
  --filter-pattern '{ $.job_id = "<job_id>" }' \
  --follow
```

### Download the final report

```bash
aws s3 cp s3://bulwark-cloud-artefacts-<account>-eu-central-1/<job_id>/report/final-report.md .
```

### Rotate the Anthropic API key

```bash
# Put the new key value
aws secretsmanager put-secret-value \
  --secret-id bulwark-cloud/anthropic \
  --secret-string "sk-ant-..."

# The change takes effect immediately for new tasks; in-flight tasks use the
# version they fetched at startup and are unaffected.
```

## Alarms and responses

| Alarm | Likely cause | Action |
|-------|-------------|--------|
| `audit-duration-p99` | Large contracts hitting Halmos timeout | Check Pass 5 duration; consider increasing `loop_bound` |
| `anthropic-daily-spend` | Unexpected audit volume or Sonnet/Opus usage | Review running jobs; check for runaway retries |
| Lambda errors > 1% | API Lambda regression | Check CloudWatch logs; rollback deployment if needed |
| Fargate task launch failures | Capacity limits or SG misconfiguration | Check ECS events; verify security group egress rules |

## Orphan job cleanup

EventBridge fires every 15 minutes. To manually trigger:

```bash
aws events put-events --entries '[{
  "Source": "bulwark-cloud.ops",
  "DetailType": "OrphanCheck",
  "Detail": "{}"
}]'
```

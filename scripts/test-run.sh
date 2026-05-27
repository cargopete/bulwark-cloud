#!/usr/bin/env bash
# Trigger a single audit run via Step Functions for M2 validation.
# Usage: ./scripts/test-run.sh <repo-url> [branch] [scope-json]
#
# Examples:
#   ./scripts/test-run.sh https://github.com/OpenZeppelin/openzeppelin-contracts
#   ./scripts/test-run.sh https://github.com/example/defi main '["contracts/"]'
#
# Prerequisites: AWS CLI configured, jq installed.

set -euo pipefail

REPO="${1:?Usage: $0 <repo-url> [branch] [scope-json]}"
BRANCH="${2:-main}"
SCOPE="${3:-'[\".\"]'}"

AWS_REGION="${AWS_REGION:-eu-north-1}"
STATE_MACHINE_NAME="bulwark-cloud-audit-pipeline"
DYNAMO_TABLE="bulwark-cloud-state"

# Generate a ULID-style job ID (timestamp prefix + random suffix)
JOB_ID="$(date +%s%N | head -c 10)$(cat /dev/urandom | LC_ALL=C tr -dc 'A-Z0-9' | head -c 16)"

echo "Starting audit run:"
echo "  Job ID : $JOB_ID"
echo "  Repo   : $REPO"
echo "  Branch : $BRANCH"
echo "  Scope  : $SCOPE"
echo ""

# Get state machine ARN
SM_ARN="$(aws stepfunctions list-state-machines \
    --region "$AWS_REGION" \
    --query "stateMachines[?name=='${STATE_MACHINE_NAME}'].stateMachineArn" \
    --output text)"

if [ -z "$SM_ARN" ]; then
    echo "ERROR: State machine '${STATE_MACHINE_NAME}' not found in ${AWS_REGION}."
    echo "Has CDK been deployed? Run: cd infra && cdk deploy --all"
    exit 1
fi

# Seed DynamoDB with PENDING status before starting the execution
aws dynamodb put-item \
    --region "$AWS_REGION" \
    --table-name "$DYNAMO_TABLE" \
    --item "{
        \"PK\": {\"S\": \"JOB#${JOB_ID}\"},
        \"SK\": {\"S\": \"METADATA\"},
        \"job_id\": {\"S\": \"${JOB_ID}\"},
        \"status\": {\"S\": \"PENDING\"},
        \"repo\": {\"S\": \"${REPO}\"},
        \"branch\": {\"S\": \"${BRANCH}\"},
        \"model\": {\"S\": \"haiku\"},
        \"created_at\": {\"S\": \"$(date -u +%Y-%m-%dT%H:%M:%SZ)\"},
        \"GSI2PK\": {\"S\": \"STATUS#PENDING\"},
        \"GSI2SK\": {\"S\": \"$(date -u +%Y-%m-%dT%H:%M:%SZ)#${JOB_ID}\"}
    }"

# Start the execution
EXECUTION_ARN="$(aws stepfunctions start-execution \
    --region "$AWS_REGION" \
    --state-machine-arn "$SM_ARN" \
    --name "test-${JOB_ID}" \
    --input "{
        \"job_id\": \"${JOB_ID}\",
        \"repo\": \"${REPO}\",
        \"branch\": \"${BRANCH}\",
        \"scope\": $SCOPE,
        \"model\": \"haiku\"
    }" \
    --query executionArn \
    --output text)"

echo "Execution started: $EXECUTION_ARN"
echo ""
echo "Monitor:"
echo "  aws stepfunctions describe-execution --execution-arn '$EXECUTION_ARN' --region $AWS_REGION"
echo "  aws logs tail /ecs/bulwark-cloud --follow --region $AWS_REGION"
echo ""
echo "Check artefacts (after completion):"
echo "  aws s3 ls s3://bulwark-cloud-artefacts-\$(aws sts get-caller-identity --query Account --output text)-$AWS_REGION/${JOB_ID}/ --region $AWS_REGION"

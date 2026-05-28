#!/usr/bin/env bash
# End-to-end smoke test for bulwark-cloud: POST /v1/audits → poll → report.
#
# Usage:
#   ./scripts/smoke-test.sh [repo-url] [branch] [scope-json]
#
# Defaults to a small public Solidity repo if no args given.
# Requires: AWS CLI configured, jq installed.

set -euo pipefail

REPO="${1:-https://github.com/OpenZeppelin/openzeppelin-contracts}"
BRANCH="${2:-master}"
SCOPE="${3:-[\"contracts/token/ERC20/\"]}"
MODEL="haiku"

REGION="${AWS_REGION:-eu-north-1}"
STACK="BulwarkCloudApi"
POLL_INTERVAL=30
TIMEOUT_SECONDS=2100  # 35 minutes — full 6-pass run

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

pass() { echo -e "${GREEN}PASS${NC} $*"; }
fail() { echo -e "${RED}FAIL${NC} $*"; exit 1; }
info() { echo -e "${YELLOW}    ${NC} $*"; }

echo "=== bulwark-cloud smoke test ==="
echo "  Repo  : $REPO"
echo "  Branch: $BRANCH"
echo "  Scope : $SCOPE"
echo "  Model : $MODEL"
echo ""

# ── Resolve API URL + key from CloudFormation ─────────────────────────────────

info "Fetching API URL from CloudFormation stack $STACK..."
API_URL=$(aws cloudformation describe-stacks \
    --stack-name "$STACK" \
    --region "$REGION" \
    --query "Stacks[0].Outputs[?OutputKey=='ApiUrl'].OutputValue" \
    --output text 2>/dev/null || true)

if [[ -z "$API_URL" || "$API_URL" == "None" ]]; then
    fail "Stack $STACK not found or not deployed. Run: cd infra && cdk deploy --all"
fi
# Strip trailing slash
API_URL="${API_URL%/}"
info "API URL: $API_URL"

info "Fetching API key..."
KEY_ID=$(aws cloudformation describe-stacks \
    --stack-name "$STACK" \
    --region "$REGION" \
    --query "Stacks[0].Outputs[?OutputKey=='ApiKeyId'].OutputValue" \
    --output text)
API_KEY=$(aws apigateway get-api-key \
    --api-key "$KEY_ID" \
    --include-value \
    --region "$REGION" \
    --query "value" \
    --output text)

# ── Health check ──────────────────────────────────────────────────────────────

info "Health check..."
HTTP_STATUS=$(curl -s -o /dev/null -w "%{http_code}" "${API_URL}/health" || true)
if [[ "$HTTP_STATUS" != "200" ]]; then
    fail "Health check returned HTTP $HTTP_STATUS (expected 200)"
fi
pass "Health check OK"

# ── Submit audit ──────────────────────────────────────────────────────────────

info "Submitting audit..."
SUBMIT_RESPONSE=$(curl -sf -X POST "${API_URL}/audits" \
    -H "x-api-key: $API_KEY" \
    -H "Content-Type: application/json" \
    -d "{
        \"repo\": \"$REPO\",
        \"branch\": \"$BRANCH\",
        \"scope\": $SCOPE,
        \"model\": \"$MODEL\"
    }")

JOB_ID=$(echo "$SUBMIT_RESPONSE" | jq -r '.job_id')
STATUS=$(echo "$SUBMIT_RESPONSE" | jq -r '.status')

if [[ -z "$JOB_ID" || "$JOB_ID" == "null" ]]; then
    fail "Submit failed — no job_id in response: $SUBMIT_RESPONSE"
fi
pass "Audit submitted — job_id=$JOB_ID status=$STATUS"

# ── Poll until terminal state ──────────────────────────────────────────────────

info "Polling (every ${POLL_INTERVAL}s, timeout ${TIMEOUT_SECONDS}s)..."
ELAPSED=0
FINAL_STATUS=""

while [[ $ELAPSED -lt $TIMEOUT_SECONDS ]]; do
    sleep "$POLL_INTERVAL"
    ELAPSED=$((ELAPSED + POLL_INTERVAL))

    POLL_RESPONSE=$(curl -sf "${API_URL}/audits/${JOB_ID}" \
        -H "x-api-key: $API_KEY" || true)
    CURRENT_STATUS=$(echo "$POLL_RESPONSE" | jq -r '.status // "UNKNOWN"')

    info "  [${ELAPSED}s] status=$CURRENT_STATUS"

    if [[ "$CURRENT_STATUS" == "COMPLETED" || "$CURRENT_STATUS" == "FAILED" || "$CURRENT_STATUS" == "CANCELLED" ]]; then
        FINAL_STATUS="$CURRENT_STATUS"
        break
    fi
done

if [[ -z "$FINAL_STATUS" ]]; then
    fail "Timed out after ${TIMEOUT_SECONDS}s — job $JOB_ID never reached a terminal state"
fi

if [[ "$FINAL_STATUS" != "COMPLETED" ]]; then
    DETAIL=$(echo "$POLL_RESPONSE" | jq -r '.failure_reason // .failure_detail // "no detail"')
    fail "Audit ended in $FINAL_STATUS — $DETAIL"
fi
pass "Audit completed in ${ELAPSED}s"

# ── Verify report endpoint ────────────────────────────────────────────────────

info "Fetching report URL..."
REPORT_RESPONSE=$(curl -sf "${API_URL}/audits/${JOB_ID}/report?format=json" \
    -H "x-api-key: $API_KEY")
REPORT_URL=$(echo "$REPORT_RESPONSE" | jq -r '.url // empty')

if [[ -z "$REPORT_URL" ]]; then
    fail "Report endpoint returned no URL: $REPORT_RESPONSE"
fi
pass "Report URL returned (signed S3 URL)"

# Verify the signed URL is actually reachable
HTTP_STATUS=$(curl -s -o /dev/null -w "%{http_code}" "$REPORT_URL" || true)
if [[ "$HTTP_STATUS" != "200" ]]; then
    fail "Signed report URL returned HTTP $HTTP_STATUS"
fi
pass "Report file accessible via signed URL"

# ── Verify findings endpoint ──────────────────────────────────────────────────

info "Fetching findings..."
FINDINGS_RESPONSE=$(curl -sf "${API_URL}/audits/${JOB_ID}/findings" \
    -H "x-api-key: $API_KEY")
FINDINGS_COUNT=$(echo "$FINDINGS_RESPONSE" | jq '.items | length')
pass "Findings endpoint OK — $FINDINGS_COUNT finding(s) indexed"

# ── Summary ───────────────────────────────────────────────────────────────────

echo ""
echo "=== SMOKE TEST PASSED ==="
echo "  Job ID  : $JOB_ID"
echo "  Duration: ${ELAPSED}s"
echo "  Findings: $FINDINGS_COUNT"
echo "  Report  : $REPORT_URL"

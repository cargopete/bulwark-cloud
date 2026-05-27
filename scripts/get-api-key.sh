#!/usr/bin/env bash
# Retrieve the bulwark-cloud API key value from a deployed stack.
#
# Usage:
#   ./scripts/get-api-key.sh [region]
#
# Examples:
#   ./scripts/get-api-key.sh
#   ./scripts/get-api-key.sh us-east-1
#
# The key ID is stored as a CloudFormation output (ApiKeyId) in BulwarkCloudApi.
# AWS credentials must be configured in the environment before running.
set -euo pipefail

REGION="${1:-${AWS_REGION:-eu-north-1}}"
STACK="BulwarkCloudApi"

echo "Region: $REGION"
echo "Stack:  $STACK"
echo ""

KEY_ID=$(aws cloudformation describe-stacks \
    --stack-name "$STACK" \
    --region "$REGION" \
    --query "Stacks[0].Outputs[?OutputKey=='ApiKeyId'].OutputValue" \
    --output text 2>/dev/null)

if [[ -z "$KEY_ID" || "$KEY_ID" == "None" ]]; then
    echo "Error: ApiKeyId output not found in stack $STACK." >&2
    echo "       Has the stack been deployed? Try: cd infra && cdk deploy BulwarkCloudApi" >&2
    exit 1
fi

echo "API Key ID:    $KEY_ID"
echo -n "API Key Value: "
aws apigateway get-api-key \
    --api-key "$KEY_ID" \
    --include-value \
    --region "$REGION" \
    --query "value" \
    --output text

echo ""

API_URL=$(aws cloudformation describe-stacks \
    --stack-name "$STACK" \
    --region "$REGION" \
    --query "Stacks[0].Outputs[?OutputKey=='ApiUrl'].OutputValue" \
    --output text 2>/dev/null || echo "")

if [[ -n "$API_URL" && "$API_URL" != "None" ]]; then
    echo "API URL: $API_URL"
fi

DASHBOARD=$(aws cloudformation describe-stacks \
    --stack-name "BulwarkCloudFrontend" \
    --region "$REGION" \
    --query "Stacks[0].Outputs[?OutputKey=='DashboardUrl'].OutputValue" \
    --output text 2>/dev/null || echo "")

if [[ -n "$DASHBOARD" && "$DASHBOARD" != "None" ]]; then
    echo "Dashboard:   $DASHBOARD"
fi

#!/usr/bin/env bash
# One-time AWS bootstrap for bulwark-cloud.
#
# Creates:
#   - GitHub OIDC identity provider
#   - IAM deploy role (assumed by GitHub Actions)
#   - CDK bootstrap stack
#
# Usage:
#   ./scripts/bootstrap-aws.sh <aws-account-id> [region]
#
# Example:
#   ./scripts/bootstrap-aws.sh 123456789012 eu-central-1
#
# Requires: aws CLI, AWS credentials with AdministratorAccess
set -euo pipefail

ACCOUNT="${1:?Usage: $0 <aws-account-id> [region]}"
REGION="${2:-eu-central-1}"
REPO="cargopete/bulwark-cloud"
ROLE_NAME="BulwarkCloudDeployRole"
OIDC_URL="https://token.actions.githubusercontent.com"
OIDC_AUDIENCE="sts.amazonaws.com"

echo "==> Account : $ACCOUNT"
echo "==> Region  : $REGION"
echo "==> Repo    : $REPO"
echo ""

# ── 1. GitHub OIDC provider ────────────────────────────────────────────────
OIDC_ARN="arn:aws:iam::${ACCOUNT}:oidc-provider/token.actions.githubusercontent.com"

if aws iam get-open-id-connect-provider --open-id-connect-provider-arn "$OIDC_ARN" \
       --region "$REGION" &>/dev/null; then
    echo "[1/3] OIDC provider already exists — skipping"
else
    echo "[1/3] Creating GitHub OIDC provider..."
    # Fetch GitHub's thumbprint
    THUMBPRINT=$(echo | openssl s_client -servername token.actions.githubusercontent.com \
        -connect token.actions.githubusercontent.com:443 2>/dev/null \
        | openssl x509 -fingerprint -noout -sha1 \
        | sed 's/.*=//' | tr -d ':' | tr '[:upper:]' '[:lower:]')

    aws iam create-open-id-connect-provider \
        --url "$OIDC_URL" \
        --client-id-list "$OIDC_AUDIENCE" \
        --thumbprint-list "$THUMBPRINT" \
        --region "$REGION" \
        --query "OpenIDConnectProviderArn" --output text
    echo "    Done."
fi

# ── 2. IAM deploy role ─────────────────────────────────────────────────────
ROLE_ARN="arn:aws:iam::${ACCOUNT}:role/${ROLE_NAME}"

TRUST_POLICY=$(cat <<EOF
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Principal": {
        "Federated": "arn:aws:iam::${ACCOUNT}:oidc-provider/token.actions.githubusercontent.com"
      },
      "Action": "sts:AssumeRoleWithWebIdentity",
      "Condition": {
        "StringLike": {
          "token.actions.githubusercontent.com:sub": "repo:${REPO}:*"
        },
        "StringEquals": {
          "token.actions.githubusercontent.com:aud": "${OIDC_AUDIENCE}"
        }
      }
    }
  ]
}
EOF
)

if aws iam get-role --role-name "$ROLE_NAME" --region "$REGION" &>/dev/null; then
    echo "[2/3] Deploy role already exists — skipping"
else
    echo "[2/3] Creating IAM deploy role..."
    aws iam create-role \
        --role-name "$ROLE_NAME" \
        --assume-role-policy-document "$TRUST_POLICY" \
        --description "Assumed by GitHub Actions to deploy bulwark-cloud" \
        --region "$REGION" \
        --query "Role.Arn" --output text

    aws iam attach-role-policy \
        --role-name "$ROLE_NAME" \
        --policy-arn "arn:aws:policies/AdministratorAccess" \
        --region "$REGION" 2>/dev/null || \
    aws iam attach-role-policy \
        --role-name "$ROLE_NAME" \
        --policy-arn "arn:aws:iam::aws:policy/AdministratorAccess"

    echo "    Done. Role ARN: $ROLE_ARN"
fi

# ── 3. CDK bootstrap ───────────────────────────────────────────────────────
echo "[3/3] Running CDK bootstrap..."
cd "$(dirname "$0")/../infra"
AWS_ACCOUNT_ID="$ACCOUNT" CDK_DEFAULT_ACCOUNT="$ACCOUNT" CDK_DEFAULT_REGION="$REGION" \
    uv run cdk bootstrap "aws://${ACCOUNT}/${REGION}" \
    --cloudformation-execution-policies "arn:aws:iam::aws:policy/AdministratorAccess" \
    --trust "$ROLE_ARN"
echo "    Done."

echo ""
echo "============================================================"
echo " Bootstrap complete!"
echo "============================================================"
echo ""
echo " Now add these secrets to GitHub:"
echo "   https://github.com/${REPO}/settings/secrets/actions"
echo ""
echo "   AWS_DEPLOY_ROLE_ARN  =  ${ROLE_ARN}"
echo "   AWS_ACCOUNT_ID       =  ${ACCOUNT}"
echo "   AWS_REGION           =  ${REGION}"
echo ""
echo " Then push to main to trigger the first deploy."
echo ""

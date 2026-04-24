#!/usr/bin/env bash
set -euo pipefail

APP_NAME="${APP_NAME:-video-script-creator}"
AWS_REGION="${AWS_REGION:-${AWS_DEFAULT_REGION:-eu-central-1}}"
CLUSTER_NAME="${CLUSTER_NAME:-${APP_NAME}-cluster}"
SERVICE_NAME="${SERVICE_NAME:-$APP_NAME}"
LOG_GROUP="${LOG_GROUP:-/ecs/${APP_NAME}}"
ALB_NAME="${ALB_NAME:-${APP_NAME}-alb}"
SINCE="${SINCE:-30m}"

export AWS_PAGER=""

ALB_DNS="$(aws elbv2 describe-load-balancers \
  --region "$AWS_REGION" \
  --names "$ALB_NAME" \
  --query "LoadBalancers[0].DNSName" \
  --output text)"

aws ecs describe-services \
  --region "$AWS_REGION" \
  --cluster "$CLUSTER_NAME" \
  --services "$SERVICE_NAME" \
  --query "services[0].{Status:status,Running:runningCount,Desired:desiredCount,Events:events[0:5].[createdAt,message]}" \
  --output table

echo
echo "Health check:"
curl -fsS "http://${ALB_DNS}/_stcore/health" || true
echo

echo
echo "Recent logs (${SINCE}):"
aws logs tail "$LOG_GROUP" \
  --region "$AWS_REGION" \
  --since "$SINCE"

echo
echo "Open: http://${ALB_DNS}"

#!/usr/bin/env bash
set -euo pipefail

APP_NAME="${APP_NAME:-video-script-creator}"
AWS_REGION="${AWS_REGION:-${AWS_DEFAULT_REGION:-eu-central-1}}"
SINCE="${SINCE:-30m}"

export AWS_PAGER=""

echo "Checking App Runner service: ${APP_NAME} in ${AWS_REGION}"

SERVICE_ARN="$(aws apprunner list-services \
  --region "$AWS_REGION" \
  --query "ServiceSummaryList[?ServiceName=='${APP_NAME}'].ServiceArn | [0]" \
  --output text)"

if [[ -z "$SERVICE_ARN" || "$SERVICE_ARN" == "None" ]]; then
  echo "No App Runner service found named ${APP_NAME} in ${AWS_REGION}" >&2
  exit 1
fi

SERVICE_ID="$(echo "$SERVICE_ARN" | awk -F/ '{print $NF}')"

echo
echo "Service ARN: ${SERVICE_ARN}"
echo "Service ID:  ${SERVICE_ID}"
echo

aws apprunner describe-service \
  --region "$AWS_REGION" \
  --service-arn "$SERVICE_ARN" \
  --query "Service.{Status:Status,Url:ServiceUrl,UpdatedAt:UpdatedAt}" \
  --output table

SERVICE_URL="$(aws apprunner describe-service \
  --region "$AWS_REGION" \
  --service-arn "$SERVICE_ARN" \
  --query "Service.ServiceUrl" \
  --output text)"

echo
echo "Health check:"
if curl -fsS "https://${SERVICE_URL}/_stcore/health"; then
  echo
else
  echo "Health check failed" >&2
fi

echo
echo "Log groups:"
aws logs describe-log-groups \
  --region "$AWS_REGION" \
  --log-group-name-prefix "/aws/apprunner/${APP_NAME}/${SERVICE_ID}" \
  --query "logGroups[].logGroupName" \
  --output table || true

echo
echo "Recent application logs (${SINCE}):"
APP_LOG_GROUP="/aws/apprunner/${APP_NAME}/${SERVICE_ID}/application"
if aws logs describe-log-groups \
  --region "$AWS_REGION" \
  --log-group-name-prefix "$APP_LOG_GROUP" \
  --query "logGroups[0].logGroupName" \
  --output text | grep -q "$APP_LOG_GROUP"; then
  aws logs tail "$APP_LOG_GROUP" \
    --region "$AWS_REGION" \
    --since "$SINCE"
else
  echo "Application log group not found: ${APP_LOG_GROUP}"
fi

echo
echo "Recent service logs (${SINCE}):"
SERVICE_LOG_GROUP="/aws/apprunner/${APP_NAME}/${SERVICE_ID}/service"
if aws logs describe-log-groups \
  --region "$AWS_REGION" \
  --log-group-name-prefix "$SERVICE_LOG_GROUP" \
  --query "logGroups[0].logGroupName" \
  --output text | grep -q "$SERVICE_LOG_GROUP"; then
  aws logs tail "$SERVICE_LOG_GROUP" \
    --region "$AWS_REGION" \
    --since "$SINCE"
else
  echo "Service log group not found: ${SERVICE_LOG_GROUP}"
fi

echo
echo "Open: https://${SERVICE_URL}"

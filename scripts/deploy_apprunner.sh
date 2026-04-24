#!/usr/bin/env bash
set -euo pipefail

APP_NAME="${APP_NAME:-video-script-creator}"
AWS_REGION="${AWS_REGION:-${AWS_DEFAULT_REGION:-eu-central-1}}"
BEDROCK_AWS_REGION="${BEDROCK_AWS_REGION:-$AWS_REGION}"
BEDROCK_MODEL_ID="${BEDROCK_MODEL_ID:-eu.amazon.nova-pro-v1:0}"
BEDROCK_MAX_TOKENS="${BEDROCK_MAX_TOKENS:-4096}"
STORAGE_BACKEND="${STORAGE_BACKEND:-local}"
S3_BUCKET="${S3_BUCKET:-}"
S3_PREFIX="${S3_PREFIX:-runtime}"

ACCOUNT_ID="$(aws sts get-caller-identity --query Account --output text)"
ECR_REPO="${ECR_REPO:-$APP_NAME}"
DEFAULT_IMAGE_TAG="$(git rev-parse --short HEAD 2>/dev/null || date +%Y%m%d%H%M%S)"
IMAGE_TAG="${IMAGE_TAG:-$DEFAULT_IMAGE_TAG}"
IMAGE_URI="${ACCOUNT_ID}.dkr.ecr.${AWS_REGION}.amazonaws.com/${ECR_REPO}:${IMAGE_TAG}"
APPRUNNER_ROLE_NAME="${APPRUNNER_ROLE_NAME:-${APP_NAME}-apprunner-ecr-access}"
APPRUNNER_INSTANCE_ROLE_NAME="${APPRUNNER_INSTANCE_ROLE_NAME:-${APP_NAME}-apprunner-instance}"

echo "Deploying ${APP_NAME} to ${AWS_REGION}"
echo "Image: ${IMAGE_URI}"

aws ecr describe-repositories --repository-names "$ECR_REPO" --region "$AWS_REGION" >/dev/null 2>&1 \
  || aws ecr create-repository --repository-name "$ECR_REPO" --region "$AWS_REGION" >/dev/null

aws ecr get-login-password --region "$AWS_REGION" \
  | docker login --username AWS --password-stdin "${ACCOUNT_ID}.dkr.ecr.${AWS_REGION}.amazonaws.com"

docker build -t "${APP_NAME}:${IMAGE_TAG}" .
docker tag "${APP_NAME}:${IMAGE_TAG}" "$IMAGE_URI"
docker push "$IMAGE_URI"

if ! aws iam get-role --role-name "$APPRUNNER_ROLE_NAME" >/dev/null 2>&1; then
  aws iam create-role \
    --role-name "$APPRUNNER_ROLE_NAME" \
    --assume-role-policy-document '{
      "Version": "2012-10-17",
      "Statement": [{
        "Effect": "Allow",
        "Principal": {"Service": "build.apprunner.amazonaws.com"},
        "Action": "sts:AssumeRole"
      }]
    }' >/dev/null
  aws iam attach-role-policy \
    --role-name "$APPRUNNER_ROLE_NAME" \
    --policy-arn arn:aws:iam::aws:policy/service-role/AWSAppRunnerServicePolicyForECRAccess
fi

if ! aws iam get-role --role-name "$APPRUNNER_INSTANCE_ROLE_NAME" >/dev/null 2>&1; then
  aws iam create-role \
    --role-name "$APPRUNNER_INSTANCE_ROLE_NAME" \
    --assume-role-policy-document '{
      "Version": "2012-10-17",
      "Statement": [{
        "Effect": "Allow",
        "Principal": {"Service": "tasks.apprunner.amazonaws.com"},
        "Action": "sts:AssumeRole"
      }]
    }' >/dev/null
fi

POLICY_DOC="$(mktemp)"
cat > "$POLICY_DOC" <<JSON
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": [
        "bedrock:InvokeModel",
        "bedrock:InvokeModelWithResponseStream"
      ],
      "Resource": "*"
    }
  ]
}
JSON

if [[ "$STORAGE_BACKEND" == "s3" ]]; then
  if [[ -z "$S3_BUCKET" ]]; then
    echo "S3_BUCKET is required when STORAGE_BACKEND=s3" >&2
    exit 1
  fi
  if ! aws s3api head-bucket --bucket "$S3_BUCKET" >/dev/null 2>&1; then
    aws s3api create-bucket \
      --bucket "$S3_BUCKET" \
      --region "$AWS_REGION" \
      --create-bucket-configuration LocationConstraint="$AWS_REGION" >/dev/null
  fi
  python3 - "$POLICY_DOC" "$S3_BUCKET" <<'PY'
import json, sys
path, bucket = sys.argv[1], sys.argv[2]
doc = json.load(open(path))
doc["Statement"].extend([
    {
        "Effect": "Allow",
        "Action": ["s3:GetObject", "s3:PutObject", "s3:DeleteObject"],
        "Resource": f"arn:aws:s3:::{bucket}/*",
    },
    {
        "Effect": "Allow",
        "Action": ["s3:ListBucket"],
        "Resource": f"arn:aws:s3:::{bucket}",
    },
])
json.dump(doc, open(path, "w"))
PY
fi

aws iam put-role-policy \
  --role-name "$APPRUNNER_INSTANCE_ROLE_NAME" \
  --policy-name "${APP_NAME}-runtime-policy" \
  --policy-document "file://${POLICY_DOC}"

ACCESS_ROLE_ARN="$(aws iam get-role --role-name "$APPRUNNER_ROLE_NAME" --query 'Role.Arn' --output text)"
INSTANCE_ROLE_ARN="$(aws iam get-role --role-name "$APPRUNNER_INSTANCE_ROLE_NAME" --query 'Role.Arn' --output text)"

SERVICE_ARN="$(aws apprunner list-services \
  --region "$AWS_REGION" \
  --query "ServiceSummaryList[?ServiceName=='${APP_NAME}'].ServiceArn | [0]" \
  --output text)"

ENV_JSON="$(mktemp)"
python3 - "$ENV_JSON" <<PY
import json, os, sys
env = {
    "BEDROCK_AWS_REGION": os.environ.get("BEDROCK_AWS_REGION", "${BEDROCK_AWS_REGION}"),
    "BEDROCK_MODEL_ID": os.environ.get("BEDROCK_MODEL_ID", "${BEDROCK_MODEL_ID}"),
    "BEDROCK_MAX_TOKENS": os.environ.get("BEDROCK_MAX_TOKENS", "${BEDROCK_MAX_TOKENS}"),
    "APP_DATA_DIR": "/app/data",
    "STORAGE_BACKEND": os.environ.get("STORAGE_BACKEND", "${STORAGE_BACKEND}"),
}
if os.environ.get("S3_BUCKET", "${S3_BUCKET}"):
    env["S3_BUCKET"] = os.environ.get("S3_BUCKET", "${S3_BUCKET}")
if os.environ.get("S3_PREFIX", "${S3_PREFIX}"):
    env["S3_PREFIX"] = os.environ.get("S3_PREFIX", "${S3_PREFIX}")
json.dump(env, open(sys.argv[1], "w"))
PY

if [[ "$SERVICE_ARN" == "None" || -z "$SERVICE_ARN" ]]; then
  aws apprunner create-service \
    --region "$AWS_REGION" \
    --service-name "$APP_NAME" \
    --source-configuration "{
      \"ImageRepository\": {
        \"ImageIdentifier\": \"${IMAGE_URI}\",
        \"ImageRepositoryType\": \"ECR\",
        \"ImageConfiguration\": {
          \"Port\": \"8501\",
          \"RuntimeEnvironmentVariables\": $(cat "$ENV_JSON")
        }
      },
      \"AutoDeploymentsEnabled\": false,
      \"AuthenticationConfiguration\": {
        \"AccessRoleArn\": \"${ACCESS_ROLE_ARN}\"
      }
    }" \
    --instance-configuration "{
      \"Cpu\": \"1 vCPU\",
      \"Memory\": \"2 GB\",
      \"InstanceRoleArn\": \"${INSTANCE_ROLE_ARN}\"
    }"
else
  aws apprunner update-service \
    --region "$AWS_REGION" \
    --service-arn "$SERVICE_ARN" \
    --source-configuration "{
      \"ImageRepository\": {
        \"ImageIdentifier\": \"${IMAGE_URI}\",
        \"ImageRepositoryType\": \"ECR\",
        \"ImageConfiguration\": {
          \"Port\": \"8501\",
          \"RuntimeEnvironmentVariables\": $(cat "$ENV_JSON")
        }
      },
      \"AutoDeploymentsEnabled\": false,
      \"AuthenticationConfiguration\": {
        \"AccessRoleArn\": \"${ACCESS_ROLE_ARN}\"
      }
    }" \
    --instance-configuration "{
      \"Cpu\": \"1 vCPU\",
      \"Memory\": \"2 GB\",
      \"InstanceRoleArn\": \"${INSTANCE_ROLE_ARN}\"
    }"
fi

echo "Waiting for App Runner service..."
sleep 15
aws apprunner list-services \
  --region "$AWS_REGION" \
  --query "ServiceSummaryList[?ServiceName=='${APP_NAME}'].[ServiceArn,Status,ServiceUrl]" \
  --output table

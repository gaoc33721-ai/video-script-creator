#!/usr/bin/env bash
set -euo pipefail

APP_NAME="${APP_NAME:-video-script-creator}"
APP_RUNTIME="${APP_RUNTIME:-api}"
APP_BASE_PATH="${APP_BASE_PATH:-}"
if [[ -n "$APP_BASE_PATH" && "$APP_BASE_PATH" != /* ]]; then
  APP_BASE_PATH="/${APP_BASE_PATH}"
fi
APP_BASE_PATH="${APP_BASE_PATH%/}"
AWS_REGION="${AWS_REGION:-${AWS_DEFAULT_REGION:-eu-central-1}}"
BEDROCK_AWS_REGION="${BEDROCK_AWS_REGION:-$AWS_REGION}"
BEDROCK_MODEL_ID="${BEDROCK_MODEL_ID:-eu.anthropic.claude-sonnet-4-5-20250929-v1:0}"
BEDROCK_MODEL_FALLBACK_IDS="${BEDROCK_MODEL_FALLBACK_IDS:-eu.amazon.nova-pro-v1:0}"
BEDROCK_MAX_TOKENS="${BEDROCK_MAX_TOKENS:-8192}"
BEDROCK_FALLBACK_MAX_TOKENS="${BEDROCK_FALLBACK_MAX_TOKENS:-4096}"
NOVA_REEL_AWS_REGION="${NOVA_REEL_AWS_REGION:-us-east-1}"
NOVA_REEL_MODEL_ID="${NOVA_REEL_MODEL_ID:-amazon.nova-reel-v1:1}"
NOVA_REEL_OUTPUT_S3_URI="${NOVA_REEL_OUTPUT_S3_URI:-}"
NOVA_REEL_ESTIMATED_USD_PER_SECOND="${NOVA_REEL_ESTIMATED_USD_PER_SECOND:-0.08}"
NOVA_REEL_MAX_SUBMISSIONS_PER_CLICK="${NOVA_REEL_MAX_SUBMISSIONS_PER_CLICK:-2}"
NOVA_CANVAS_AWS_REGION="${NOVA_CANVAS_AWS_REGION:-us-west-2}"
NOVA_CANVAS_MODEL_ID="${NOVA_CANVAS_MODEL_ID:-stability.sd3-5-large-v1:0}"
NOVA_CANVAS_ESTIMATED_USD_PER_IMAGE="${NOVA_CANVAS_ESTIMATED_USD_PER_IMAGE:-0.08}"
NOVA_CANVAS_REFERENCE_STRENGTH="${NOVA_CANVAS_REFERENCE_STRENGTH:-0.9}"
MEDIA_IMAGE_PROVIDER="${MEDIA_IMAGE_PROVIDER:-nova_canvas}"
LIBLIBAI_BASE_URL="${LIBLIBAI_BASE_URL:-https://openapi.liblibai.cloud}"
LIBLIBAI_TEMPLATE_UUID="${LIBLIBAI_TEMPLATE_UUID:-5d7e67009b344550bc1aa6ccbfa1d7f4}"
LIBLIBAI_IMAGE_MODEL_LABEL="${LIBLIBAI_IMAGE_MODEL_LABEL:-liblibai:star-3-alpha}"
LIBLIBAI_IMAGE_ASPECT_RATIO="${LIBLIBAI_IMAGE_ASPECT_RATIO:-landscape}"
LIBLIBAI_IMAGE_WIDTH="${LIBLIBAI_IMAGE_WIDTH:-1280}"
LIBLIBAI_IMAGE_HEIGHT="${LIBLIBAI_IMAGE_HEIGHT:-720}"
LIBLIBAI_IMAGE_SIZE_ENABLED="${LIBLIBAI_IMAGE_SIZE_ENABLED:-false}"
LIBLIBAI_IMAGE_STEPS="${LIBLIBAI_IMAGE_STEPS:-20}"
LIBLIBAI_IMAGE_COUNT="${LIBLIBAI_IMAGE_COUNT:-1}"
LIBLIBAI_REQUEST_TIMEOUT="${LIBLIBAI_REQUEST_TIMEOUT:-90}"
LIBLIBAI_POLL_TIMEOUT="${LIBLIBAI_POLL_TIMEOUT:-240}"
LIBLIBAI_POLL_INTERVAL="${LIBLIBAI_POLL_INTERVAL:-3}"
LIBLIBAI_MAX_PROMPT_LENGTH="${LIBLIBAI_MAX_PROMPT_LENGTH:-1800}"
LIBLIBAI_REFERENCE_CONTROL_TYPE="${LIBLIBAI_REFERENCE_CONTROL_TYPE:-depth}"
LIBLIBAI_ACCESS_KEY="${LIBLIBAI_ACCESS_KEY:-}"
LIBLIBAI_ACCESS_KEY_SECRET_NAME="${LIBLIBAI_ACCESS_KEY_SECRET_NAME:-${APP_NAME}/liblibai-access-key}"
LIBLIBAI_ACCESS_KEY_SECRET_ARN="${LIBLIBAI_ACCESS_KEY_SECRET_ARN:-}"
LIBLIBAI_SECRET_KEY="${LIBLIBAI_SECRET_KEY:-}"
LIBLIBAI_SECRET_KEY_SECRET_NAME="${LIBLIBAI_SECRET_KEY_SECRET_NAME:-${APP_NAME}/liblibai-secret-key}"
LIBLIBAI_SECRET_KEY_SECRET_ARN="${LIBLIBAI_SECRET_KEY_SECRET_ARN:-}"
RAINFOREST_API_KEY="${RAINFOREST_API_KEY:-}"
RAINFOREST_API_KEY_SECRET_NAME="${RAINFOREST_API_KEY_SECRET_NAME:-${APP_NAME}/rainforest-api-key}"
RAINFOREST_API_KEY_SECRET_ARN="${RAINFOREST_API_KEY_SECRET_ARN:-}"
RAINFOREST_DEFAULT_AMAZON_DOMAIN="${RAINFOREST_DEFAULT_AMAZON_DOMAIN:-amazon.com}"
RAINFOREST_SEARCH_TOP_N="${RAINFOREST_SEARCH_TOP_N:-8}"
RAINFOREST_DISCOVERY_REQUEST_LIMIT="${RAINFOREST_DISCOVERY_REQUEST_LIMIT:-6}"
RAINFOREST_MAX_PRODUCTS_PER_REFRESH="${RAINFOREST_MAX_PRODUCTS_PER_REFRESH:-30}"
YOUTUBE_API_KEY="${YOUTUBE_API_KEY:-}"
YOUTUBE_API_KEY_SECRET_NAME="${YOUTUBE_API_KEY_SECRET_NAME:-${APP_NAME}/youtube-api-key}"
YOUTUBE_API_KEY_SECRET_ARN="${YOUTUBE_API_KEY_SECRET_ARN:-}"
YOUTUBE_DISCOVERY_TOP_N="${YOUTUBE_DISCOVERY_TOP_N:-8}"
YOUTUBE_DISCOVERY_REQUEST_LIMIT="${YOUTUBE_DISCOVERY_REQUEST_LIMIT:-4}"
SOCIAL_OEMBED_ACCESS_TOKEN="${SOCIAL_OEMBED_ACCESS_TOKEN:-}"
SOCIAL_OEMBED_ACCESS_TOKEN_SECRET_NAME="${SOCIAL_OEMBED_ACCESS_TOKEN_SECRET_NAME:-${APP_NAME}/social-oembed-token}"
SOCIAL_OEMBED_ACCESS_TOKEN_SECRET_ARN="${SOCIAL_OEMBED_ACCESS_TOKEN_SECRET_ARN:-}"
SOCIAL_REQUEST_TIMEOUT="${SOCIAL_REQUEST_TIMEOUT:-15}"
STORAGE_BACKEND="${STORAGE_BACKEND:-local}"
S3_BUCKET="${S3_BUCKET:-}"
S3_PREFIX="${S3_PREFIX:-runtime}"
APP_ACCESS_PASSWORD="${APP_ACCESS_PASSWORD:-}"
APP_ACCESS_PASSWORD_SECRET_NAME="${APP_ACCESS_PASSWORD_SECRET_NAME:-${APP_NAME}/app-access-password}"
APP_ACCESS_PASSWORD_SECRET_ARN="${APP_ACCESS_PASSWORD_SECRET_ARN:-}"
APP_ACCESS_PASSWORD_CACHE_TTL="${APP_ACCESS_PASSWORD_CACHE_TTL:-300}"
DATABASE_URL_SECRET_ARN="${DATABASE_URL_SECRET_ARN:-}"
ALLOWED_HTTP_CIDRS="${ALLOWED_HTTP_CIDRS:-}"
CONTAINER_PORT="${CONTAINER_PORT:-8501}"
DESIRED_COUNT="${DESIRED_COUNT:-1}"
if [[ "$APP_RUNTIME" == "api" ]]; then
  DEFAULT_HEALTH_CHECK_PATH="${APP_BASE_PATH}/healthz"
else
  DEFAULT_HEALTH_CHECK_PATH="/_stcore/health"
fi
HEALTH_CHECK_PATH="${HEALTH_CHECK_PATH:-$DEFAULT_HEALTH_CHECK_PATH}"

export AWS_PAGER=""

ACCOUNT_ID="$(aws sts get-caller-identity --query Account --output text)"
ECR_REPO="${ECR_REPO:-$APP_NAME}"
DEFAULT_IMAGE_TAG="$(git rev-parse --short HEAD 2>/dev/null || date +%Y%m%d%H%M%S)"
IMAGE_TAG="${IMAGE_TAG:-$DEFAULT_IMAGE_TAG}"
IMAGE_URI="${ACCOUNT_ID}.dkr.ecr.${AWS_REGION}.amazonaws.com/${ECR_REPO}:${IMAGE_TAG}"

CLUSTER_NAME="${CLUSTER_NAME:-${APP_NAME}-cluster}"
TASK_EXEC_ROLE="${TASK_EXEC_ROLE:-${APP_NAME}-ecs-task-execution}"
TASK_ROLE="${TASK_ROLE:-${APP_NAME}-ecs-task}"
TASK_FAMILY="${TASK_FAMILY:-$APP_NAME}"
SERVICE_NAME="${SERVICE_NAME:-$APP_NAME}"
LOG_GROUP="${LOG_GROUP:-/ecs/${APP_NAME}}"
ALB_NAME="${ALB_NAME:-${APP_NAME}-alb}"
TG_NAME="${TG_NAME:-${APP_NAME}-tg}"
ALB_SG_NAME="${ALB_SG_NAME:-${APP_NAME}-alb-sg}"
TASK_SG_NAME="${TASK_SG_NAME:-${APP_NAME}-task-sg}"

echo "Deploying ${APP_NAME} to ECS Fargate in ${AWS_REGION}"
echo "Image: ${IMAGE_URI}"
echo "Base path: ${APP_BASE_PATH:-/}"

aws ecr describe-repositories --repository-names "$ECR_REPO" --region "$AWS_REGION" >/dev/null 2>&1 \
  || aws ecr create-repository --repository-name "$ECR_REPO" --region "$AWS_REGION" >/dev/null

aws ecr get-login-password --region "$AWS_REGION" \
  | docker login --username AWS --password-stdin "${ACCOUNT_ID}.dkr.ecr.${AWS_REGION}.amazonaws.com"

docker build -t "${APP_NAME}:${IMAGE_TAG}" .
docker tag "${APP_NAME}:${IMAGE_TAG}" "$IMAGE_URI"
docker push "$IMAGE_URI"

VPC_ID="$(aws ec2 describe-vpcs \
  --region "$AWS_REGION" \
  --filters Name=isDefault,Values=true \
  --query "Vpcs[0].VpcId" \
  --output text)"

if [[ -z "$VPC_ID" || "$VPC_ID" == "None" ]]; then
  echo "No default VPC found in ${AWS_REGION}. Create a VPC or set VPC_ID support in this script." >&2
  exit 1
fi

SUBNETS="$(aws ec2 describe-subnets \
  --region "$AWS_REGION" \
  --filters Name=vpc-id,Values="$VPC_ID" Name=default-for-az,Values=true \
  --query "Subnets[].SubnetId" \
  --output text)"

if [[ -z "$SUBNETS" ]]; then
  echo "No default subnets found in VPC ${VPC_ID}" >&2
  exit 1
fi

SUBNETS_CSV="$(echo "$SUBNETS" | tr '\t' ',')"

get_sg_id() {
  local name="$1"
  aws ec2 describe-security-groups \
    --region "$AWS_REGION" \
    --filters Name=vpc-id,Values="$VPC_ID" Name=group-name,Values="$name" \
    --query "SecurityGroups[0].GroupId" \
    --output text
}

ALB_SG_ID="$(get_sg_id "$ALB_SG_NAME")"
if [[ "$ALB_SG_ID" == "None" || -z "$ALB_SG_ID" ]]; then
  ALB_SG_ID="$(aws ec2 create-security-group \
    --region "$AWS_REGION" \
    --vpc-id "$VPC_ID" \
    --group-name "$ALB_SG_NAME" \
    --description "ALB SG for ${APP_NAME}" \
    --query GroupId \
    --output text)"
  aws ec2 authorize-security-group-ingress \
    --region "$AWS_REGION" \
    --group-id "$ALB_SG_ID" \
    --protocol tcp \
    --port 80 \
    --cidr 0.0.0.0/0 >/dev/null
fi

if [[ -n "$ALLOWED_HTTP_CIDRS" ]]; then
  EXISTING_PERMISSIONS="$(aws ec2 describe-security-groups \
    --region "$AWS_REGION" \
    --group-ids "$ALB_SG_ID" \
    --query "SecurityGroups[0].IpPermissions[?IpProtocol=='tcp' && FromPort==\`80\` && ToPort==\`80\`]" \
    --output json)"
  if [[ "$EXISTING_PERMISSIONS" != "[]" ]]; then
    aws ec2 revoke-security-group-ingress \
      --region "$AWS_REGION" \
      --group-id "$ALB_SG_ID" \
      --ip-permissions "$EXISTING_PERMISSIONS" >/dev/null || true
  fi
  IFS=',' read -ra CIDRS <<< "$ALLOWED_HTTP_CIDRS"
  for cidr in "${CIDRS[@]}"; do
    cidr="$(echo "$cidr" | xargs)"
    if [[ -n "$cidr" ]]; then
      aws ec2 authorize-security-group-ingress \
        --region "$AWS_REGION" \
        --group-id "$ALB_SG_ID" \
        --protocol tcp \
        --port 80 \
        --cidr "$cidr" >/dev/null || true
    fi
  done
fi

TASK_SG_ID="$(get_sg_id "$TASK_SG_NAME")"
if [[ "$TASK_SG_ID" == "None" || -z "$TASK_SG_ID" ]]; then
  TASK_SG_ID="$(aws ec2 create-security-group \
    --region "$AWS_REGION" \
    --vpc-id "$VPC_ID" \
    --group-name "$TASK_SG_NAME" \
    --description "Task SG for ${APP_NAME}" \
    --query GroupId \
    --output text)"
  aws ec2 authorize-security-group-ingress \
    --region "$AWS_REGION" \
    --group-id "$TASK_SG_ID" \
    --protocol tcp \
    --port "$CONTAINER_PORT" \
    --source-group "$ALB_SG_ID" >/dev/null
fi

ALB_ARN="$(aws elbv2 describe-load-balancers \
  --region "$AWS_REGION" \
  --names "$ALB_NAME" \
  --query "LoadBalancers[0].LoadBalancerArn" \
  --output text 2>/dev/null || true)"

if [[ -z "$ALB_ARN" || "$ALB_ARN" == "None" ]]; then
  ALB_ARN="$(aws elbv2 create-load-balancer \
    --region "$AWS_REGION" \
    --name "$ALB_NAME" \
    --subnets $SUBNETS \
    --security-groups "$ALB_SG_ID" \
    --scheme internet-facing \
    --type application \
    --query "LoadBalancers[0].LoadBalancerArn" \
    --output text)"
fi

ALB_DNS="$(aws elbv2 describe-load-balancers \
  --region "$AWS_REGION" \
  --load-balancer-arns "$ALB_ARN" \
  --query "LoadBalancers[0].DNSName" \
  --output text)"

TG_ARN="$(aws elbv2 describe-target-groups \
  --region "$AWS_REGION" \
  --names "$TG_NAME" \
  --query "TargetGroups[0].TargetGroupArn" \
  --output text 2>/dev/null || true)"

if [[ -z "$TG_ARN" || "$TG_ARN" == "None" ]]; then
  TG_ARN="$(aws elbv2 create-target-group \
    --region "$AWS_REGION" \
    --name "$TG_NAME" \
    --protocol HTTP \
    --port "$CONTAINER_PORT" \
    --vpc-id "$VPC_ID" \
    --target-type ip \
    --health-check-protocol HTTP \
    --health-check-path "$HEALTH_CHECK_PATH" \
    --health-check-interval-seconds 30 \
    --health-check-timeout-seconds 5 \
    --healthy-threshold-count 2 \
    --unhealthy-threshold-count 3 \
    --query "TargetGroups[0].TargetGroupArn" \
    --output text)"
fi

aws elbv2 modify-target-group \
  --region "$AWS_REGION" \
  --target-group-arn "$TG_ARN" \
  --health-check-path "$HEALTH_CHECK_PATH" >/dev/null

aws elbv2 modify-target-group-attributes \
  --region "$AWS_REGION" \
  --target-group-arn "$TG_ARN" \
  --attributes \
    Key=stickiness.enabled,Value=true \
    Key=stickiness.type,Value=lb_cookie \
    Key=stickiness.lb_cookie.duration_seconds,Value=86400 \
    Key=deregistration_delay.timeout_seconds,Value=30 >/dev/null

LISTENER_ARN="$(aws elbv2 describe-listeners \
  --region "$AWS_REGION" \
  --load-balancer-arn "$ALB_ARN" \
  --query "Listeners[?Port==\`80\`].ListenerArn | [0]" \
  --output text)"

if [[ "$LISTENER_ARN" == "None" || -z "$LISTENER_ARN" ]]; then
  aws elbv2 create-listener \
    --region "$AWS_REGION" \
    --load-balancer-arn "$ALB_ARN" \
    --protocol HTTP \
    --port 80 \
    --default-actions Type=forward,TargetGroupArn="$TG_ARN" >/dev/null
else
  aws elbv2 modify-listener \
    --region "$AWS_REGION" \
    --listener-arn "$LISTENER_ARN" \
    --default-actions Type=forward,TargetGroupArn="$TG_ARN" >/dev/null
fi

aws ecs describe-clusters --region "$AWS_REGION" --clusters "$CLUSTER_NAME" \
  --query "clusters[0].clusterName" --output text | grep -q "$CLUSTER_NAME" \
  || aws ecs create-cluster --region "$AWS_REGION" --cluster-name "$CLUSTER_NAME" >/dev/null

if ! aws iam get-role --role-name "$TASK_EXEC_ROLE" >/dev/null 2>&1; then
  aws iam create-role \
    --role-name "$TASK_EXEC_ROLE" \
    --assume-role-policy-document '{
      "Version": "2012-10-17",
      "Statement": [{
        "Effect": "Allow",
        "Principal": {"Service": "ecs-tasks.amazonaws.com"},
        "Action": "sts:AssumeRole"
      }]
    }' >/dev/null
  aws iam attach-role-policy \
    --role-name "$TASK_EXEC_ROLE" \
    --policy-arn arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy
fi

if [[ -n "$APP_ACCESS_PASSWORD" && -z "$APP_ACCESS_PASSWORD_SECRET_ARN" ]]; then
  if aws secretsmanager describe-secret \
    --region "$AWS_REGION" \
    --secret-id "$APP_ACCESS_PASSWORD_SECRET_NAME" >/dev/null 2>&1; then
    aws secretsmanager put-secret-value \
      --region "$AWS_REGION" \
      --secret-id "$APP_ACCESS_PASSWORD_SECRET_NAME" \
      --secret-string "$APP_ACCESS_PASSWORD" >/dev/null
  else
    aws secretsmanager create-secret \
      --region "$AWS_REGION" \
      --name "$APP_ACCESS_PASSWORD_SECRET_NAME" \
      --secret-string "$APP_ACCESS_PASSWORD" >/dev/null
  fi
  APP_ACCESS_PASSWORD_SECRET_ARN="$(aws secretsmanager describe-secret \
    --region "$AWS_REGION" \
    --secret-id "$APP_ACCESS_PASSWORD_SECRET_NAME" \
    --query ARN \
    --output text)"
fi

if [[ -z "$APP_ACCESS_PASSWORD" && -z "$APP_ACCESS_PASSWORD_SECRET_ARN" ]]; then
  if aws secretsmanager describe-secret \
    --region "$AWS_REGION" \
    --secret-id "$APP_ACCESS_PASSWORD_SECRET_NAME" >/dev/null 2>&1; then
    APP_ACCESS_PASSWORD_SECRET_ARN="$(aws secretsmanager describe-secret \
      --region "$AWS_REGION" \
      --secret-id "$APP_ACCESS_PASSWORD_SECRET_NAME" \
      --query ARN \
      --output text)"
  fi
fi

if [[ -n "$RAINFOREST_API_KEY" && -z "$RAINFOREST_API_KEY_SECRET_ARN" ]]; then
  if aws secretsmanager describe-secret \
    --region "$AWS_REGION" \
    --secret-id "$RAINFOREST_API_KEY_SECRET_NAME" >/dev/null 2>&1; then
    aws secretsmanager put-secret-value \
      --region "$AWS_REGION" \
      --secret-id "$RAINFOREST_API_KEY_SECRET_NAME" \
      --secret-string "$RAINFOREST_API_KEY" >/dev/null
  else
    aws secretsmanager create-secret \
      --region "$AWS_REGION" \
      --name "$RAINFOREST_API_KEY_SECRET_NAME" \
      --secret-string "$RAINFOREST_API_KEY" >/dev/null
  fi
  RAINFOREST_API_KEY_SECRET_ARN="$(aws secretsmanager describe-secret \
    --region "$AWS_REGION" \
    --secret-id "$RAINFOREST_API_KEY_SECRET_NAME" \
    --query ARN \
    --output text)"
fi

if [[ -z "$RAINFOREST_API_KEY" && -z "$RAINFOREST_API_KEY_SECRET_ARN" ]]; then
  if aws secretsmanager describe-secret \
    --region "$AWS_REGION" \
    --secret-id "$RAINFOREST_API_KEY_SECRET_NAME" >/dev/null 2>&1; then
    RAINFOREST_API_KEY_SECRET_ARN="$(aws secretsmanager describe-secret \
      --region "$AWS_REGION" \
      --secret-id "$RAINFOREST_API_KEY_SECRET_NAME" \
      --query ARN \
      --output text)"
  fi
fi

if [[ -n "$YOUTUBE_API_KEY" && -z "$YOUTUBE_API_KEY_SECRET_ARN" ]]; then
  if aws secretsmanager describe-secret \
    --region "$AWS_REGION" \
    --secret-id "$YOUTUBE_API_KEY_SECRET_NAME" >/dev/null 2>&1; then
    aws secretsmanager put-secret-value \
      --region "$AWS_REGION" \
      --secret-id "$YOUTUBE_API_KEY_SECRET_NAME" \
      --secret-string "$YOUTUBE_API_KEY" >/dev/null
  else
    aws secretsmanager create-secret \
      --region "$AWS_REGION" \
      --name "$YOUTUBE_API_KEY_SECRET_NAME" \
      --secret-string "$YOUTUBE_API_KEY" >/dev/null
  fi
  YOUTUBE_API_KEY_SECRET_ARN="$(aws secretsmanager describe-secret \
    --region "$AWS_REGION" \
    --secret-id "$YOUTUBE_API_KEY_SECRET_NAME" \
    --query ARN \
    --output text)"
fi

if [[ -z "$YOUTUBE_API_KEY" && -z "$YOUTUBE_API_KEY_SECRET_ARN" ]]; then
  if aws secretsmanager describe-secret \
    --region "$AWS_REGION" \
    --secret-id "$YOUTUBE_API_KEY_SECRET_NAME" >/dev/null 2>&1; then
    YOUTUBE_API_KEY_SECRET_ARN="$(aws secretsmanager describe-secret \
      --region "$AWS_REGION" \
      --secret-id "$YOUTUBE_API_KEY_SECRET_NAME" \
      --query ARN \
      --output text)"
  fi
fi

if [[ -n "$SOCIAL_OEMBED_ACCESS_TOKEN" && -z "$SOCIAL_OEMBED_ACCESS_TOKEN_SECRET_ARN" ]]; then
  if aws secretsmanager describe-secret \
    --region "$AWS_REGION" \
    --secret-id "$SOCIAL_OEMBED_ACCESS_TOKEN_SECRET_NAME" >/dev/null 2>&1; then
    aws secretsmanager put-secret-value \
      --region "$AWS_REGION" \
      --secret-id "$SOCIAL_OEMBED_ACCESS_TOKEN_SECRET_NAME" \
      --secret-string "$SOCIAL_OEMBED_ACCESS_TOKEN" >/dev/null
  else
    aws secretsmanager create-secret \
      --region "$AWS_REGION" \
      --name "$SOCIAL_OEMBED_ACCESS_TOKEN_SECRET_NAME" \
      --secret-string "$SOCIAL_OEMBED_ACCESS_TOKEN" >/dev/null
  fi
  SOCIAL_OEMBED_ACCESS_TOKEN_SECRET_ARN="$(aws secretsmanager describe-secret \
    --region "$AWS_REGION" \
    --secret-id "$SOCIAL_OEMBED_ACCESS_TOKEN_SECRET_NAME" \
    --query ARN \
    --output text)"
fi

if [[ -z "$SOCIAL_OEMBED_ACCESS_TOKEN" && -z "$SOCIAL_OEMBED_ACCESS_TOKEN_SECRET_ARN" ]]; then
  if aws secretsmanager describe-secret \
    --region "$AWS_REGION" \
    --secret-id "$SOCIAL_OEMBED_ACCESS_TOKEN_SECRET_NAME" >/dev/null 2>&1; then
    SOCIAL_OEMBED_ACCESS_TOKEN_SECRET_ARN="$(aws secretsmanager describe-secret \
      --region "$AWS_REGION" \
      --secret-id "$SOCIAL_OEMBED_ACCESS_TOKEN_SECRET_NAME" \
      --query ARN \
      --output text)"
  fi
fi

if [[ -n "$LIBLIBAI_ACCESS_KEY" && -z "$LIBLIBAI_ACCESS_KEY_SECRET_ARN" ]]; then
  if aws secretsmanager describe-secret \
    --region "$AWS_REGION" \
    --secret-id "$LIBLIBAI_ACCESS_KEY_SECRET_NAME" >/dev/null 2>&1; then
    aws secretsmanager put-secret-value \
      --region "$AWS_REGION" \
      --secret-id "$LIBLIBAI_ACCESS_KEY_SECRET_NAME" \
      --secret-string "$LIBLIBAI_ACCESS_KEY" >/dev/null
  else
    aws secretsmanager create-secret \
      --region "$AWS_REGION" \
      --name "$LIBLIBAI_ACCESS_KEY_SECRET_NAME" \
      --secret-string "$LIBLIBAI_ACCESS_KEY" >/dev/null
  fi
  LIBLIBAI_ACCESS_KEY_SECRET_ARN="$(aws secretsmanager describe-secret \
    --region "$AWS_REGION" \
    --secret-id "$LIBLIBAI_ACCESS_KEY_SECRET_NAME" \
    --query ARN \
    --output text)"
fi

if [[ -z "$LIBLIBAI_ACCESS_KEY" && -z "$LIBLIBAI_ACCESS_KEY_SECRET_ARN" ]]; then
  if aws secretsmanager describe-secret \
    --region "$AWS_REGION" \
    --secret-id "$LIBLIBAI_ACCESS_KEY_SECRET_NAME" >/dev/null 2>&1; then
    LIBLIBAI_ACCESS_KEY_SECRET_ARN="$(aws secretsmanager describe-secret \
      --region "$AWS_REGION" \
      --secret-id "$LIBLIBAI_ACCESS_KEY_SECRET_NAME" \
      --query ARN \
      --output text)"
  fi
fi

if [[ -n "$LIBLIBAI_SECRET_KEY" && -z "$LIBLIBAI_SECRET_KEY_SECRET_ARN" ]]; then
  if aws secretsmanager describe-secret \
    --region "$AWS_REGION" \
    --secret-id "$LIBLIBAI_SECRET_KEY_SECRET_NAME" >/dev/null 2>&1; then
    aws secretsmanager put-secret-value \
      --region "$AWS_REGION" \
      --secret-id "$LIBLIBAI_SECRET_KEY_SECRET_NAME" \
      --secret-string "$LIBLIBAI_SECRET_KEY" >/dev/null
  else
    aws secretsmanager create-secret \
      --region "$AWS_REGION" \
      --name "$LIBLIBAI_SECRET_KEY_SECRET_NAME" \
      --secret-string "$LIBLIBAI_SECRET_KEY" >/dev/null
  fi
  LIBLIBAI_SECRET_KEY_SECRET_ARN="$(aws secretsmanager describe-secret \
    --region "$AWS_REGION" \
    --secret-id "$LIBLIBAI_SECRET_KEY_SECRET_NAME" \
    --query ARN \
    --output text)"
fi

if [[ -z "$LIBLIBAI_SECRET_KEY" && -z "$LIBLIBAI_SECRET_KEY_SECRET_ARN" ]]; then
  if aws secretsmanager describe-secret \
    --region "$AWS_REGION" \
    --secret-id "$LIBLIBAI_SECRET_KEY_SECRET_NAME" >/dev/null 2>&1; then
    LIBLIBAI_SECRET_KEY_SECRET_ARN="$(aws secretsmanager describe-secret \
      --region "$AWS_REGION" \
      --secret-id "$LIBLIBAI_SECRET_KEY_SECRET_NAME" \
      --query ARN \
      --output text)"
  fi
fi

if ! aws iam get-role --role-name "$TASK_ROLE" >/dev/null 2>&1; then
  aws iam create-role \
    --role-name "$TASK_ROLE" \
    --assume-role-policy-document '{
      "Version": "2012-10-17",
      "Statement": [{
        "Effect": "Allow",
        "Principal": {"Service": "ecs-tasks.amazonaws.com"},
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
        "bedrock:InvokeModelWithResponseStream",
        "bedrock:StartAsyncInvoke",
        "bedrock:GetAsyncInvoke",
        "bedrock:ListAsyncInvokes"
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
    if [[ "$AWS_REGION" == "us-east-1" ]]; then
      aws s3api create-bucket \
        --bucket "$S3_BUCKET" \
        --region "$AWS_REGION" >/dev/null
    else
      aws s3api create-bucket \
        --bucket "$S3_BUCKET" \
        --region "$AWS_REGION" \
        --create-bucket-configuration LocationConstraint="$AWS_REGION" >/dev/null
    fi
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
  --role-name "$TASK_ROLE" \
  --policy-name "${APP_NAME}-runtime-policy" \
  --policy-document "file://${POLICY_DOC}"

aws logs create-log-group --region "$AWS_REGION" --log-group-name "$LOG_GROUP" >/dev/null 2>&1 || true

EXEC_ROLE_ARN="$(aws iam get-role --role-name "$TASK_EXEC_ROLE" --query 'Role.Arn' --output text)"
TASK_ROLE_ARN="$(aws iam get-role --role-name "$TASK_ROLE" --query 'Role.Arn' --output text)"

SECRET_ARNS=()
if [[ -n "$APP_ACCESS_PASSWORD_SECRET_ARN" ]]; then
  SECRET_ARNS+=("$APP_ACCESS_PASSWORD_SECRET_ARN")
fi
if [[ -n "$DATABASE_URL_SECRET_ARN" ]]; then
  SECRET_ARNS+=("$DATABASE_URL_SECRET_ARN")
fi
if [[ -n "$RAINFOREST_API_KEY_SECRET_ARN" ]]; then
  SECRET_ARNS+=("$RAINFOREST_API_KEY_SECRET_ARN")
fi
if [[ -n "$YOUTUBE_API_KEY_SECRET_ARN" ]]; then
  SECRET_ARNS+=("$YOUTUBE_API_KEY_SECRET_ARN")
fi
if [[ -n "$SOCIAL_OEMBED_ACCESS_TOKEN_SECRET_ARN" ]]; then
  SECRET_ARNS+=("$SOCIAL_OEMBED_ACCESS_TOKEN_SECRET_ARN")
fi
if [[ -n "$LIBLIBAI_ACCESS_KEY_SECRET_ARN" ]]; then
  SECRET_ARNS+=("$LIBLIBAI_ACCESS_KEY_SECRET_ARN")
fi
if [[ -n "$LIBLIBAI_SECRET_KEY_SECRET_ARN" ]]; then
  SECRET_ARNS+=("$LIBLIBAI_SECRET_KEY_SECRET_ARN")
fi

if [[ ${#SECRET_ARNS[@]} -gt 0 ]]; then
  SECRET_POLICY_DOC="$(mktemp)"
  SECRET_RESOURCES_JSON="$(printf '%s\n' "${SECRET_ARNS[@]}" | python3 -c 'import json, sys; print(json.dumps([line.strip() for line in sys.stdin if line.strip()]))')"
  cat > "$SECRET_POLICY_DOC" <<JSON
{
  "Version": "2012-10-17",
  "Statement": [{
    "Effect": "Allow",
    "Action": [
      "secretsmanager:GetSecretValue"
    ],
    "Resource": ${SECRET_RESOURCES_JSON}
  }]
}
JSON
  aws iam put-role-policy \
    --role-name "$TASK_EXEC_ROLE" \
    --policy-name "${APP_NAME}-read-app-secrets" \
    --policy-document "file://${SECRET_POLICY_DOC}"
  aws iam put-role-policy \
    --role-name "$TASK_ROLE" \
    --policy-name "${APP_NAME}-read-app-secrets-runtime" \
    --policy-document "file://${SECRET_POLICY_DOC}"
fi

TASK_DEF_FILE="$(mktemp)"
python3 - "$TASK_DEF_FILE" <<PY
import json, os, sys
env = [
    {"name": "APP_RUNTIME", "value": "${APP_RUNTIME}"},
    {"name": "APP_BASE_PATH", "value": "${APP_BASE_PATH}"},
    {"name": "BEDROCK_AWS_REGION", "value": "${BEDROCK_AWS_REGION}"},
    {"name": "BEDROCK_MODEL_ID", "value": "${BEDROCK_MODEL_ID}"},
    {"name": "BEDROCK_MODEL_FALLBACK_IDS", "value": "${BEDROCK_MODEL_FALLBACK_IDS}"},
    {"name": "BEDROCK_MAX_TOKENS", "value": "${BEDROCK_MAX_TOKENS}"},
    {"name": "BEDROCK_FALLBACK_MAX_TOKENS", "value": "${BEDROCK_FALLBACK_MAX_TOKENS}"},
    {"name": "NOVA_REEL_AWS_REGION", "value": "${NOVA_REEL_AWS_REGION}"},
    {"name": "NOVA_REEL_MODEL_ID", "value": "${NOVA_REEL_MODEL_ID}"},
    {"name": "NOVA_REEL_OUTPUT_S3_URI", "value": "${NOVA_REEL_OUTPUT_S3_URI}"},
    {"name": "NOVA_REEL_ESTIMATED_USD_PER_SECOND", "value": "${NOVA_REEL_ESTIMATED_USD_PER_SECOND}"},
    {"name": "NOVA_REEL_MAX_SUBMISSIONS_PER_CLICK", "value": "${NOVA_REEL_MAX_SUBMISSIONS_PER_CLICK}"},
    {"name": "NOVA_CANVAS_AWS_REGION", "value": "${NOVA_CANVAS_AWS_REGION}"},
    {"name": "NOVA_CANVAS_MODEL_ID", "value": "${NOVA_CANVAS_MODEL_ID}"},
    {"name": "NOVA_CANVAS_ESTIMATED_USD_PER_IMAGE", "value": "${NOVA_CANVAS_ESTIMATED_USD_PER_IMAGE}"},
    {"name": "NOVA_CANVAS_REFERENCE_STRENGTH", "value": "${NOVA_CANVAS_REFERENCE_STRENGTH}"},
    {"name": "MEDIA_IMAGE_PROVIDER", "value": "${MEDIA_IMAGE_PROVIDER}"},
    {"name": "LIBLIBAI_BASE_URL", "value": "${LIBLIBAI_BASE_URL}"},
    {"name": "LIBLIBAI_TEMPLATE_UUID", "value": "${LIBLIBAI_TEMPLATE_UUID}"},
    {"name": "LIBLIBAI_IMAGE_MODEL_LABEL", "value": "${LIBLIBAI_IMAGE_MODEL_LABEL}"},
    {"name": "LIBLIBAI_IMAGE_ASPECT_RATIO", "value": "${LIBLIBAI_IMAGE_ASPECT_RATIO}"},
    {"name": "LIBLIBAI_IMAGE_WIDTH", "value": "${LIBLIBAI_IMAGE_WIDTH}"},
    {"name": "LIBLIBAI_IMAGE_HEIGHT", "value": "${LIBLIBAI_IMAGE_HEIGHT}"},
    {"name": "LIBLIBAI_IMAGE_SIZE_ENABLED", "value": "${LIBLIBAI_IMAGE_SIZE_ENABLED}"},
    {"name": "LIBLIBAI_IMAGE_STEPS", "value": "${LIBLIBAI_IMAGE_STEPS}"},
    {"name": "LIBLIBAI_IMAGE_COUNT", "value": "${LIBLIBAI_IMAGE_COUNT}"},
    {"name": "LIBLIBAI_REQUEST_TIMEOUT", "value": "${LIBLIBAI_REQUEST_TIMEOUT}"},
    {"name": "LIBLIBAI_POLL_TIMEOUT", "value": "${LIBLIBAI_POLL_TIMEOUT}"},
    {"name": "LIBLIBAI_POLL_INTERVAL", "value": "${LIBLIBAI_POLL_INTERVAL}"},
    {"name": "LIBLIBAI_MAX_PROMPT_LENGTH", "value": "${LIBLIBAI_MAX_PROMPT_LENGTH}"},
    {"name": "LIBLIBAI_REFERENCE_CONTROL_TYPE", "value": "${LIBLIBAI_REFERENCE_CONTROL_TYPE}"},
    {"name": "RAINFOREST_DEFAULT_AMAZON_DOMAIN", "value": "${RAINFOREST_DEFAULT_AMAZON_DOMAIN}"},
    {"name": "RAINFOREST_SEARCH_TOP_N", "value": "${RAINFOREST_SEARCH_TOP_N}"},
    {"name": "RAINFOREST_DISCOVERY_REQUEST_LIMIT", "value": "${RAINFOREST_DISCOVERY_REQUEST_LIMIT}"},
    {"name": "RAINFOREST_MAX_PRODUCTS_PER_REFRESH", "value": "${RAINFOREST_MAX_PRODUCTS_PER_REFRESH}"},
    {"name": "YOUTUBE_DISCOVERY_TOP_N", "value": "${YOUTUBE_DISCOVERY_TOP_N}"},
    {"name": "YOUTUBE_DISCOVERY_REQUEST_LIMIT", "value": "${YOUTUBE_DISCOVERY_REQUEST_LIMIT}"},
    {"name": "SOCIAL_REQUEST_TIMEOUT", "value": "${SOCIAL_REQUEST_TIMEOUT}"},
    {"name": "APP_DATA_DIR", "value": "/app/data"},
    {"name": "STORAGE_BACKEND", "value": "${STORAGE_BACKEND}"},
    {"name": "STREAMLIT_SERVER_ENABLE_CORS", "value": "false"},
    {"name": "STREAMLIT_SERVER_ENABLE_XSRF_PROTECTION", "value": "false"},
    {"name": "STREAMLIT_SERVER_ENABLE_WEBSOCKET_COMPRESSION", "value": "false"},
]
if "${S3_BUCKET}":
    env.append({"name": "S3_BUCKET", "value": "${S3_BUCKET}"})
if "${S3_PREFIX}":
    env.append({"name": "S3_PREFIX", "value": "${S3_PREFIX}"})
if "${APP_ACCESS_PASSWORD_SECRET_ARN}":
    env.append({"name": "APP_ACCESS_PASSWORD_SECRET_ID", "value": "${APP_ACCESS_PASSWORD_SECRET_ARN}"})
    env.append({"name": "APP_ACCESS_PASSWORD_CACHE_TTL", "value": "${APP_ACCESS_PASSWORD_CACHE_TTL}"})
if "${RAINFOREST_API_KEY_SECRET_ARN}":
    env.append({"name": "RAINFOREST_API_KEY_SECRET_ID", "value": "${RAINFOREST_API_KEY_SECRET_ARN}"})
if "${YOUTUBE_API_KEY_SECRET_ARN}":
    env.append({"name": "YOUTUBE_API_KEY_SECRET_ID", "value": "${YOUTUBE_API_KEY_SECRET_ARN}"})
if "${SOCIAL_OEMBED_ACCESS_TOKEN_SECRET_ARN}":
    env.append({"name": "SOCIAL_OEMBED_ACCESS_TOKEN_SECRET_ID", "value": "${SOCIAL_OEMBED_ACCESS_TOKEN_SECRET_ARN}"})
if "${LIBLIBAI_ACCESS_KEY_SECRET_ARN}":
    env.append({"name": "LIBLIBAI_ACCESS_KEY_SECRET_ID", "value": "${LIBLIBAI_ACCESS_KEY_SECRET_ARN}"})
if "${LIBLIBAI_SECRET_KEY_SECRET_ARN}":
    env.append({"name": "LIBLIBAI_SECRET_KEY_SECRET_ID", "value": "${LIBLIBAI_SECRET_KEY_SECRET_ARN}"})
secrets = []
if "${APP_ACCESS_PASSWORD_SECRET_ARN}":
    secrets.append({"name": "APP_ACCESS_PASSWORD", "valueFrom": "${APP_ACCESS_PASSWORD_SECRET_ARN}"})
if "${DATABASE_URL_SECRET_ARN}":
    secrets.append({"name": "DATABASE_URL", "valueFrom": "${DATABASE_URL_SECRET_ARN}"})
if "${RAINFOREST_API_KEY_SECRET_ARN}":
    secrets.append({"name": "RAINFOREST_API_KEY", "valueFrom": "${RAINFOREST_API_KEY_SECRET_ARN}"})
if "${YOUTUBE_API_KEY_SECRET_ARN}":
    secrets.append({"name": "YOUTUBE_API_KEY", "valueFrom": "${YOUTUBE_API_KEY_SECRET_ARN}"})
if "${SOCIAL_OEMBED_ACCESS_TOKEN_SECRET_ARN}":
    secrets.append({"name": "SOCIAL_OEMBED_ACCESS_TOKEN", "valueFrom": "${SOCIAL_OEMBED_ACCESS_TOKEN_SECRET_ARN}"})
if "${LIBLIBAI_ACCESS_KEY_SECRET_ARN}":
    secrets.append({"name": "LIBLIBAI_ACCESS_KEY", "valueFrom": "${LIBLIBAI_ACCESS_KEY_SECRET_ARN}"})
if "${LIBLIBAI_SECRET_KEY_SECRET_ARN}":
    secrets.append({"name": "LIBLIBAI_SECRET_KEY", "valueFrom": "${LIBLIBAI_SECRET_KEY_SECRET_ARN}"})

doc = {
    "family": "${TASK_FAMILY}",
    "networkMode": "awsvpc",
    "requiresCompatibilities": ["FARGATE"],
    "cpu": "1024",
    "memory": "2048",
    "executionRoleArn": "${EXEC_ROLE_ARN}",
    "taskRoleArn": "${TASK_ROLE_ARN}",
    "containerDefinitions": [{
        "name": "${APP_NAME}",
        "image": "${IMAGE_URI}",
        "essential": True,
        "portMappings": [{"containerPort": ${CONTAINER_PORT}, "protocol": "tcp"}],
        "environment": env,
        "secrets": secrets,
        "logConfiguration": {
            "logDriver": "awslogs",
            "options": {
                "awslogs-group": "${LOG_GROUP}",
                "awslogs-region": "${AWS_REGION}",
                "awslogs-stream-prefix": "ecs"
            }
        }
    }]
}
json.dump(doc, open(sys.argv[1], "w"))
PY

TASK_DEF_ARN="$(aws ecs register-task-definition \
  --region "$AWS_REGION" \
  --cli-input-json "file://${TASK_DEF_FILE}" \
  --query "taskDefinition.taskDefinitionArn" \
  --output text)"

SERVICE_STATUS="$(aws ecs describe-services \
  --region "$AWS_REGION" \
  --cluster "$CLUSTER_NAME" \
  --services "$SERVICE_NAME" \
  --query "services[0].status" \
  --output text 2>/dev/null || true)"

if [[ "$SERVICE_STATUS" == "ACTIVE" ]]; then
  aws ecs update-service \
    --region "$AWS_REGION" \
    --cluster "$CLUSTER_NAME" \
    --service "$SERVICE_NAME" \
    --task-definition "$TASK_DEF_ARN" \
    --desired-count "$DESIRED_COUNT" \
    --force-new-deployment >/dev/null
else
  aws ecs create-service \
    --region "$AWS_REGION" \
    --cluster "$CLUSTER_NAME" \
    --service-name "$SERVICE_NAME" \
    --task-definition "$TASK_DEF_ARN" \
    --desired-count "$DESIRED_COUNT" \
    --launch-type FARGATE \
    --network-configuration "awsvpcConfiguration={subnets=[$SUBNETS_CSV],securityGroups=[$TASK_SG_ID],assignPublicIp=ENABLED}" \
    --load-balancers "targetGroupArn=${TG_ARN},containerName=${APP_NAME},containerPort=${CONTAINER_PORT}" \
    --health-check-grace-period-seconds 120 >/dev/null
fi

echo "Waiting for ECS service to become stable..."
aws ecs wait services-stable \
  --region "$AWS_REGION" \
  --cluster "$CLUSTER_NAME" \
  --services "$SERVICE_NAME"

echo
echo "ECS service is stable."
echo "Open: http://${ALB_DNS}"
echo
aws ecs describe-services \
  --region "$AWS_REGION" \
  --cluster "$CLUSTER_NAME" \
  --services "$SERVICE_NAME" \
  --query "services[0].{Status:status,Running:runningCount,Desired:desiredCount,TaskDefinition:taskDefinition}" \
  --output table

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
CONTAINER_PORT="${CONTAINER_PORT:-8501}"
DESIRED_COUNT="${DESIRED_COUNT:-1}"

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
    --health-check-path "/_stcore/health" \
    --health-check-interval-seconds 30 \
    --health-check-timeout-seconds 5 \
    --healthy-threshold-count 2 \
    --unhealthy-threshold-count 3 \
    --query "TargetGroups[0].TargetGroupArn" \
    --output text)"
fi

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

TASK_DEF_FILE="$(mktemp)"
python3 - "$TASK_DEF_FILE" <<PY
import json, os, sys
env = [
    {"name": "BEDROCK_AWS_REGION", "value": "${BEDROCK_AWS_REGION}"},
    {"name": "BEDROCK_MODEL_ID", "value": "${BEDROCK_MODEL_ID}"},
    {"name": "BEDROCK_MAX_TOKENS", "value": "${BEDROCK_MAX_TOKENS}"},
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

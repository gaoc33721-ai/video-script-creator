# AWS 部署 Runbook

## 1. 本地容器验证

```powershell
docker build -t video-script-creator:local .
docker run --rm -p 8501:8501 `
  -e BEDROCK_AWS_REGION="us-east-1" `
  -e BEDROCK_MODEL_ID="eu.amazon.nova-pro-v1:0" `
  -e APP_DATA_DIR=/app/data `
  video-script-creator:local
```

浏览器访问：

```text
http://localhost:8501
```

## 2. 创建基础 AWS 资源

建议先创建：

- ECR repository：`video-script-creator`
- S3 bucket：`video-script-creator-prod-assets`
- Secrets Manager secret：`video-script-creator/prod/database`
- CloudWatch log group：`/ecs/video-script-creator`
- ECS cluster：`internal-ai-tools`

## 2.1 没有本地 AWS 凭证怎么办

推荐走公司 AWS IAM Identity Center/SSO，不建议在个人电脑长期保存 IAM access key。

```powershell
aws configure sso --profile video-script-prod
aws sso login --profile video-script-prod
$env:AWS_PROFILE="video-script-prod"
$env:AWS_REGION="us-east-1"
$env:BEDROCK_AWS_REGION="us-east-1"
```

如果公司暂时只给 IAM access key，则用：

```powershell
aws configure --profile video-script-prod
$env:AWS_PROFILE="video-script-prod"
```

至少需要这些权限：

- 创建/更新 ECR、ECS/App Runner、S3、CloudWatch、Secrets Manager、IAM role。
- 调用 Bedrock：`bedrock:InvokeModel`、`bedrock:InvokeModelWithResponseStream`。
- 如需初始化 RDS：连接数据库并执行 `aws/postgres_schema.sql`。

## 2.2 没有 Bedrock 模型授权怎么办

进入 AWS Console：

1. 打开 Amazon Bedrock。
2. 确认区域与 `BEDROCK_AWS_REGION` 一致，例如 `us-east-1`。
3. 打开 Model access / Model catalog。
4. 确认目标模型可用：默认是 `eu.amazon.nova-pro-v1:0`。
5. 如果该 profile 不可用，选择账号已可用的 Amazon Nova 模型/profile，并把 `BEDROCK_MODEL_ID` 改成对应 ID。

AWS 当前文档说明：Bedrock 模型访问与 Marketplace/模型访问权限相关；Converse 调用需要 `bedrock:InvokeModel` 权限。

## 2.3 部署前自检

```powershell
pip install -r requirements.txt
python scripts/check_aws_ready.py
```

自检会确认：

- 当前 AWS identity。
- S3 bucket 是否可访问。
- Bedrock 模型是否能列出。
- `Converse` 是否能实际调用。

## 3. 推送镜像到 ECR

```powershell
aws ecr get-login-password --region <region> | docker login --username AWS --password-stdin <account-id>.dkr.ecr.<region>.amazonaws.com
docker tag video-script-creator:local <account-id>.dkr.ecr.<region>.amazonaws.com/video-script-creator:latest
docker push <account-id>.dkr.ecr.<region>.amazonaws.com/video-script-creator:latest
```

## 3.1 快速部署到 App Runner

CloudShell 中确认 `python3 scripts/check_aws_ready.py` 成功后，可以先用 App Runner 跑内部试用版：

```bash
export AWS_REGION=eu-central-1
export AWS_DEFAULT_REGION=eu-central-1
export BEDROCK_AWS_REGION=eu-central-1
export BEDROCK_MODEL_ID=eu.amazon.nova-pro-v1:0

bash scripts/deploy_apprunner.sh
```

部署后查状态和日志：

```bash
git pull
bash scripts/apprunner_status.sh
```

如果健康检查返回 `ok` 但浏览器一直停留在 Streamlit 骨架屏，通常是反向代理下 websocket/session 配置问题。镜像中已设置：

- `STREAMLIT_SERVER_ENABLE_CORS=false`
- `STREAMLIT_SERVER_ENABLE_XSRF_PROTECTION=false`
- `STREAMLIT_SERVER_ENABLE_WEBSOCKET_COMPRESSION=false`

如果 App Runner 仍无法加载 Streamlit 页面，切到 ECS Fargate + ALB：

```bash
git pull
bash scripts/deploy_ecs_fargate.sh
bash scripts/ecs_status.sh
```

ECS/Fargate 的 target group 会开启 ALB sticky session。Streamlit 的上传和页面会话依赖同一个后端 task；滚动部署期间若浏览器仍连着旧 task，可能出现 `AxiosError: Request failed with status code 400`。等待部署 stable 后强刷页面再上传即可。

默认使用本地容器临时存储。若要让上传的 Excel 和配置持久化到 S3：

```bash
export STORAGE_BACKEND=s3
export S3_BUCKET=video-script-creator-prod-assets-625093290485
export S3_PREFIX=runtime

bash scripts/deploy_apprunner.sh
```

ECS/Fargate 使用 S3 持久化：

```bash
export STORAGE_BACKEND=s3
export S3_BUCKET=video-script-creator-prod-assets-625093290485
export S3_PREFIX=runtime

bash scripts/deploy_ecs_fargate.sh
bash scripts/ecs_status.sh
```

`deploy_ecs_fargate.sh` 会在 bucket 不存在时自动创建，并给 ECS task role 增加对应 bucket 的读写权限。

## 3.2 访问控制

试用期建议先启用应用访问密码：

```bash
export APP_ACCESS_PASSWORD='替换为内部试用密码'
bash scripts/deploy_ecs_fargate.sh
```

如果已经拿到公司出口 IP/CIDR，可同时限制 ALB 只允许这些来源访问：

```bash
export ALLOWED_HTTP_CIDRS='203.0.113.10/32,198.51.100.0/24'
bash scripts/deploy_ecs_fargate.sh
```

说明：

- `APP_ACCESS_PASSWORD` 为空时，不显示密码门禁。
- `ALLOWED_HTTP_CIDRS` 为空时，ALB 保持当前安全组规则。
- 正式版建议接入 Cognito / IAM Identity Center / 公司 SSO。

脚本会创建/更新：

- ECR repository
- App Runner ECR access role
- App Runner instance role
- App Runner service
- 可选 S3 bucket

部署脚本默认使用当前 git commit 短 SHA 作为镜像 tag，避免 App Runner 因复用 `latest` 标签而继续运行旧镜像。

## 4. ECS 任务环境变量

| 名称 | 来源 | 示例 |
|---|---|---|
| `BEDROCK_AWS_REGION` | Environment | `us-east-1` |
| `BEDROCK_MODEL_ID` | Environment | `eu.amazon.nova-pro-v1:0` |
| `BEDROCK_MAX_TOKENS` | Environment | `4096` |
| `APP_DATA_DIR` | Environment | `/app/data` |
| `STORAGE_BACKEND` | Environment | `local` 或 `s3` |
| `S3_BUCKET` | Environment | `video-script-creator-prod-assets` |
| `S3_PREFIX` | Environment | `runtime` |
| `DATABASE_URL` | Secrets Manager | `video-script-creator/prod/database:DATABASE_URL` |

ECS task role 需要至少包含：

- `bedrock:InvokeModel`
- `bedrock:InvokeModelWithResponseStream`（为后续流式输出预留）
- `s3:GetObject`
- `s3:PutObject`
- `s3:HeadObject`
- `s3:ListBucket`（限定到平台 bucket）

## 5. 初始化 PostgreSQL

在 RDS PostgreSQL 创建后执行：

```powershell
psql $env:DATABASE_URL -f aws/postgres_schema.sql
```

如果暂时没有开启 `pgvector` 扩展权限，可先注释 schema 中的 `create extension if not exists vector;` 和 `embedding vector(1536)` 字段；第一阶段脚本生成不依赖向量字段。

## 6. 导入产品卖点库

```powershell
python scripts/import_product_features.py "../产品卖点库（0422）.xlsx" --database-url $env:DATABASE_URL
```

应用内上传也会走同一套过滤规则：只保留 `英语` 和 `全球通用版`，并要求 `Feature Description`、`model`、`Category` 非空。

## 7. 第一阶段部署判断标准

- 应用能通过 ALB 或 App Runner URL 打开。
- 能上传 `产品卖点库（0422）.xlsx` 并看到品类、型号、卖点数量。
- 能成功生成 2 套脚本。
- 能下载 Excel。
- CloudWatch 能看到应用日志。
- 容器重启后，如暂未接 RDS/S3，需明确提示当前仍是临时缓存；接入 RDS/S3 后历史不丢失。

# GitHub Actions 自动部署说明

## 为什么之前需要手动复制 CloudShell 命令

Codex 当前本地环境没有 AWS 凭证，也没有 Bedrock/ECS/ECR/S3 的操作权限。代码可以由 Codex 修改并推送到 GitHub，但部署动作只能在已经登录 AWS 的 CloudShell 中执行。

要实现“代码推送后自动部署”，需要把 AWS 部署权限交给 GitHub Actions。推荐使用 GitHub OIDC + AWS IAM Role，不建议把长期 Access Key 放到 GitHub。

## 自动部署后的流程

完成一次性配置后：

1. Codex 修改代码。
2. Codex push 到 `main`。
3. GitHub Actions 自动触发 `.github/workflows/deploy-ecs.yml`。
4. Actions 通过 OIDC 临时扮演 AWS 部署角色。
5. 自动执行 `scripts/deploy_ecs_fargate.sh`。
6. 自动构建镜像、推送 ECR、更新 ECS/Fargate 服务。

也可以在 GitHub Actions 页面手动点击 `Deploy ECS Fargate` 工作流运行。

## GitHub 需要配置的 Secrets

进入 GitHub 仓库：

`Settings` -> `Secrets and variables` -> `Actions`

新增 Secret：

| 名称 | 示例 | 说明 |
|---|---|---|
| `AWS_ROLE_TO_ASSUME` | `arn:aws:iam::625093290485:role/video-script-creator-github-actions-deploy` | GitHub Actions 要扮演的 AWS IAM Role |
| `APP_ACCESS_PASSWORD_SECRET_ARN` | `arn:aws:secretsmanager:eu-central-1:625093290485:secret:video-script-creator/app-access-password-xxxxxx` | 可选，访问密码 Secret ARN；当前若已关闭密码门禁，可先不填 |

## GitHub 可选 Variables

新增 Variables：

| 名称 | 默认值 |
|---|---|
| `AWS_REGION` | `eu-central-1` |
| `BEDROCK_AWS_REGION` | `eu-central-1` |
| `BEDROCK_MODEL_ID` | `eu.amazon.nova-pro-v1:0` |
| `BEDROCK_MAX_TOKENS` | `4096` |
| `STORAGE_BACKEND` | `s3` |
| `S3_BUCKET` | `video-script-creator-prod-assets-625093290485` |
| `S3_PREFIX` | `runtime` |
| `NOVA_REEL_AWS_REGION` | `us-east-1` |
| `NOVA_REEL_MODEL_ID` | `amazon.nova-reel-v1:1` |
| `NOVA_REEL_OUTPUT_S3_URI` | `s3://video-script-creator-prod-assets-625093290485/runtime/nova-reel-poc` |
| `NOVA_REEL_MAX_SUBMISSIONS_PER_CLICK` | `2` |

如果不配置 Variables，工作流会使用 `.github/workflows/deploy-ecs.yml` 中的默认值。

## AWS 侧一次性配置

需要在 AWS 中创建一个允许 GitHub Actions 扮演的 IAM Role。信任策略应限制到本仓库的 `main` 分支。

示例信任策略中的仓库名需要与 GitHub 仓库一致：

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Principal": {
        "Federated": "arn:aws:iam::625093290485:oidc-provider/token.actions.githubusercontent.com"
      },
      "Action": "sts:AssumeRoleWithWebIdentity",
      "Condition": {
        "StringEquals": {
          "token.actions.githubusercontent.com:aud": "sts.amazonaws.com"
        },
        "StringLike": {
          "token.actions.githubusercontent.com:sub": "repo:gaoc33721-ai/video-script-creator:ref:refs/heads/main"
        }
      }
    }
  ]
}
```

部署 Role 至少需要权限操作：

- ECR：创建仓库、登录、推送镜像。
- ECS：注册 task definition、创建/更新 service、查询状态。
- EC2/ELB：查询默认 VPC/Subnet/Security Group，创建/更新 ALB、Target Group、Listener。
- IAM：创建/更新 ECS task role、execution role、inline policy、pass role。
- S3：创建/读写平台 bucket。
- Secrets Manager：读取访问密码 Secret。
- CloudWatch Logs：创建日志组。
- Bedrock：文本模型调用和 Nova Reel 异步视频任务。

当前部署脚本会自动创建/更新部分 AWS 资源，所以这个 Role 需要比“只更新 ECS 服务”的权限更宽。正式生产化后可以把基础设施固定下来，再收窄权限。

## 推荐落地方式

第一阶段：

- 使用 GitHub OIDC + 一个部署 Role。
- push `main` 自动部署。
- GitHub Actions 保留部署日志，方便排查。

第二阶段：

- 把 AWS 基础设施改为 Terraform/CDK 管理。
- GitHub Actions 只负责构建镜像和更新 ECS task definition。
- 收窄 IAM 权限，避免部署 Role 长期拥有创建 IAM/ELB 的权限。

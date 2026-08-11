# LibTV 快乐马 1.1 生产配置

平台的视频生成任务固定使用 LibTV `Happy Horse 1.1`（模型键 `happy-horse-1.1`）。卖点图动效与已确认分镜的单镜头视频共用同一适配器；不自动切换 Nova Reel、Ray2、ToAPIs 或其他视频模型。

## 已创建的生产画布

- 名称：`视频脚本平台-快乐马1.1生产任务`
- UUID：`60ba07b049e74d6da4ef25f89e5d01c8`

## 发布前必需配置

GitHub Actions Variables：

- `LIBTV_CLI_ZIP_URL`：LibTV 官方 Linux x64 CLI ZIP 的固定下载地址。
- `LIBTV_CLI_SHA256`：上述 ZIP 的 SHA-256；镜像构建会校验，不一致立即失败。
- `LIBTV_PROJECT_UUID`：可省略，工作流默认使用上述生产画布。

GitHub Actions Secret：

- `LIBTV_CREDENTIALS_JSON`：本机 `LIBTV_CONFIG_DIR/credentials.json` 的完整 JSON。发布流程使用 OIDC 在 AWS Secrets Manager 中创建或更新 `${APP_NAME}/libtv-credentials`，再把 ARN 注入 ECS；凭据不得提交到 Git。
- `LIBTV_CREDENTIALS_SECRET_ARN`：可选。若已有 AWS Secret，可直接配置其 ARN，并不再上传 `LIBTV_CREDENTIALS_JSON`。

生产任务环境固定为：

- `VIDEO_PROVIDER=libtv_happy_horse_1_1`
- `LIBTV_HAPPY_HORSE_RESOLUTION=1080P`
- `LIBTV_HAPPY_HORSE_DURATION=5`

## 扣点与恢复规则

- 每个业务任务使用固定的 LibTV 节点名和任务 ID。
- 在调用 `libtv node --run` 前先持久化 `run_requested`。
- 同一个业务任务重复点击时复用原任务，不再次运行模型。
- 服务重启或结果不确定时只尝试下载原视频节点；不会自动再次执行并扣点。
- 缺少项目 UUID、凭据或指定模型时直接失败，不回退低质量模板或其他视频模型。

## 能力边界

快乐马 1.1 单次支持 3–15 秒。本平台固定生成 5 秒单镜头；整段长视频快捷入口已禁用，应先逐镜头生成并质检。长视频拼接不在本次范围内。

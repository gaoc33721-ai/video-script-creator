---
name: "vlog-generator"
description: "Generates viral vlog short videos from daily clips, adds BGM, edits, and outputs finished samples for platforms. Invoke when the user asks to create, edit, or generate a vlog/video from raw footage."
---

# 爆款 Vlog 自动剪辑生成器 (Vlog Generator)

这个 Skill 旨在帮助用户将 Action 4 等运动相机或手机拍摄的日常碎片视频，自动剪辑并生成为带有背景音乐的爆款 Vlog 短视频，最后协助输出成品并指导上传至各平台的草稿箱。

## 适用场景

当你想要将一堆零散的视频素材快速处理成具有网感、踩点音乐的 Vlog 时，调用此 Skill。

## 工作流 (Workflow)

### 1. 素材收集与偏好确认
- **获取素材路径**：向用户询问存储原始视频片段（如 mp4 文件）和背景音乐（BGM）的本地文件夹路径。
- **确认风格**：询问用户期望的视频风格（例如：快节奏踩点、治愈系慢生活、高燃运动风等），以及平台偏好（抖音、小红书、B站等，这会影响视频比例和输出规格）。

### 2. 生成剪辑脚本
- 编写 Python 脚本进行自动化剪辑。优先推荐使用 `moviepy` 等视频处理库。
- **核心剪辑逻辑**：
  - **筛选与裁剪**：自动舍弃过长或静止画面，保留每个片段中最精华的 2-3 秒。
  - **转场与拼接**：将多个片段流畅拼接，可以加入简单的淡入淡出（crossfade）转场效果。
  - **音频处理**：将原视频静音或降低音量，混入用户提供的 BGM，并根据 BGM 的时长自动截断或循环视频内容。
  - **格式与比例**：如果是竖屏短视频平台（抖音、小红书），通过裁剪（crop）或缩放填充（resize）将视频调整为 9:16 (例如 1080x1920)。

### 3. 环境配置与执行
- 检查并提示用户安装必要的依赖，如：
  ```bash
  pip install moviepy
  ```
- 运行脚本，并在工作目录生成 `vlog_output.mp4` 成品文件。

### 4. 平台发布与草稿箱对接
- **API 接口发布**：若目标平台（如抖音开放平台、B站 API）支持直接上传至草稿箱，生成相应的 Python API 调用脚本协助用户完成授权与上传。
- **自动化/手动引导**：若平台不支持直接的公开 API，提供基于 `playwright` 或 `selenium` 的自动化上传脚本示例，或提供详细的手动上传至草稿箱的步骤指南。

## 给 AI 的提示 (Instructions for AI)
- 在执行视频剪辑前，务必先确认用户本地是否已配置好 Python 与 FFmpeg 环境。
- 考虑到视频渲染耗时较长，请确保代码逻辑正确，并建议用户先用少量素材进行测试。
- 在处理“爆款模板”时，建议代码里预置几种节奏模式（如：每 2 秒切一个镜头的快剪模式）。
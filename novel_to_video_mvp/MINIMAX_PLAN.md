# Minimax + Doubao/Jimeng 网文转视频自动化方案

## 1. 方案架构 (Architecture)

本方案旨在利用 **Minimax (abab6.5)** 强大的长文本处理和角色扮演能力，将网文深度改编为分镜脚本，并生成适配 **剪映(Jimeng)/豆包(Doubao)** 的高质量提示词，最后合成视频。

### 核心流程
1.  **文本分析 (Text Analysis)**: 使用 Minimax 分析网文章节，提取核心角色(Characters)、场景(Environments)、氛围(Mood)。
2.  **分镜改编 (Storyboard Generation)**: 将小说文本转化为影视级分镜脚本 (Shot List)。
3.  **提示词工程 (Prompt Engineering)**: 针对“即梦(Jimeng)”或“豆包”视频生成模型，将分镜转化为优化的中文提示词 (Prompts)。
4.  **视频生成 (Video Generation)**:
    *   *方案 A (API自动化)*: 如果拥有火山引擎 (Volcengine) 企业权限，直接调用 API。
    *   *方案 B (人工/半自动)*: 导出标准 CSV/Excel 文件，在即梦/豆包网页版批量生成或逐个生成。
5.  **后期合成 (Post-Processing)**: 结合 Edge-TTS 生成旁白，使用 MoviePy 合成最终视频。

## 2. Minimax 调用策略

Minimax 的 `abab6.5` 系列模型在中文创作和语境理解上表现优异。我们将使用它完成以下任务：

*   **API Endpoint**: `https://api.minimax.chat/v1/text/chatcompletion_v2`
*   **Model**: `abab6.5s` (性价比高，适合大批量文本处理) 或 `abab6.5` (复杂逻辑)。

### Prompt 设计思路
*   **Role**: 设定为“资深电影导演”和“AIGC 提示词专家”。
*   **Task**: 
    1.  提取视觉元素（光影、镜头角度、动作）。
    2.  翻译为即梦(Jimeng) 喜欢的 Prompt 格式（如：`电影质感，8k分辨率，赛博朋克风格，...`）。

## 3. 视频生成工具 (Doubao/Jimeng)

鉴于目前豆包/即梦的视频生成 API 可能需要企业认证，本 MVP 采用 **"Prompt-First"** 策略：
*   **输出产物**: 生成一份包含 `分镜描述`、`视觉提示词(Prompt)`、`旁白(Voiceover)` 的结构化文件。
*   **使用方式**: 用户只需复制 `视觉提示词` 到即梦 App/网页版即可生成高质量视频。

## 4. 项目结构更新

我们将基于现有的 `novel_to_video_mvp` 进行扩展：

```text
novel_to_video_mvp/
├── src/
│   ├── minimax_client.py       # 新增: Minimax API 客户端
│   ├── prompt_optimizer.py     # 新增: 针对即梦的提示词优化器
│   └── ...
├── input/
│   └── novel.txt
├── output/
│   └── storyboards/            # 生成的分镜脚本和提示词列表
└── main_minimax.py             # 新增: 专门针对 Minimax 流程的启动入口
```

## 5. 下一步行动

1.  **配置环境**: 用户需要提供 `MINIMAX_API_KEY` 和 `GROUP_ID`。
2.  **代码实现**: 编写 Minimax 交互逻辑和 Prompt 优化逻辑。
3.  **执行测试**: 用一段网文测试 Prompt 生成质量。

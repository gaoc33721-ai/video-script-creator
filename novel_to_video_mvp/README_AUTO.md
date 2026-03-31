# 全自动网文转视频工作流

该脚本实现了从“网文”到“成品视频”的**全自动**转换。

## 功能特点
1.  **自动分镜**：调用 Minimax (abab6.5) 将网文拆解为分镜脚本。
2.  **自动视频生成**：
    *   ✅ **Minimax Video (推荐)**: 使用你现有的 Minimax Key 直接生成视频 (Video-01 模型)。
    *   🚧 **Jimeng/Volcengine**: 需要额外的火山引擎 API Key。
3.  **自动旁白**：使用 Edge-TTS 生成中文语音。
4.  **自动剪辑**：将生成的视频与音频自动对齐、拼接。

## 使用方法

### 1. 确保配置
确保 `.env` 文件中已有 `MINIMAX_API_KEY`。

### 2. 运行全自动脚本

**使用 Minimax 生成视频（默认，推荐）：**
```bash
python main_full_auto.py input/sample_novel.txt
```

**如果你有火山引擎 Key 并想用即梦：**
```bash
python main_full_auto.py input/sample_novel.txt --provider jimeng
```

## 输出产物
*   最终视频：`output/final_auto_movie.mp4`
*   分镜脚本：`output/prompts/auto_prompts.csv`
*   中间素材：`assets/video_clips/` 和 `assets/audio/`

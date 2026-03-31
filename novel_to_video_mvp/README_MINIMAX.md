MINIMAX_API_KEY=sk-api-Ziw71W_tn_E9-9ucL0jS0WlBeuoHPKZTzNv0klErXBQ-5ZMSaJOxIZ5TJlYzVnrjbt17rf6JTiQJB3rthU9ifIbMhXZ_8Br6aF6_M4J5JGehnPUVzTvqgvg
MINIMAX_GROUP_ID=2017165760928944618# Minimax to Jimeng/Doubao Workflow

This specific workflow leverages **Minimax (abab6.5)** for high-quality script adaptation and **Jimeng/Doubao** for video generation.

## 1. Setup

1.  **Get Minimax API Key**:
    *   Sign up at [Minimax Platform](https://platform.minimax.io/).
    *   Get your `API_KEY` and `GROUP_ID`.
2.  **Configure Environment**:
    *   Create a `.env` file in the root directory:
        ```env
        MINIMAX_API_KEY=your_api_key_here
        MINIMAX_GROUP_ID=your_group_id_here
        ```

## 2. Usage

Run the Minimax workflow script:

```bash
python main_minimax.py input/your_novel.txt
```

## 3. Workflow Steps

1.  **Script Generation**: The tool sends your novel text to Minimax, which acts as a director to break it down into scenes (shots).
2.  **Prompt Optimization**: It automatically adds keywords like "8k resolution", "cinematic lighting", etc., and translates concepts into visual descriptions suitable for Jimeng.
3.  **Export**:
    *   Find the generated prompts in `output/prompts/jimeng_prompts.csv`.
    *   Find the generated voiceovers in `assets/audio/`.

## 4. Video Generation (Manual Step)

Since Jimeng/Doubao video generation is currently best accessed via their App/Web interface for free users:

1.  Open **Jimeng (即梦)** or **Doubao (豆包)**.
2.  Open `output/prompts/jimeng_prompts.csv`.
3.  For each scene:
    *   Copy the `optimized_prompt` column content.
    *   Paste into the "Image to Video" or "Text to Video" input box in the app.
    *   Download the generated video.
4.  Save the videos to `assets/video_clips/` as `scene_1.mp4`, `scene_2.mp4`, etc.

## 5. Final Assembly

(Coming Soon) A script to stitch the manually generated videos with the automatically generated audio.

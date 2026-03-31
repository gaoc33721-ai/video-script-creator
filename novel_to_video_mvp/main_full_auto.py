import asyncio
import os
import sys
import json
import csv
from dotenv import load_dotenv

# Import modules
from src.minimax_client import MinimaxClient
from src.prompt_optimizer import PromptOptimizer
from src.audio_generator import AudioGenerator
from src.video_generator_client import MinimaxVideoGenerator, JimengVideoGenerator, SiliconFlowVideoGenerator
from src.image_generator_client import SiliconFlowImageGenerator # New
from src.video_editor import VideoEditor
from assemble_videos import VideoAssembler # Reuse our assembler

# Load environment variables
load_dotenv()

async def run_full_automation(novel_text_file: str, video_provider="minimax"):
    """
    Fully automated pipeline: Novel -> Minimax Script -> Video Gen (API) -> Audio -> Assembly
    """
    print(f"🚀 启动全自动网文转视频流程 ({video_provider.upper()})")
    print(f"📄 输入文件: {novel_text_file}")
    
    # 1. Environment Check
    api_key = os.getenv("MINIMAX_API_KEY")
    group_id = os.getenv("MINIMAX_GROUP_ID")
    silicon_key = os.getenv("SILICONFLOW_API_KEY")
    
    if not api_key:
        print("❌ Error: MINIMAX_API_KEY not found.")
        return

    # 2. Read Novel Text
    try:
        with open(novel_text_file, 'r', encoding='utf-8') as f:
            novel_text = f.read()
            if len(novel_text) > 2000:
                print(f"⚠️ 文本过长，截取前 2000 字进行测试...")
                novel_text = novel_text[:2000]
    except FileNotFoundError:
        print(f"❌ Error: File '{novel_text_file}' not found.")
        return

    # 3. Script Generation (Minimax)
    print("\n📝 [1/4] 正在分析文本并生成分镜脚本...")
    minimax_client = MinimaxClient(api_key, group_id)
    scenes = minimax_client.adapt_novel_to_script(novel_text)
    
    if not scenes:
        print("❌ 脚本生成失败，终止流程。")
        return
    print(f"✅ 生成了 {len(scenes)} 个分镜。")

    # 4. Prompt Optimization
    optimizer = PromptOptimizer(platform="jimeng") # Optimization style
    optimized_scenes = optimizer.optimize_prompts(scenes)
    
    # Export for record
    os.makedirs("output/prompts", exist_ok=True)
    optimizer.export_to_csv(optimized_scenes, "output/prompts/auto_prompts.csv")

    # 5. Parallel Generation: Audio & Video
    print("\n🎬 [2/4] 开始并行生成 视频/图片 和 音频...")
    
    # Initialize Generators
    audio_gen = AudioGenerator()
    
    video_gen = None
    image_gen = None
    
    if video_provider == "minimax":
        video_gen = MinimaxVideoGenerator(api_key, group_id)
    elif video_provider == "jimeng":
        video_gen = JimengVideoGenerator() # Will check VOLCENGINE_API_KEY inside
    elif video_provider == "siliconflow":
        if not silicon_key:
            print("❌ Error: SILICONFLOW_API_KEY not found.")
            return
        video_gen = SiliconFlowVideoGenerator(silicon_key)
    elif video_provider == "image": # New Image Mode
        if not silicon_key:
            print("❌ Error: SILICONFLOW_API_KEY not found for image generation.")
            return
        image_gen = SiliconFlowImageGenerator(silicon_key)
    else:
        print(f"❌ Unknown provider: {video_provider}")
        return

    os.makedirs("assets/audio", exist_ok=True)
    os.makedirs("assets/video_clips", exist_ok=True)
    
    tasks = []
    
    for scene in optimized_scenes:
        scene_id = scene['scene_id']
        voiceover = scene['voiceover']
        # Use optimized prompt
        visual_prompt = scene['optimized_prompt'] 
        
        audio_path = os.path.join("assets/audio", f"scene_{scene_id}.mp3")
        
        print(f"\n--- 处理场景 {scene_id} ---")
        
        # Audio Task (Run sequentially to avoid rate limits or overlap)
        # Using await directly instead of gather for better flow control in MVP
        await audio_gen.generate_audio(voiceover, audio_path)
        
        # Video/Image Task
        if image_gen:
            # Image Mode
            image_path = os.path.join("assets/video_clips", f"scene_{scene_id}.png")
            # Run in thread executor if blocking, but here it's sync HTTP
            # Ideally wrap in async, but for now direct call is fine for MVP speed
            image_gen.generate_image(visual_prompt, image_path)
        elif video_gen:
            # Video Mode
            video_path = os.path.join("assets/video_clips", f"scene_{scene_id}.mp4")
            await video_gen.generate_video(visual_prompt, video_path)

    # 6. Assembly
    print("\n🎞️ [3/4] 开始合成最终视频...")
    assembler = VideoAssembler()
    # Ensure assembler looks for the right files (it looks in assets/video_clips by default)
    # And uses the prompt file we just saved
    assembler.prompts_file = "output/prompts/auto_prompts.csv" 
    assembler.assemble("final_auto_movie.mp4")
    
    print("\n✅✅✅ 全流程结束！")

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Auto Novel to Video")
    parser.add_argument("file", help="Path to novel text file")
    parser.add_argument("--provider", default="minimax", choices=["minimax", "jimeng", "siliconflow", "image"], help="Video generation provider")
    
    args = parser.parse_args()
    
    if len(sys.argv) < 2:
        # Default to siliconflow if no args provided for convenience
        pass
        
    if len(sys.argv) == 2 and not sys.argv[1].startswith("-"):
        # User only provided filename
        provider = "siliconflow" if os.getenv("SILICONFLOW_API_KEY") else "minimax"
        asyncio.run(run_full_automation(sys.argv[1], provider))
    else:
        # Use argparse
        try:
            if len(sys.argv) < 2:
                print("Usage: python main_full_auto.py <novel_file> [--provider minimax|jimeng|siliconflow|image]")
            else:
                args = parser.parse_args()
                asyncio.run(run_full_automation(args.file, args.provider))
        except SystemExit:
            pass

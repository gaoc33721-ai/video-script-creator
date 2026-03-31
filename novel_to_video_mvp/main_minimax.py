import asyncio
import os
import sys
import json
import csv
from dotenv import load_dotenv

# Import our new modules
from src.minimax_client import MinimaxClient
from src.prompt_optimizer import PromptOptimizer
from src.audio_generator import AudioGenerator
from src.video_editor import VideoEditor

# Load environment variables
load_dotenv()

async def process_novel_with_minimax(novel_text_file: str):
    """
    Main entry point for Minimax -> Doubao workflow.
    """
    print(f"🎬 Starting Minimax-Driven Novel Conversion: {novel_text_file}")
    
    # 1. Environment Check
    api_key = os.getenv("MINIMAX_API_KEY")
    group_id = os.getenv("MINIMAX_GROUP_ID")
    
    if not api_key or not group_id:
        print("⚠️  Warning: MINIMAX_API_KEY or MINIMAX_GROUP_ID not found in .env file.")
        print("Please add them to .env to use the actual API.")
        print("Example: MINIMAX_API_KEY=eyJ...")
        print("Using Mock Data for demonstration if API fails...")
        
    # 2. Read Novel Text
    try:
        with open(novel_text_file, 'r', encoding='utf-8') as f:
            novel_text = f.read()
            if len(novel_text) > 2000: # Simple chunking for demo
                print(f"Text too long ({len(novel_text)} chars), truncating to first 2000 chars for demo.")
                novel_text = novel_text[:2000]
    except FileNotFoundError:
        print(f"❌ Error: File '{novel_text_file}' not found.")
        return

    # 3. Minimax Processing (Script Generation)
    minimax_client = MinimaxClient(api_key, group_id)
    print("🤖 Calling Minimax to adapt novel into storyboard...")
    
    try:
        scenes = minimax_client.adapt_novel_to_script(novel_text)
    except Exception as e:
        print(f"❌ Minimax API call failed: {e}")
        scenes = []

    if not scenes:
        print("⚠️  No scenes generated from Minimax. Falling back to mock data.")
        scenes = [
            {"scene_id": 1, "voiceover": "测试场景1", "visual_prompt": "测试画面1"},
            {"scene_id": 2, "voiceover": "测试场景2", "visual_prompt": "测试画面2"}
        ]

    print(f"✅ Generated {len(scenes)} scenes.")

    # 4. Prompt Optimization (For Doubao/Jimeng)
    optimizer = PromptOptimizer(platform="jimeng")
    optimized_scenes = optimizer.optimize_prompts(scenes)
    
    # 5. Export Prompts (Crucial Step for Manual Video Gen)
    os.makedirs("output/prompts", exist_ok=True)
    prompt_csv_path = "output/prompts/jimeng_prompts.csv"
    optimizer.export_to_csv(optimized_scenes, prompt_csv_path)
    print(f"📝 Prompts exported to: {prompt_csv_path}")
    print("👉 Use this CSV to batch generate videos in Jimeng/Doubao if supported, or copy-paste prompts manually.")

    # 6. Audio Generation (Automatic)
    print("🔊 Generating Audio (Voiceovers)...")
    audio_gen = AudioGenerator()
    os.makedirs("assets/audio", exist_ok=True)
    
    for scene in optimized_scenes:
        text = scene.get('voiceover', '')
        scene_id = scene.get('scene_id')
        audio_path = os.path.join("assets/audio", f"scene_{scene_id}.mp3")
        
        if text:
            await audio_gen.generate_audio(text, audio_path)
            scene['audio_path'] = audio_path
        else:
            print(f"Skipping audio for scene {scene_id} (no text)")

    # 7. Video Assembly Placeholder
    # Since we don't have the actual video files from Jimeng yet, we can't assemble.
    # But we can prepare the project structure.
    print("🚧 Video Assembly Step:")
    print("   Please generate videos using the prompts in 'output/prompts/jimeng_prompts.csv'.")
    print("   Save the generated video clips to 'assets/video_clips/' named as 'scene_1.mp4', 'scene_2.mp4', etc.")
    print("   Then run a separate assembly script (e.g., 'python assemble_videos.py').")

if __name__ == "__main__":
    if len(sys.argv) < 2:
        # Create a dummy file for testing if none provided
        dummy_file = "input/sample_novel.txt"
        os.makedirs("input", exist_ok=True)
        if not os.path.exists(dummy_file):
            with open(dummy_file, "w", encoding="utf-8") as f:
                f.write("夜色如墨，古老的剑身在月光下泛着寒光。少年缓缓抬起头，眼神中透着坚毅。")
        print(f"Using default sample file: {dummy_file}")
        asyncio.run(process_novel_with_minimax(dummy_file))
    else:
        asyncio.run(process_novel_with_minimax(sys.argv[1]))

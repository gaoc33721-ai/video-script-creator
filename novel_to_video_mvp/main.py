import asyncio
import os
import sys
from src.script_generator import ScriptGenerator
from src.audio_generator import AudioGenerator
from src.image_generator import ImageGenerator
from src.video_editor import VideoEditor

async def process_novel(novel_text_file: str):
    print(f"Starting conversion for: {novel_text_file}")
    
    # 1. Read input
    try:
        with open(novel_text_file, 'r', encoding='utf-8') as f:
            novel_text = f.read()
    except FileNotFoundError:
        print(f"Error: File '{novel_text_file}' not found.")
        return

    # 2. Generate Script
    script_gen = ScriptGenerator()
    scenes = script_gen.generate_script(novel_text)
    print(f"Generated {len(scenes)} scenes.")

    # 3. Generate Assets (Audio & Images)
    audio_gen = AudioGenerator()
    image_gen = ImageGenerator(output_dir="assets/images")
    video_scenes = []

    # Ensure output directories exist
    os.makedirs("assets/audio", exist_ok=True)
    os.makedirs("assets/images", exist_ok=True)

    for i, scene in enumerate(scenes):
        scene_id = scene['scene_id']
        text = scene['text']
        prompt = scene['prompt']
        
        print(f"Processing Scene {scene_id}...")
        
        # Audio
        audio_path = os.path.join("assets/audio", f"scene_{scene_id}.mp3")
        await audio_gen.generate_audio(text, audio_path)
        
        # Image
        image_path = image_gen.generate_image(prompt, scene_id)
        
        if audio_path and image_path:
            video_scenes.append({
                "scene_id": scene_id,
                "text": text,
                "audio_path": audio_path,
                "image_path": image_path
            })
        else:
            print(f"Skipping Scene {scene_id} due to generation failure.")

    # 4. Assemble Video
    if video_scenes:
        editor = VideoEditor(output_dir="output")
        final_video_path = editor.assemble_video(video_scenes, output_filename="novel_video.mp4")
        if final_video_path:
            print(f"✅ Success! Video created at: {final_video_path}")
    else:
        print("❌ Failed to generate any valid scenes for video assembly.")

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python main.py <path_to_novel_text_file>")
        # Create a dummy file for testing if none provided
        dummy_file = "input/sample_novel.txt"
        os.makedirs("input", exist_ok=True)
        if not os.path.exists(dummy_file):
            with open(dummy_file, "w", encoding="utf-8") as f:
                f.write("This is a sample novel text for demonstration purposes.")
        print(f"Using default sample file: {dummy_file}")
        asyncio.run(process_novel(dummy_file))
    else:
        asyncio.run(process_novel(sys.argv[1]))

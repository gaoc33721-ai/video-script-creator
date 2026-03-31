import os
import csv
from moviepy import VideoFileClip, AudioFileClip, ImageClip, concatenate_videoclips

class VideoAssembler:
    def __init__(self, base_dir="."):
        self.base_dir = base_dir
        self.video_dir = os.path.join(base_dir, "assets", "video_clips")
        self.audio_dir = os.path.join(base_dir, "assets", "audio")
        self.output_dir = os.path.join(base_dir, "output")
        self.prompts_file = os.path.join(base_dir, "output", "prompts", "jimeng_prompts.csv")
        
        os.makedirs(self.output_dir, exist_ok=True)

    def assemble(self, output_filename="final_movie.mp4"):
        print("🎬 开始合成视频...")
        
        scenes = []
        if os.path.exists(self.prompts_file):
            with open(self.prompts_file, 'r', encoding='utf-8-sig') as f:
                reader = csv.DictReader(f)
                for row in reader:
                    scenes.append(row)
        else:
            print(f"⚠️ 未找到提示词文件: {self.prompts_file}，将尝试直接扫描目录。")
            return

        final_clips = []
        
        for scene in scenes:
            scene_id = scene['scene_id']
            video_path = os.path.join(self.video_dir, f"scene_{scene_id}.mp4")
            image_path = os.path.join(self.video_dir, f"scene_{scene_id}.png") # Support images
            audio_path = os.path.join(self.audio_dir, f"scene_{scene_id}.mp3")
            
            # 1. Determine visual source (Video > Image)
            clip = None
            if os.path.exists(video_path):
                try:
                    clip = VideoFileClip(video_path)
                except Exception as e:
                    print(f"❌ 视频损坏 {video_path}: {e}")
            elif os.path.exists(image_path):
                try:
                    # Create static clip from image, duration will be set later
                    clip = ImageClip(image_path)
                except Exception as e:
                    print(f"❌ 图片损坏 {image_path}: {e}")
            else:
                print(f"❌ 缺少视觉素材 (Video/Image) 场景 {scene_id}")
                continue

            # 2. Load Audio
            audio_clip = None
            if os.path.exists(audio_path):
                try:
                    audio_clip = AudioFileClip(audio_path)
                except Exception as e:
                    print(f"⚠️ 音频出错 {audio_path}: {e}")
            else:
                print(f"⚠️ 缺少音频 {scene_id}")

            try:
                # 3. Combine Visual + Audio
                if audio_clip:
                    duration = audio_clip.duration
                    
                    if isinstance(clip, ImageClip):
                        # For Image: Set duration to match audio
                        clip = clip.with_duration(duration)
                        # Optional: Add simple resize effect (Ken Burns) could be added here
                        # For now, static image
                    else:
                        # For Video: Loop or Cut
                        if clip.duration < duration:
                            loops = int(duration / clip.duration) + 1
                            clip = concatenate_videoclips([clip] * loops).subclipped(0, duration)
                        else:
                            clip = clip.subclipped(0, duration)
                    
                    clip = clip.with_audio(audio_clip)
                else:
                    # No audio: Default duration for image
                    if isinstance(clip, ImageClip):
                        clip = clip.with_duration(3) # Default 3s
                
                final_clips.append(clip)
                print(f"✅ 已处理场景 {scene_id}")
                
            except Exception as e:
                print(f"❌ 合成场景 {scene_id} 失败: {e}")

        if not final_clips:
            print("❌ 没有有效片段。")
            return

        print("🔨 正在拼接...")
        try:
            final_video = concatenate_videoclips(final_clips, method="compose")
            output_path = os.path.join(self.output_dir, output_filename)
            final_video.write_videofile(output_path, fps=24, codec='libx264', audio_codec='aac')
            print(f"🎉 视频保存至: {output_path}")
        except Exception as e:
            print(f"❌ 最终保存失败: {e}")

if __name__ == "__main__":
    assembler = VideoAssembler()
    assembler.assemble()

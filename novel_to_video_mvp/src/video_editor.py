from moviepy import ImageClip, AudioFileClip, concatenate_videoclips, TextClip, CompositeVideoClip
import os

class VideoEditor:
    def __init__(self, output_dir: str = "output"):
        self.output_dir = output_dir
        if not os.path.exists(output_dir):
            os.makedirs(output_dir)

    def assemble_video(self, scenes: list, output_filename: str = "final_video.mp4") -> str:
        """
        Assembles video clips from scenes.
        Each scene dict should have: 'image_path', 'audio_path', 'text' (optional for subtitles)
        """
        clips = []
        
        for scene in scenes:
            image_path = scene.get('image_path')
            audio_path = scene.get('audio_path')
            
            if not image_path or not audio_path:
                print(f"Skipping scene due to missing files: {scene}")
                continue
                
            try:
                # Create audio clip
                audio_clip = AudioFileClip(audio_path)
                duration = audio_clip.duration
                
                # Create image clip with same duration
                image_clip = ImageClip(image_path).with_duration(duration)
                
                # Combine image and audio
                video_clip = image_clip.with_audio(audio_clip)
                
                # TODO: Add subtitles using TextClip (requires ImageMagick)
                # For now, just return the video clip
                clips.append(video_clip)
                
            except Exception as e:
                print(f"Error processing scene {scene}: {e}")
        
        if not clips:
            print("No clips to assemble.")
            return None
            
        final_clip = concatenate_videoclips(clips, method="compose")
        output_path = os.path.join(self.output_dir, output_filename)
        final_clip.write_videofile(output_path, fps=24, codec='libx264', audio_codec='aac')
        
        print(f"Video saved to: {output_path}")
        return output_path

if __name__ == "__main__":
    # Test with dummy files (requires actual files to run)
    pass

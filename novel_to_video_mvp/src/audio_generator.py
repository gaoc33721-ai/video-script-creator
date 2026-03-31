import asyncio
import edge_tts
import os
import random

class AudioGenerator:
    def __init__(self, voice="zh-CN-YunxiNeural", rate="+0%"):
        # Changed default voice to Chinese (Yunxi - Male, popular for narration)
        self.voice = voice
        self.rate = rate

    async def generate_audio(self, text: str, output_file: str, retries=3):
        """
        Generates audio from text using Edge TTS.
        Saves to output_file.
        """
        if not text or not text.strip():
            print(f"⚠️ Skipping audio generation: Text is empty for {output_file}")
            return None

        for attempt in range(retries):
            try:
                print(f"🎙️ Generating audio for: '{text[:15]}...' using {self.voice} (Attempt {attempt+1})")
                communicate = edge_tts.Communicate(text, self.voice, rate=self.rate)
                await communicate.save(output_file)
                
                if os.path.exists(output_file) and os.path.getsize(output_file) > 0:
                    print(f"✅ Audio saved to: {output_file}")
                    return output_file
                else:
                    print(f"❌ Audio file created but empty: {output_file}")
                    
            except Exception as e:
                print(f"❌ Error generating audio for '{text[:10]}...' (Attempt {attempt+1}): {e}")
                # Wait before retry
                await asyncio.sleep(random.uniform(1, 3))
        
        print(f"❌ Failed to generate audio after {retries} attempts.")
        return None

if __name__ == "__main__":
    generator = AudioGenerator()
    asyncio.run(generator.generate_audio("你好，这是一个音频生成测试。", "test_audio.mp3"))

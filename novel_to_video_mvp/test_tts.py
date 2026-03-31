import asyncio
import edge_tts

async def test_tts():
    text = "你好，这是一个测试。"
    voice = "zh-CN-YunxiNeural"
    output = "test_audio_zh.mp3"
    try:
        print(f"Testing TTS with voice: {voice}")
        communicate = edge_tts.Communicate(text, voice)
        await communicate.save(output)
        print(f"Success! Saved to {output}")
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    asyncio.run(test_tts())

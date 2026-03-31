import os
import requests
import time
import base64

class SiliconFlowImageGenerator:
    def __init__(self, api_key: str):
        self.api_key = api_key
        # SiliconFlow Image Generation Endpoint
        self.base_url = "https://api.siliconflow.cn/v1/images/generations"
        # Using FLUX.1-schnell (Fast, cheap, high quality)
        self.model = "black-forest-labs/FLUX.1-schnell"

    def generate_image(self, prompt: str, output_path: str) -> str:
        """
        Generates an image from text using SiliconFlow (Flux).
        Returns path to saved image.
        """
        print(f"🎨 [SiliconFlow] Generating image: {prompt[:30]}...")
        
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json"
        }
        
        # Flux usually works best with English prompts, but handles Chinese okay-ish.
        # Ideally we should translate, but for MVP let's try direct.
        # Or Minimax might have already optimized it to include English keywords? 
        # The prompt optimizer added "国漫风格" etc. Flux might need English for best results.
        # But let's try.
        
        payload = {
            "model": self.model,
            "prompt": prompt,
            "image_size": "1024x1024", # Or 16:9 like "1280x720" if supported, Flux supports flexible resolutions
            "num_inference_steps": 4, # Schnell is fast
            "seed": int(time.time())
        }
        
        try:
            response = requests.post(self.base_url, headers=headers, json=payload)
            
            if response.status_code != 200:
                print(f"❌ Image Gen Failed: {response.text}")
                return None
                
            data = response.json()
            # SiliconFlow usually returns OpenAI-compatible format: data[0].url
            
            if 'data' in data and len(data['data']) > 0:
                image_url = data['data'][0]['url']
                self._download_file(image_url, output_path)
                return output_path
            else:
                print(f"❌ Unexpected response: {data}")
                return None

        except Exception as e:
            print(f"❌ Image Gen Error: {e}")
            return None

    def _download_file(self, url: str, path: str):
        try:
            r = requests.get(url)
            with open(path, 'wb') as f:
                f.write(r.content)
            print(f"🖼️ Image saved to: {path}")
        except Exception as e:
            print(f"❌ Failed to download image: {e}")

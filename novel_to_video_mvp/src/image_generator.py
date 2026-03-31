import requests
import os
import urllib.parse
from PIL import Image, ImageDraw, ImageFont
import random

class ImageGenerator:
    def __init__(self, output_dir: str = "assets/images"):
        self.output_dir = output_dir
        if not os.path.exists(output_dir):
            os.makedirs(output_dir)

    def generate_image(self, prompt: str, scene_id: int) -> str:
        """
        Generates an image using Pollinations.ai (Free) based on the prompt.
        If that fails, falls back to a placeholder image.
        Returns the path to the saved image file.
        """
        # URL encode the prompt
        encoded_prompt = urllib.parse.quote(prompt)
        url = f"https://image.pollinations.ai/prompt/{encoded_prompt}"
        
        output_file = os.path.join(self.output_dir, f"scene_{scene_id}.jpg")
        
        try:
            print(f"Generating image for scene {scene_id} with prompt: {prompt[:30]}...")
            response = requests.get(url, timeout=30)
            if response.status_code == 200:
                with open(output_file, 'wb') as f:
                    f.write(response.content)
                print(f"Image saved to: {output_file}")
                return output_file
            else:
                print(f"Failed to generate image (Status: {response.status_code}). Using placeholder.")
                return self.generate_placeholder(prompt, scene_id)
        except Exception as e:
            print(f"Error generating image: {e}. Using placeholder.")
            return self.generate_placeholder(prompt, scene_id)

    def generate_placeholder(self, prompt: str, scene_id: int) -> str:
        """Generates a placeholder image with random color and text."""
        width, height = 1280, 720
        color = (random.randint(0, 255), random.randint(0, 255), random.randint(0, 255))
        img = Image.new('RGB', (width, height), color=color)
        d = ImageDraw.Draw(img)
        
        # Try to load a font, fallback to default
        try:
            # Windows usually has arial.ttf
            font = ImageFont.truetype("arial.ttf", 40)
        except IOError:
            font = ImageFont.load_default()

        text = f"Scene {scene_id}\n{prompt[:50]}..."
        # Center text roughly
        d.text((100, 300), text, fill=(255, 255, 255), font=font)
        
        output_file = os.path.join(self.output_dir, f"scene_{scene_id}_placeholder.jpg")
        img.save(output_file)
        print(f"Placeholder image saved to: {output_file}")
        return output_file

if __name__ == "__main__":
    generator = ImageGenerator()
    generator.generate_image("A futuristic city at sunset, cyberpunk style", 999)

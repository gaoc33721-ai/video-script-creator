import json
import os
from typing import List, Dict

# Mock data for demonstration purposes if no API key is provided
MOCK_SCRIPT = [
    {
        "scene_id": 1,
        "text": "The ancient sword gleamed in the moonlight, its blade etched with runes of power.",
        "prompt": "Ancient sword glowing blue in moonlight, intricate runes on blade, dark forest background, cinematic lighting, fantasy art style, 8k resolution"
    },
    {
        "scene_id": 2,
        "text": "A shadowed figure emerged from the mist, eyes burning like embers.",
        "prompt": "Mysterious hooded figure stepping out of thick mist, glowing red eyes, dark fantasy atmosphere, dramatic lighting, detailed character design"
    },
    {
        "scene_id": 3,
        "text": "The ground trembled as the dragon awoke from its thousand-year slumber.",
        "prompt": "Massive dragon eye opening, scales texture detailed, cave interior with gold coins, epic scale, dynamic angle, high fantasy style"
    }
]

class ScriptGenerator:
    def __init__(self, api_key: str = None):
        self.api_key = api_key

    def generate_script(self, text: str) -> List[Dict]:
        """
        Generates a script from the input text.
        Returns a list of dictionaries with 'scene_id', 'text', and 'prompt'.
        """
        if not self.api_key:
            print("No API key provided. Using mock script for demonstration.")
            return MOCK_SCRIPT
        
        # TODO: Implement actual OpenAI call here
        # prompt = f"Convert this novel text into a video script with visual prompts: {text}"
        # response = openai.ChatCompletion.create(...)
        # return parse_response(response)
        
        return MOCK_SCRIPT

if __name__ == "__main__":
    generator = ScriptGenerator()
    script = generator.generate_script("Test novel text")
    print(json.dumps(script, indent=2))

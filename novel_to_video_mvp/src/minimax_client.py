import requests
import json
import os
import re

class MinimaxClient:
    def __init__(self, api_key: str, group_id: str):
        self.api_key = api_key
        self.group_id = group_id
        # Standard MiniMax endpoint for abab6.5
        self.base_url = "https://api.minimax.chat/v1/text/chatcompletion_v2"
        self.model = "abab6.5s-chat" # Using 's' for speed and cost, switch to 'abab6.5-chat' for complexity

    def adapt_novel_to_script(self, novel_text: str) -> list:
        """
        Adapts a novel chapter into a sequence of video scenes (shots).
        Returns a list of dicts: { "scene_id": 1, "voiceover": "...", "visual_prompt": "..." }
        """
        system_prompt = """
        你是一位资深电影导演和分镜画师。你的任务是将一段中文网文改编为高质量的短视频分镜脚本。
        
        核心原则：
        1.  **原汁原味 (Faithfulness)**：
            *   **绝对禁止**将角色的精彩台词概括为陈述句。
            *   **必须保留**原著中有趣、搞笑或关键的对话作为旁白。
            *   例如：原文是“老头，这是哪儿？”，旁白必须包含“老头，这是哪儿？”，绝不能写成“王德发问这是哪里”。
        2.  **角色一致性 (Character Consistency)**：
            *   提取主角的核心外貌特征（如：黑发束冠，身穿现代T恤/破旧灰袍）。
            *   在每个场景的 visual_prompt 中**必须重复**这些特征。
        3.  **视觉描述 (Visual Prompt)**：
            *   风格：统一为国产动漫风格（类似斗破苍穹、斗罗大陆），3D渲染，色彩鲜艳明快。
            *   描述需适合 AI 生图/视频工具，使用短语。

        JSON 示例：
        [
            {
                "scene_id": 1,
                "voiceover": "王德发醒来，看着眼前的老头，脱口而出：“老头，这是哪儿？你们在开老年大学？”",
                "visual_prompt": "特写，王德发（现代青年，短发，T恤牛仔裤）一脸懵逼，看着对面的白发老者，国产动漫风格，3D渲染"
            },
            {
                "scene_id": 2,
                "voiceover": "满堂大能瞬间石化。鸿钧愣了三秒，抬手就要灭了这个蝼蚁。",
                "visual_prompt": "全景，紫霄宫内，众仙（古风装束）表情震惊，鸿钧（白发老道）手停在半空，面带怒色，国产动漫风格，3D渲染"
            }
        ]
        """

        user_prompt = f"请将以下网文片段改编为分镜脚本，严格保留原著台词：\n\n{novel_text}"

        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "name": "MM Intelligent Assistant", "content": system_prompt},
                {"role": "user", "name": "User", "content": user_prompt}
            ],
            "tokens_to_generate": 4096, # Increased limit
            "max_tokens": 4096, # Added max_tokens for compatibility
            "temperature": 0.7,
            "top_p": 0.95,
        }

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json"
        }

        try:
            print("Sending request to Minimax API...")
            response = requests.post(self.base_url + f"?GroupId={self.group_id}", headers=headers, json=payload)
            response.raise_for_status()
            
            result = response.json()
            
            if 'choices' in result and len(result['choices']) > 0:
                content = result['choices'][0]['message']['content']
                # Clean up markdown code blocks if present
                content = self._clean_json_markdown(content)
                
                # Try to parse JSON directly
                try:
                    return json.loads(content)
                except json.JSONDecodeError:
                    print("⚠️ JSON truncated or invalid. Attempting to repair...")
                    return self._repair_json_list(content)
            else:
                print(f"Unexpected response format: {result}")
                return []
                
        except Exception as e:
            print(f"Error calling Minimax API: {e}")
            if 'response' in locals() and response:
                print(f"Response text: {response.text}")
            return []

    def _clean_json_markdown(self, text: str) -> str:
        """Helper to remove markdown code blocks from LLM response"""
        pattern = r"```json\s*(.*?)\s*```"
        match = re.search(pattern, text, re.DOTALL)
        if match:
            return match.group(1)
        return text.strip()

    def _repair_json_list(self, text: str) -> list:
        """
        Attempts to extract valid JSON objects from a truncated list string.
        """
        # Find the last closing brace '}'
        last_brace_index = text.rfind('}')
        if last_brace_index == -1:
            return []
            
        # Cut off everything after the last '}'
        truncated_text = text[:last_brace_index+1]
        
        # Ensure it ends with ']'
        if not truncated_text.strip().endswith(']'):
            truncated_text += ']'
            
        try:
            return json.loads(truncated_text)
        except json.JSONDecodeError:
            # If simply adding ']' didn't fix it (e.g. comma at end), try removing trailing comma
            truncated_text = text[:last_brace_index+1]
            # Remove trailing comma if exists
            if truncated_text.strip().endswith(','):
                 truncated_text = truncated_text.strip()[:-1]
            
            truncated_text += ']'
            try:
                return json.loads(truncated_text)
            except:
                print("❌ Failed to repair JSON.")
                return []

if __name__ == "__main__":
    # Test (Need actual keys to work)
    client = MinimaxClient("test_key", "test_group")
    # print(client.adapt_novel_to_script("测试文本"))

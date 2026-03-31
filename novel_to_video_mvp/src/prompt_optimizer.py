import json
import csv
import os

class PromptOptimizer:
    def __init__(self, platform="jimeng"):
        self.platform = platform
        # Common negative prompts for AI Video
        self.negative_prompt = "nsfw, low quality, bad anatomy, ugly, text, watermark, logo, bad proportions, missing limbs, extra limbs"

    def optimize_prompts(self, scenes: list) -> list:
        """
        Takes raw storyboard scenes and optimizes prompts for video generation.
        Adds quality boosters and style keywords.
        """
        optimized_scenes = []
        for scene in scenes:
            raw_prompt = scene.get('visual_prompt', '')
            
            # 1. Enhance with quality keywords (国漫/3D动漫风格)
            # 关键词：国漫风格, 3D动画, 动漫渲染, 类似斗破苍穹, 类似斗罗大陆, 虚幻引擎5, 赛璐璐风格, 鲜艳明快
            style_keywords = "国漫风格, 3D动漫渲染, 虚幻引擎5, 类似斗罗大陆画风, 角色建模精致, 动作流畅, 色彩鲜艳明快, 高饱和度"
            quality_boosters = "最高画质, 8k分辨率, 电影级光影, 细节丰富, 杰作, 极高清晰度"
            
            # 2. Combine: 风格词放在前面以强调风格
            full_prompt = f"{style_keywords}, {raw_prompt}, {quality_boosters}"
            
            scene['optimized_prompt'] = full_prompt
            scene['negative_prompt'] = self.negative_prompt
            optimized_scenes.append(scene)
            
        return optimized_scenes

    def export_to_csv(self, scenes: list, output_file: str):
        """
        Exports scenes to a CSV file suitable for batch import or manual copy-paste.
        Columns: Scene ID, Voiceover, Optimized Prompt, Negative Prompt
        """
        if not scenes:
            print("No scenes to export.")
            return

        keys = ['scene_id', 'voiceover', 'optimized_prompt', 'negative_prompt']
        
        with open(output_file, 'w', newline='', encoding='utf-8-sig') as f: # utf-8-sig for Excel compatibility
            writer = csv.DictWriter(f, fieldnames=keys, extrasaction='ignore')
            writer.writeheader()
            writer.writerows(scenes)
        
        print(f"Exported optimized prompts to: {output_file}")

if __name__ == "__main__":
    optimizer = PromptOptimizer()
    sample_scenes = [
        {"scene_id": 1, "visual_prompt": "一个孤独的背影", "voiceover": "测试"},
        {"scene_id": 2, "visual_prompt": "赛博朋克城市全景", "voiceover": "测试2"}
    ]
    optimized = optimizer.optimize_prompts(sample_scenes)
    optimizer.export_to_csv(optimized, "test_prompts.csv")

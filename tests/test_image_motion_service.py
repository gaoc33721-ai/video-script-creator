import io
import unittest

from PIL import Image, ImageDraw

from image_motion_service import (
    analyze_creative_image,
    build_motion_prompt,
    prepare_motion_source,
    validate_motion_plan,
)


def image_bytes(image):
    output = io.BytesIO()
    image.save(output, format="PNG")
    return output.getvalue()


class ImageMotionServiceTests(unittest.TestCase):
    def test_default_prompt_is_not_empty_without_custom_instruction(self):
        asset = {
            "category": "oven",
            "model": "OV-01",
            "feature": "360 degree hot air circulation",
            "analysis": {},
        }
        prompt = build_motion_prompt(asset, {"preset": "flow", "focus": "effect", "intensity": "standard"})
        self.assertIn("exactly one Hisense oven", prompt)
        self.assertIn("bright, positive", prompt)
        self.assertNotIn("Creator instruction", prompt)

    def test_source_ratio_is_preserved(self):
        source = image_bytes(Image.new("RGB", (800, 1000), (30, 40, 50)))
        prepared, metadata = prepare_motion_source(
            source,
            {"visual_crop": {"x": 0, "y": 0, "width": 1, "height": 1}},
            ratio="source",
            text_policy="visual_only",
            quality="720p",
        )
        prepared_image = Image.open(io.BytesIO(prepared))
        self.assertEqual((720, 900), prepared_image.size)
        self.assertEqual("source", metadata["aspect_ratio"])
        self.assertEqual("1:1", metadata["provider_aspect_ratio"])

    def test_example_like_header_recommends_lower_visual_crop(self):
        image = Image.new("RGB", (1000, 1000), (20, 20, 20))
        draw = ImageDraw.Draw(image)
        for x in range(0, 1000, 40):
            for y in range(340, 1000, 40):
                fill = (225, 120, 35) if (x // 40 + y // 40) % 2 else (15, 20, 25)
                draw.rectangle((x, y, x + 38, y + 38), fill=fill)
        result = analyze_creative_image(
            image_bytes(image),
            filename="oven-air-flow.png",
            category="oven",
            model="OV-01",
            feature="hot air circulation",
        )
        self.assertTrue(result["has_copy_header"])
        self.assertGreaterEqual(result["visual_crop"]["y"], 0.29)
        self.assertEqual("flow", result["recommended_preset"])
        self.assertTrue(result["ready"])

    def test_multiple_appliance_hint_blocks_submission(self):
        result = analyze_creative_image(
            image_bytes(Image.new("RGB", (800, 800), (30, 40, 50))),
            filename="oven-multi-product.png",
            category="oven",
            model="OV-01",
        )
        self.assertEqual(2, result["appliance_count"])
        self.assertFalse(result["ready"])
        self.assertTrue(result["blocking_issues"])

    def test_motion_plan_exposes_only_supported_runtime_values(self):
        plan = validate_motion_plan(
            {
                "preset": "unsupported",
                "intensity": "maximum",
                "focus": "anything",
                "text_policy": "redraw_text",
                "aspect_ratio": "4:3",
                "duration_seconds": 8,
            }
        )
        self.assertEqual("auto", plan["preset"])
        self.assertEqual("standard", plan["intensity"])
        self.assertEqual("effect", plan["focus"])
        self.assertEqual("visual_only", plan["text_policy"])
        self.assertEqual("source", plan["aspect_ratio"])
        self.assertEqual(5.0, plan["duration_seconds"])
        self.assertEqual(24, plan["fps"])


if __name__ == "__main__":
    unittest.main()

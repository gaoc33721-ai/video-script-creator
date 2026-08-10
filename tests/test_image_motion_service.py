import io
import unittest

from PIL import Image, ImageDraw

from image_motion_service import (
    analyze_creative_image,
    assess_component_motion,
    build_motion_prompt,
    enhance_image_resolution,
    prepare_motion_source,
    render_stable_motion_video,
    validate_motion_plan,
)


def image_bytes(image):
    output = io.BytesIO()
    image.save(output, format="PNG")
    return output.getvalue()


class ImageMotionServiceTests(unittest.TestCase):
    def test_low_resolution_image_is_fidelity_upscaled_to_720_short_side(self):
        source = image_bytes(Image.new("RGB", (690, 388), (25, 90, 105)))
        enhanced, metadata = enhance_image_resolution(source)
        self.assertTrue(metadata["applied"])
        self.assertEqual("enhanced", metadata["status"])
        self.assertEqual((1280, 720), Image.open(io.BytesIO(enhanced)).size)
        self.assertGreaterEqual(metadata["fidelity_score"], 72)

    def test_too_small_image_is_not_upscaled(self):
        source = image_bytes(Image.new("RGB", (320, 180), (25, 90, 105)))
        enhanced, metadata = enhance_image_resolution(source)
        self.assertFalse(metadata["applied"])
        self.assertEqual("too_small", metadata["status"])
        self.assertEqual(source, enhanced)

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

    def test_component_prompt_requires_real_part_motion_and_locked_camera(self):
        asset = {
            "category": "Airfryer",
            "model": "HAFA11BDW",
            "feature": "Dual Cooking Zone",
            "analysis": {},
        }
        plan = validate_motion_plan(
            {"preset": "component", "focus": "product", "intensity": "standard", "direction": "drawer motion"}
        )
        prompt = build_motion_prompt(asset, plan)
        self.assertEqual("locked", plan["camera_motion"])
        self.assertIn("cooking basket or front drawer slides outward", prompt)
        self.assertIn("absolutely no zoom", prompt)
        self.assertIn("outer cabinet remain fixed", prompt)

    def test_flow_prompt_requires_real_currents_and_rejects_highlight_sweep(self):
        asset = {
            "category": "Airfryer",
            "model": "HAFA11BDW",
            "feature": "Dual Cooking Zone",
            "analysis": {},
        }
        plan = validate_motion_plan(
            {"preset": "flow", "focus": "effect", "intensity": "standard", "direction": "circulating hot air"}
        )
        prompt = build_motion_prompt(asset, plan)
        self.assertEqual("locked", plan["camera_motion"])
        self.assertIn("Both visible cooking zones", prompt)
        self.assertIn("continuous circulating hot-air currents", prompt)
        self.assertIn("not be a static glow, horizontal highlight sweep", prompt)
        self.assertIn("absolutely no zoom", prompt)

    def test_steam_prompt_requires_volumetric_vapor_and_rejects_drawn_lines(self):
        asset = {
            "category": "Airfryer",
            "model": "HAFA11BDW",
            "feature": "Dual Cooking Zone",
            "analysis": {},
        }
        plan = validate_motion_plan(
            {"preset": "steam", "focus": "effect", "intensity": "standard", "direction": "rising from hot food"}
        )
        prompt = build_motion_prompt(asset, plan)
        self.assertEqual("locked", plan["camera_motion"])
        self.assertIn("Required volumetric steam motion", prompt)
        self.assertIn("both visible cooking baskets", prompt)
        self.assertIn("irregular turbulent curls", prompt)
        self.assertIn("No drawn white lines", prompt)
        self.assertIn("absolutely no zoom", prompt)

    def test_liquid_prompt_requires_physical_flow_and_rejects_vector_ribbons(self):
        asset = {
            "category": "Dishwasher",
            "model": "DW-01",
            "feature": "Fresh Water Rinse",
            "analysis": {},
        }
        plan = validate_motion_plan(
            {"preset": "liquid", "focus": "effect", "intensity": "standard", "direction": "downward rinse"}
        )
        prompt = build_motion_prompt(asset, plan)
        self.assertEqual("locked", plan["camera_motion"])
        self.assertIn("physically coherent liquid motion", prompt)
        self.assertIn("surface ripples", prompt)
        self.assertIn("No blue lines, vector ribbons", prompt)
        self.assertIn("absolutely no zoom", prompt)

    def test_airfryer_category_name_does_not_force_touch_panel_into_flow(self):
        result = analyze_creative_image(
            image_bytes(Image.new("RGB", (800, 800), (30, 40, 50))),
            filename="Colourful-Touch-Panel.jpg",
            category="Airfryer",
            model="HAFA11BDW",
            feature="Colourful Touch Panel",
        )
        self.assertEqual("glow", result["recommended_preset"])

    def test_dual_cooking_zone_still_recommends_flow(self):
        result = analyze_creative_image(
            image_bytes(Image.new("RGB", (800, 800), (30, 40, 50))),
            filename="Dual-Cooking-Zone.jpg",
            category="Airfryer",
            model="HAFA11BDW",
            feature="Dual Cooking Zone",
        )
        self.assertEqual("flow", result["recommended_preset"])

    def test_flow_plan_locks_camera_and_renders_localized_hot_air_motion(self):
        plan = validate_motion_plan({"preset": "flow", "focus": "effect", "intensity": "standard"})
        self.assertEqual("locked", plan["camera_motion"])
        source = Image.new("RGB", (320, 180), (12, 32, 38))
        draw = ImageDraw.Draw(source)
        draw.arc((70, 35, 250, 155), 20, 340, fill=(255, 135, 20), width=10)
        draw.line((100, 130, 100, 55), fill=(255, 185, 40), width=8)
        draw.line((220, 55, 220, 130), fill=(255, 185, 40), width=8)
        video = render_stable_motion_video(
            image_bytes(source),
            preset="flow",
            intensity="standard",
            duration_seconds=5,
            fps=24,
        )
        qa = assess_component_motion(
            video,
            target_region={"x": 0.10, "y": 0.10, "width": 0.80, "height": 0.80},
            motion_name="\u70ed\u6d41",
        )
        self.assertEqual("passed", qa["status"], qa)
        self.assertFalse(qa["global_zoom_explains_motion"])
        strict_qa = assess_component_motion(
            video,
            target_region={"x": 0.10, "y": 0.10, "width": 0.80, "height": 0.80},
            motion_name="\u70ed\u6d41",
            minimum_motion_coverage=0.22,
        )
        self.assertEqual("failed", strict_qa["status"], strict_qa)




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

    def test_provider_frame_can_be_limited_without_changing_requested_quality(self):
        source = image_bytes(Image.new("RGB", (2400, 800), (30, 40, 50)))
        prepared, metadata = prepare_motion_source(
            source,
            {"visual_crop": {"x": 0, "y": 0, "width": 1, "height": 1}},
            ratio="source",
            text_policy="visual_only",
            quality="1080p",
            max_dimension=1552,
        )
        prepared_size = Image.open(io.BytesIO(prepared)).size
        self.assertLessEqual(max(prepared_size), 1552)
        self.assertEqual(1552, metadata["max_dimension"])

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

import io
import unittest

from PIL import Image, ImageDraw

from image_motion_service import (
    analyze_creative_image,
    assess_component_motion,
    assess_video_fidelity,
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
        self.assertIn("about 3% total", prompt)
        self.assertIn("tiny 1% lateral arc", prompt)
        self.assertNotIn("Creator instruction", prompt)

    def test_component_prompt_requires_real_part_motion_and_controlled_camera(self):
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
        self.assertEqual("gentle_cinematic", plan["camera_motion"])
        self.assertIn("cooking basket or front drawer slides outward", prompt)
        self.assertIn("smooth cinematic dolly-in", prompt)
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
        self.assertEqual("gentle_cinematic", plan["camera_motion"])
        self.assertIn("Both visible cooking zones", prompt)
        self.assertIn("continuous circulating hot-air currents", prompt)
        self.assertIn("not be a static glow, horizontal highlight sweep", prompt)
        self.assertIn("subtle particle advection", prompt)
        self.assertIn("heat shimmer", prompt)
        self.assertIn("Premium technology-commercial finish", prompt)
        self.assertIn("0.6-4.4 seconds", prompt)
        self.assertIn("smooth cinematic dolly-in", prompt)

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
        self.assertEqual("gentle_cinematic", plan["camera_motion"])
        self.assertIn("Required volumetric steam motion", prompt)
        self.assertIn("both visible cooking baskets", prompt)
        self.assertIn("irregular turbulent curls", prompt)
        self.assertIn("No drawn white lines", prompt)
        self.assertIn("smooth cinematic dolly-in", prompt)

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
        self.assertEqual("gentle_cinematic", plan["camera_motion"])
        self.assertIn("physically coherent liquid motion", prompt)
        self.assertIn("surface ripples", prompt)
        self.assertIn("No blue lines, vector ribbons", prompt)
        self.assertIn("smooth cinematic dolly-in", prompt)

    def test_glow_prompt_locks_product_and_uses_layered_technology_choreography(self):
        asset = {
            "category": "Airfryer",
            "model": "HAFA11BDW",
            "feature": "Viewing Windows",
            "analysis": {},
        }
        plan = validate_motion_plan(
            {"preset": "glow", "focus": "effect", "intensity": "standard", "direction": "feature illumination"}
        )

        prompt = build_motion_prompt(asset, plan)

        self.assertEqual("gentle_cinematic", plan["camera_motion"])
        self.assertIn("immutable product reference", prompt)
        self.assertIn("existing viewing window", prompt)
        self.assertIn("inner emissive depth", prompt)
        self.assertIn("material-aware micro-reflections", prompt)
        self.assertIn("0.0-0.6 seconds", prompt)
        self.assertIn("4.4-5.0 seconds", prompt)
        self.assertIn("must not travel as one flat horizontal sweep", prompt)
        self.assertIn("localized illumination evolving inside", prompt)
        self.assertNotIn("gentle forward motion", prompt)
        self.assertIn("smooth cinematic dolly-in", prompt)
        self.assertLessEqual(len(prompt), 3000)

    def test_component_prompt_uses_visible_travel_and_holds_final_position(self):
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

        self.assertIn("15-22% of its visible depth", prompt)
        self.assertIn("holds open long enough", prompt)
        self.assertIn("1.2-3.8 seconds complete one smooth", prompt)
        self.assertIn("Do not return to the start", prompt)
        self.assertIn("Do not substitute camera motion", prompt)

    def test_creator_directed_component_action_has_priority_over_default_action(self):
        asset = {
            "category": "Airfryer",
            "model": "HAFA11BDW",
            "feature": "Large Capacity",
            "analysis": {},
        }
        plan = validate_motion_plan(
            {
                "preset": "component",
                "focus": "product",
                "intensity": "strong",
                "custom_instruction": "Pull the left drawer outward along its rails by 30% and hold it open",
            }
        )

        prompt = build_motion_prompt(asset, plan)

        self.assertIn("execute this creator-directed action exactly", prompt)
        self.assertIn("Pull the left drawer outward along its rails by 30% and hold it open", prompt)
        self.assertNotIn("one clearly visible cooking basket", prompt)
        self.assertIn("spatially separated from the fixed cabinet", prompt)
        self.assertLessEqual(len(prompt), 3000)

    def test_creator_directed_natural_effect_rejects_overlay_substitution(self):
        asset = {
            "category": "Oven",
            "model": "Speed Oven",
            "feature": "60% Faster Cooking",
            "analysis": {},
        }
        plan = validate_motion_plan(
            {
                "preset": "flow",
                "focus": "effect",
                "intensity": "strong",
                "custom_instruction": "Develop heat from the food into a three-dimensional convection field",
            }
        )

        prompt = build_motion_prompt(asset, plan)

        self.assertIn("highest priority after product fidelity", prompt)
        self.assertIn("visible travel and evolving depth", prompt)
        self.assertIn("do not reduce it to a glow pulse", prompt)
        self.assertIn("never loop one identical overlay", prompt)

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

    def test_flow_plan_uses_controlled_camera_and_renders_localized_hot_air_motion(self):
        plan = validate_motion_plan({"preset": "flow", "focus": "effect", "intensity": "standard"})
        self.assertEqual("gentle_cinematic", plan["camera_motion"])
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
            allow_gentle_camera_motion=True,
        )
        self.assertEqual("passed", qa["status"], qa)
        self.assertTrue(qa["global_zoom_explains_motion"])
        strict_qa = assess_component_motion(
            video,
            target_region={"x": 0.10, "y": 0.10, "width": 0.80, "height": 0.80},
            motion_name="\u70ed\u6d41",
            allow_gentle_camera_motion=True,
            minimum_motion_coverage=0.22,
        )
        self.assertEqual("passed", strict_qa["status"], strict_qa)




    def test_steam_composite_preserves_pixels_outside_effect_region(self):
        plan = validate_motion_plan({"preset": "steam", "focus": "effect", "intensity": "standard"})
        self.assertEqual("hybrid_composite", plan["generation_strategy"])
        source = Image.new("RGB", (320, 180), (10, 34, 42))
        draw = ImageDraw.Draw(source)
        draw.rounded_rectangle((45, 45, 275, 165), radius=18, fill=(25, 25, 28), outline=(150, 155, 160), width=3)
        draw.rectangle((75, 95, 155, 155), fill=(190, 95, 25))
        draw.rectangle((165, 95, 245, 155), fill=(220, 160, 55))
        region = {"x": 0.10, "y": 0.12, "width": 0.80, "height": 0.72}
        prepared = image_bytes(source)
        video = render_stable_motion_video(
            prepared,
            preset="steam",
            intensity="standard",
            duration_seconds=5,
            fps=24,
            effect_region=region,
        )
        fidelity = assess_video_fidelity(prepared, video, allowed_motion_region=region, allow_gentle_camera_motion=True)
        self.assertEqual("passed", fidelity["status"], fidelity)
        self.assertLessEqual(fidelity["outside_effect_difference"], 8.0)
        motion = assess_component_motion(
            video,
            target_region=region,
            motion_name="蒸汽",
            minimum_motion_coverage=0.06,
            allow_gentle_camera_motion=True,
        )
        self.assertEqual("passed", motion["status"], motion)
        self.assertTrue(motion["global_zoom_explains_motion"])

    def test_camera_only_motion_cannot_pass_as_selling_point_motion(self):
        source = Image.new("RGB", (320, 180), (18, 42, 52))
        draw = ImageDraw.Draw(source)
        draw.rectangle((35, 30, 285, 155), outline=(210, 220, 225), width=5)
        draw.ellipse((105, 55, 215, 150), fill=(230, 125, 35))
        video = render_stable_motion_video(
            image_bytes(source),
            preset="camera",
            intensity="standard",
            duration_seconds=5,
            fps=12,
        )

        qa = assess_component_motion(
            video,
            target_region={"x": 0.25, "y": 0.20, "width": 0.50, "height": 0.65},
            motion_name="卖点",
            allow_gentle_camera_motion=True,
        )

        self.assertEqual("failed", qa["status"], qa)
        self.assertTrue(qa["gentle_camera_motion_allowed"])

    def test_full_frame_motion_region_does_not_divide_by_zero(self):
        prepared = image_bytes(Image.new("RGB", (320, 180), (22, 48, 58)))
        video = render_stable_motion_video(
            prepared,
            preset="camera",
            intensity="subtle",
            duration_seconds=1,
            fps=8,
        )

        qa = assess_video_fidelity(
            prepared,
            video,
            allowed_motion_region={"x": 0.0, "y": 0.0, "width": 1.0, "height": 1.0},
        )

        self.assertIn(qa["status"], {"passed", "failed"})
        self.assertEqual(0.0, qa["outside_effect_difference"])

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

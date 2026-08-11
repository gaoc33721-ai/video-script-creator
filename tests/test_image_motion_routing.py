import copy
import io
import unittest

from PIL import Image

from image_motion_routes import (
    CREATIVE_ASSETS_KEY,
    IMAGE_MOTION_JOBS_KEY,
    ImageMotionWorkflow,
)


class MemoryStorage:
    def __init__(self):
        self.json = {}
        self.files = {}

    def read_json(self, key, default):
        return copy.deepcopy(self.json.get(key, default))

    def write_json(self, key, value):
        self.json[key] = copy.deepcopy(value)
        return True

    def read_file_bytes(self, key):
        return self.files[key]

    def write_file_bytes(self, key, value, content_type="application/octet-stream"):
        self.files[key] = value
        return key


def png_bytes(size=(800, 800)):
    output = io.BytesIO()
    Image.new("RGB", size, (25, 80, 100)).save(output, format="PNG")
    return output.getvalue()


def component_asset():
    return {
        "id": "asset-component",
        "category": "Airfryer",
        "model": "HAFA11BDW",
        "feature": "Dual Cooking Zone",
        "original_key": "creative-assets/asset-component/original.png",
        "analysis": {
            "ready": True,
            "recommended_preset": "component",
            "visual_crop": {"x": 0, "y": 0, "width": 1, "height": 1},
        },
    }


def component_job(status="queued"):
    return {
        "id": "job-component",
        "creative_asset_id": "asset-component",
        "version": 1,
        "status": status,
        "motion_plan": {
            "preset": "component",
            "text_policy": "visual_only",
            "focus": "product",
            "intensity": "standard",
            "aspect_ratio": "source",
            "duration_seconds": 5,
        },
    }

def natural_asset(preset="flow"):
    return {
        "id": f"asset-{preset}",
        "category": "Airfryer",
        "model": "HAFA11BDW",
        "feature": "Dual Cooking Zone",
        "original_key": f"creative-assets/asset-{preset}/original.png",
        "analysis": {
            "ready": True,
            "recommended_preset": preset,
            "visual_crop": {"x": 0, "y": 0, "width": 1, "height": 1},
        },
    }


def natural_job(status="queued", preset="flow"):
    return {
        "id": f"job-{preset}",
        "creative_asset_id": f"asset-{preset}",
        "version": 1,
        "status": status,
        "motion_plan": {
            "preset": preset,
            "text_policy": "visual_only",
            "focus": "effect",
            "intensity": "standard",
            "aspect_ratio": "source",
            "duration_seconds": 5,
            "generation_strategy": "generative",
        },
    }



class ImageMotionRoutingTests(unittest.TestCase):
    def workflow(self, component_submit, component_poll, preset="component"):
        storage = MemoryStorage()
        is_natural = preset in {"flow", "steam", "liquid"}
        asset = natural_asset(preset) if is_natural else component_asset()
        storage.json[CREATIVE_ASSETS_KEY] = [asset]
        storage.json[IMAGE_MOTION_JOBS_KEY] = [natural_job(preset=preset) if is_natural else component_job()]
        storage.files[asset["original_key"]] = png_bytes((690, 388)) if is_natural else png_bytes()
        workflow = ImageMotionWorkflow(
            storage,
            lambda: [],
            component_provider_submit=component_submit,
            component_provider_poll=component_poll,
        )
        workflow.render_fallback = lambda *args, **kwargs: self.fail("Ray2 target motion must not use stable fallback")
        return workflow, storage

    def test_component_job_uses_dedicated_provider(self):
        submitted = {}

        def submit(**kwargs):
            submitted.update(kwargs)
            return {"task_id": "ray-task-1", "provider": "luma_ray2", "metadata": {"region": "us-west-2"}}

        workflow, storage = self.workflow(submit, lambda task_id: {"status": "processing"})
        workflow.run_job("job-component")

        job = storage.json[IMAGE_MOTION_JOBS_KEY][0]
        self.assertEqual("processing", job["status"])
        self.assertEqual("luma_ray2_component", job["generation_mode"])
        self.assertEqual("luma_ray2", job["provider_name"])
        self.assertEqual("ray-task-1", job["external_task_id"])
        self.assertEqual("1:1", submitted["aspect_ratio"])
        self.assertIn("absolutely no zoom", submitted["prompt"])

    def test_exclusive_libtv_provider_overrides_all_legacy_providers(self):
        storage = MemoryStorage()
        asset = natural_asset("flow")
        storage.json[CREATIVE_ASSETS_KEY] = [asset]
        storage.json[IMAGE_MOTION_JOBS_KEY] = [natural_job(preset="flow")]
        storage.files[asset["original_key"]] = png_bytes((690, 388))
        submitted = {}

        def submit(**kwargs):
            submitted.update(kwargs)
            return {"task_id": kwargs["client_business_id"], "provider": "libtv_happy_horse_1_1"}

        workflow = ImageMotionWorkflow(
            storage,
            lambda: [],
            provider_submit=submit,
            provider_poll=lambda task_id: {"status": "processing"},
            component_provider_submit=lambda **kwargs: self.fail("legacy provider must not be called"),
            component_provider_poll=lambda task_id: self.fail("legacy provider must not be polled"),
            comparison_provider_submit=lambda **kwargs: self.fail("comparison provider must not be called"),
            comparison_provider_poll=lambda task_id: self.fail("comparison provider must not be polled"),
            exclusive_provider_name="libtv_happy_horse_1_1",
        )

        workflow.run_job("job-flow")

        job = storage.json[IMAGE_MOTION_JOBS_KEY][0]
        self.assertEqual("processing", job["status"])
        self.assertEqual("libtv_happy_horse_1_1", job["provider_name"])
        self.assertEqual("libtv_happy_horse_1_1_flow", job["generation_mode"])
        self.assertEqual("image_motion_job-flow", job["external_task_id"])
        self.assertEqual("image_motion_job-flow", submitted["client_business_id"])

    def test_component_submit_failure_is_not_downgraded(self):
        def submit(**kwargs):
            raise RuntimeError("Ray 2 unavailable")

        workflow, storage = self.workflow(submit, lambda task_id: {"status": "processing"})
        workflow.run_job("job-component")

        job = storage.json[IMAGE_MOTION_JOBS_KEY][0]
        self.assertEqual("failed", job["status"])
        self.assertEqual("failed", job["qa_status"])
        self.assertNotIn("video_key", job)

    def test_flow_job_uses_ray2_provider_and_rejects_sweep_prompt(self):
        submitted = {}

        def submit(**kwargs):
            submitted.update(kwargs)
            return {"task_id": "ray-flow-1", "provider": "luma_ray2", "metadata": {"region": "us-west-2"}}

        workflow, storage = self.workflow(submit, lambda task_id: {"status": "processing"}, preset="flow")
        workflow.run_job("job-flow")

        job = storage.json[IMAGE_MOTION_JOBS_KEY][0]
        self.assertEqual("processing", job["status"])
        self.assertEqual("luma_ray2_flow", job["generation_mode"])
        self.assertEqual("luma_ray2", job["provider_name"])
        self.assertEqual("ray-flow-1", job["external_task_id"])
        self.assertIn("Both visible cooking zones", submitted["prompt"])
        self.assertIn("not be a static glow, horizontal highlight sweep", submitted["prompt"])
        self.assertTrue(submitted["lock_end_frame"])
        submitted_size = Image.open(io.BytesIO(submitted["image_bytes"])).size
        self.assertEqual((1280, 720), submitted_size)
        self.assertLessEqual(max(submitted_size), 1552)
        self.assertEqual(1280, job["prepared_metadata"]["width"])
        self.assertEqual((1920, 1080), (job["prepared_metadata"]["export_width"], job["prepared_metadata"]["export_height"]))


    def test_steam_and_liquid_jobs_use_ray2_with_natural_motion_prompts(self):
        expected_prompt = {
            "steam": "No drawn white lines",
            "liquid": "No blue lines, vector ribbons",
        }
        for preset, required_text in expected_prompt.items():
            with self.subTest(preset=preset):
                submitted = {}

                def submit(**kwargs):
                    submitted.update(kwargs)
                    return {"task_id": f"ray-{preset}-1", "provider": "luma_ray2"}

                workflow, storage = self.workflow(
                    submit,
                    lambda task_id: {"status": "processing"},
                    preset=preset,
                )
                workflow.run_job(f"job-{preset}")

                job = storage.json[IMAGE_MOTION_JOBS_KEY][0]
                self.assertEqual("processing", job["status"])
                self.assertEqual(f"luma_ray2_{preset}", job["generation_mode"])
                self.assertEqual("luma_ray2", job["provider_name"])
                self.assertIn(required_text, submitted["prompt"])
                self.assertIn("absolutely no zoom", submitted["prompt"])
                self.assertTrue(submitted["lock_end_frame"])

    def test_steam_and_liquid_submit_failures_are_not_downgraded(self):
        for preset in ("steam", "liquid"):
            with self.subTest(preset=preset):
                def submit(**kwargs):
                    raise RuntimeError("Ray 2 unavailable")

                workflow, storage = self.workflow(
                    submit,
                    lambda task_id: {"status": "processing"},
                    preset=preset,
                )
                workflow.run_job(f"job-{preset}")

                job = storage.json[IMAGE_MOTION_JOBS_KEY][0]
                self.assertEqual("failed", job["status"])
                self.assertEqual("failed", job["qa_status"])
                self.assertNotIn("video_key", job)

    def test_flow_submit_failure_is_not_downgraded(self):
        def submit(**kwargs):
            raise RuntimeError("Ray 2 unavailable")

        workflow, storage = self.workflow(
            submit,
            lambda task_id: {"status": "processing"},
            preset="flow",
        )
        workflow.run_job("job-flow")
        job = storage.json[IMAGE_MOTION_JOBS_KEY][0]
        self.assertEqual("failed", job["status"])
        self.assertEqual("failed", job["qa_status"])
        self.assertNotIn("video_key", job)

    def test_failed_job_does_not_block_a_new_idempotent_submission(self):
        workflow, storage = self.workflow(
            lambda **kwargs: {"task_id": "ray-flow-retry"},
            lambda task_id: {"status": "processing"},
            preset="flow",
        )
        asset = natural_asset()
        plan = natural_job()["motion_plan"]
        first, first_reused = workflow.create_job(asset, plan, "same-click-key")
        workflow.update_job(first["id"], status="failed")

        second, second_reused = workflow.create_job(asset, plan, "same-click-key")

        self.assertFalse(first_reused)
        self.assertFalse(second_reused)
        self.assertNotEqual(first["id"], second["id"])
        self.assertEqual(first["version"] + 1, second["version"])




    def test_component_poll_failure_is_not_downgraded(self):
        workflow, storage = self.workflow(
            lambda **kwargs: {"task_id": "ray-task-1"},
            lambda task_id: {"status": "failed", "message": "provider failed"},
        )
        job = component_job(status="processing")
        job.update({"external_task_id": "ray-task-1", "provider_name": "luma_ray2"})
        storage.json[IMAGE_MOTION_JOBS_KEY] = [job]

        workflow.refresh_provider_jobs()

        refreshed = storage.json[IMAGE_MOTION_JOBS_KEY][0]
        self.assertEqual("failed", refreshed["status"])
        self.assertEqual("failed", refreshed["qa_status"])
        self.assertNotIn("video_key", refreshed)


if __name__ == "__main__":
    unittest.main()

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


def png_bytes():
    output = io.BytesIO()
    Image.new("RGB", (800, 800), (25, 80, 100)).save(output, format="PNG")
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

def flow_asset():
    return {
        "id": "asset-flow",
        "category": "Airfryer",
        "model": "HAFA11BDW",
        "feature": "Dual Cooking Zone",
        "original_key": "creative-assets/asset-flow/original.png",
        "analysis": {
            "ready": True,
            "recommended_preset": "flow",
            "visual_crop": {"x": 0, "y": 0, "width": 1, "height": 1},
        },
    }


def flow_job(status="queued"):
    return {
        "id": "job-flow",
        "creative_asset_id": "asset-flow",
        "version": 1,
        "status": status,
        "motion_plan": {
            "preset": "flow",
            "text_policy": "visual_only",
            "focus": "effect",
            "intensity": "standard",
            "aspect_ratio": "source",
            "duration_seconds": 5,
        },
    }



class ImageMotionRoutingTests(unittest.TestCase):
    def workflow(self, component_submit, component_poll, preset="component"):
        storage = MemoryStorage()
        asset = flow_asset() if preset == "flow" else component_asset()
        storage.json[CREATIVE_ASSETS_KEY] = [asset]
        storage.json[IMAGE_MOTION_JOBS_KEY] = [flow_job() if preset == "flow" else component_job()]
        storage.files[asset["original_key"]] = png_bytes()
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

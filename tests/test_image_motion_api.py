import io
import os
import tempfile
import unittest

from PIL import Image


_TEST_DATA = tempfile.TemporaryDirectory(prefix="image-motion-tests-")
os.environ["APP_DATA_DIR"] = _TEST_DATA.name
os.environ["STORAGE_BACKEND"] = "local"
os.environ["APP_ACCESS_CONTROL_ENABLED"] = "false"
os.environ["IMAGE_MOTION_ENABLED"] = "true"

from fastapi.testclient import TestClient

import api_app


class ImageMotionApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        api_app.IMAGE_MOTION_WORKFLOW.run_job = lambda job_id: None
        cls.client = TestClient(api_app.app)

    @staticmethod
    def png(color):
        output = io.BytesIO()
        Image.new("RGB", (800, 800), color).save(output, format="PNG")
        return output.getvalue()

    def test_batch_upload_plan_idempotency_and_unified_tasks(self):
        response = self.client.post(
            "/api/creative-assets",
            data={"category": "oven", "model": "OV-API-01"},
            files=[
                ("files", ("oven-flow-a.png", self.png((30, 35, 40)), "image/png")),
                ("files", ("oven-flow-b.png", self.png((40, 45, 50)), "image/png")),
            ],
        )
        self.assertEqual(200, response.status_code, response.text)
        assets = response.json()["assets"]
        self.assertEqual(2, len(assets))
        self.assertTrue(all(item["analysis"]["ready"] for item in assets))

        asset_id = assets[0]["id"]
        response = self.client.post(
            f"/api/creative-assets/{asset_id}/motion-plan",
            json={
                "focus": "effect",
                "preset": "flow",
                "intensity": "subtle",
                "text_policy": "visual_only",
                "aspect_ratio": "source",
            },
        )
        self.assertEqual(200, response.status_code, response.text)
        self.assertEqual("subtle", response.json()["motion_plan"]["intensity"])

        payload = {"creative_asset_ids": [item["id"] for item in assets]}
        first = self.client.post("/api/image-motion/jobs", json=payload)
        second = self.client.post("/api/image-motion/jobs", json=payload)
        self.assertEqual(200, first.status_code, first.text)
        self.assertEqual(200, second.status_code, second.text)
        self.assertEqual(
            [item["id"] for item in first.json()["jobs"]],
            [item["id"] for item in second.json()["jobs"]],
        )
        self.assertTrue(all(item["idempotent_reuse"] for item in second.json()["jobs"]))

        tasks = self.client.get("/api/tasks?task_type=image_motion")
        self.assertEqual(200, tasks.status_code, tasks.text)
        self.assertEqual(2, len(tasks.json()["tasks"]))
        self.assertTrue(all(item["task_type"] == "image_motion" for item in tasks.json()["tasks"]))

        legacy_jobs = self.client.get("/api/jobs")
        legacy_options = self.client.get("/api/options")
        self.assertEqual(200, legacy_jobs.status_code, legacy_jobs.text)
        self.assertEqual(200, legacy_options.status_code, legacy_options.text)
        self.assertIn("jobs", legacy_jobs.json())
        self.assertIn("categories", legacy_options.json())


if __name__ == "__main__":
    unittest.main()

import io
import os
import tempfile
import unittest
from unittest.mock import Mock, patch

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
    def png(color, size=(800, 800)):
        output = io.BytesIO()
        Image.new("RGB", size, color).save(output, format="PNG")
        return output.getvalue()

    def test_all_video_tasks_are_locked_to_libtv_happy_horse(self):
        self.assertEqual("libtv_happy_horse_1_1", api_app._video_provider_name())
        self.assertEqual("happy-horse-1.1", api_app._video_model_id())
        response = self.client.post(
            "/api/nova-reel/submit",
            json={"script_job_id": "unused", "variant_index": 0},
        )
        self.assertEqual(409, response.status_code, response.text)
    def test_existing_low_resolution_asset_is_migrated_on_workspace_load(self):
        workflow = api_app.IMAGE_MOTION_WORKFLOW
        original = self.png((30, 80, 100), (690, 388))
        asset_id = "legacy-low-asset"
        original_key = f"creative-assets/{asset_id}/original.png"
        workflow.storage.write_file_bytes(original_key, original, content_type="image/png")
        legacy = {
            "id": asset_id,
            "category": "air fryer",
            "model": "AF-LEGACY-01",
            "feature": "large capacity",
            "filename": "airfryer-legacy.png",
            "content_type": "image/png",
            "source_width": 690,
            "source_height": 388,
            "file_hash": "legacy",
            "original_key": original_key,
            "analysis": {"ready": False, "blocking_issues": ["low resolution"]},
            "created_at": workflow.now(),
            "updated_at": workflow.now(),
        }
        workflow.write_list("creative_assets.json", [legacy] + workflow.assets(), 1000)

        response = self.client.get(
            "/api/creative-assets",
            params={"category": "air fryer", "model": "AF-LEGACY-01"},
        )
        self.assertEqual(200, response.status_code, response.text)
        asset = response.json()["assets"][0]
        self.assertTrue(asset["resolution_enhancement"]["applied"])
        self.assertEqual((1280, 720), (asset["working_width"], asset["working_height"]))
        self.assertTrue(asset["analysis"]["ready"])
        preview = self.client.get(asset["preview_url"])
        self.assertEqual((1280, 720), Image.open(io.BytesIO(preview.content)).size)

    def test_low_resolution_upload_is_enhanced_and_ready(self):
        response = self.client.post(
            "/api/creative-assets",
            data={"category": "air fryer", "model": "AF-LOW-01"},
            files=[
                ("files", ("airfryer-low.png", self.png((25, 90, 105), (690, 388)), "image/png")),
            ],
        )
        self.assertEqual(200, response.status_code, response.text)
        asset = response.json()["assets"][0]
        self.assertEqual((690, 388), (asset["source_width"], asset["source_height"]))
        self.assertEqual((1280, 720), (asset["working_width"], asset["working_height"]))
        self.assertTrue(asset["resolution_enhancement"]["applied"])
        self.assertTrue(asset["analysis"]["ready"])
        preview = self.client.get(asset["preview_url"])
        self.assertEqual(200, preview.status_code, preview.text)
        self.assertEqual((1280, 720), Image.open(io.BytesIO(preview.content)).size)
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
        self.assertEqual("hybrid_composite", response.json()["motion_plan"]["generation_strategy"])

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

        explicit_click = {**payload, "idempotency_key": "explicit-click-1"}
        third = self.client.post("/api/image-motion/jobs", json=explicit_click)
        third_replay = self.client.post("/api/image-motion/jobs", json=explicit_click)
        fourth = self.client.post(
            "/api/image-motion/jobs",
            json={**payload, "idempotency_key": "explicit-click-2"},
        )
        self.assertEqual(200, third.status_code, third.text)
        self.assertEqual(200, third_replay.status_code, third_replay.text)
        self.assertEqual(200, fourth.status_code, fourth.text)
        self.assertNotEqual(
            [item["id"] for item in first.json()["jobs"]],
            [item["id"] for item in third.json()["jobs"]],
        )
        self.assertEqual(
            [item["id"] for item in third.json()["jobs"]],
            [item["id"] for item in third_replay.json()["jobs"]],
        )
        self.assertTrue(all(item["idempotent_reuse"] for item in third_replay.json()["jobs"]))
        self.assertNotEqual(
            [item["id"] for item in third.json()["jobs"]],
            [item["id"] for item in fourth.json()["jobs"]],
        )
        self.assertEqual([2, 2], [item["version"] for item in third.json()["jobs"]])
        self.assertEqual([3, 3], [item["version"] for item in fourth.json()["jobs"]])

        failed_job_id = fourth.json()["jobs"][0]["id"]
        strengthened = self.client.post(
            f"/api/image-motion/jobs/{failed_job_id}/regenerate",
            json={"action": "strengthen_effect"},
        )
        self.assertEqual(200, strengthened.status_code, strengthened.text)
        retry_job = strengthened.json()["job"]
        self.assertEqual(4, retry_job["version"])
        self.assertEqual(failed_job_id, retry_job["parent_job_id"])
        self.assertEqual("flow", retry_job["motion_plan"]["preset"])
        self.assertEqual("standard", retry_job["motion_plan"]["intensity"])
        self.assertIn("Previous attempt failed motion QA", retry_job["motion_plan"]["custom_instruction"])
        self.assertIn("Do not substitute camera zoom", retry_job["motion_plan"]["custom_instruction"])

        api_app.IMAGE_MOTION_WORKFLOW.update_job(
            failed_job_id,
            status="failed",
            failure_message="产品或背景发生重绘：整帧相似度约45%。",
            qa_result={
                "status": "failed",
                "fidelity": {"status": "failed", "message": "产品或背景发生重绘：整帧相似度约45%。"},
            },
        )
        blocked = self.client.post(
            f"/api/image-motion/jobs/{failed_job_id}/regenerate",
            json={"action": "strengthen_effect"},
        )
        self.assertEqual(409, blocked.status_code, blocked.text)

        preserved = self.client.post(
            f"/api/image-motion/jobs/{failed_job_id}/regenerate",
            json={"action": "preserve_composition"},
        )
        self.assertEqual(200, preserved.status_code, preserved.text)
        preserved_job = preserved.json()["job"]
        self.assertEqual(5, preserved_job["version"])
        self.assertEqual("generative", preserved_job["motion_plan"]["generation_strategy"])
        self.assertEqual("libtv_happy_horse_1_1", preserved_job["motion_plan"]["provider_preference"])
        self.assertEqual("", preserved_job["motion_plan"]["custom_instruction"])

        compared = self.client.post(
            f"/api/image-motion/jobs/{failed_job_id}/regenerate",
            json={"action": "compare_model"},
        )
        self.assertEqual(409, compared.status_code, compared.text)
        tasks = self.client.get("/api/tasks?task_type=image_motion")
        self.assertEqual(200, tasks.status_code, tasks.text)
        self.assertEqual(8, len(tasks.json()["tasks"]))
        self.assertTrue(all(item["task_type"] == "image_motion" for item in tasks.json()["tasks"]))

        legacy_jobs = self.client.get("/api/jobs")
        legacy_options = self.client.get("/api/options")
        self.assertEqual(200, legacy_jobs.status_code, legacy_jobs.text)
        self.assertEqual(200, legacy_options.status_code, legacy_options.text)
        self.assertIn("jobs", legacy_jobs.json())
        self.assertIn("categories", legacy_options.json())


    def test_ray2_model_input_uses_asset_ratio_and_start_keyframe(self):
        image_payload = {
            "type": "image",
            "source": {"type": "base64", "media_type": "image/png", "data": "abc"},
        }
        payload = api_app._build_luma_ray2_model_input(
            "locked camera component motion",
            duration_seconds=5,
            image_payload=image_payload,
            aspect_ratio="1:1",
        )
        self.assertEqual("1:1", payload["aspect_ratio"])
        self.assertEqual("5s", payload["duration"])
        self.assertEqual("720p", payload["resolution"])
        self.assertEqual(image_payload, payload["keyframes"]["frame0"])
        self.assertNotIn("frame1", payload["keyframes"])

        locked_payload = api_app._build_luma_ray2_model_input(
            "locked steam motion",
            duration_seconds=5,
            image_payload=image_payload,
            end_image_payload=image_payload,
            aspect_ratio="16:9",
        )
        self.assertEqual(image_payload, locked_payload["keyframes"]["frame1"])

    def test_nova_reel_job_submits_image_to_video_payload(self):
        image_payload = {"format": "png", "source": {"bytes": "abc"}}
        client = Mock()
        client.start_async_invoke.return_value = {"invocationArn": "arn:aws:bedrock:job/test"}

        with patch.object(api_app, "_nova_reel_job_output_uri", return_value="s3://nova-output/runtime/test"), patch.object(
            api_app.boto3, "client", return_value=client
        ):
            invocation_arn, output_uri = api_app._start_nova_reel_job(
                "Airfryer", "HAFA11BDW", "localized realistic steam", image_payload=image_payload
            )

        self.assertEqual("arn:aws:bedrock:job/test", invocation_arn)
        self.assertEqual("s3://nova-output/runtime/test", output_uri)
        request = client.start_async_invoke.call_args.kwargs
        self.assertEqual("amazon.nova-reel-v1:1", request["modelId"])
        self.assertEqual([image_payload], request["modelInput"]["textToVideoParams"]["images"])
        self.assertEqual(6, request["modelInput"]["videoGenerationConfig"]["durationSeconds"])
        self.assertEqual("1280x720", request["modelInput"]["videoGenerationConfig"]["dimension"])

if __name__ == "__main__":
    unittest.main()

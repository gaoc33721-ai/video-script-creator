import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from libtv_provider import (
    HAPPY_HORSE_MODEL_KEY,
    HAPPY_HORSE_MODEL_NAME,
    LibTVConfig,
    LibTVHappyHorseProvider,
    LibTVProviderError,
    PROVIDER_NAME,
)


class MemoryStorage:
    def __init__(self):
        self.json_values = {}
        self.files = {}

    def read_json(self, key, default=None):
        return self.json_values.get(key, default)

    def write_json(self, key, value):
        self.json_values[key] = json.loads(json.dumps(value))
        return True

    def write_file_bytes(self, key, value, content_type="application/octet-stream"):
        del content_type
        self.files[key] = bytes(value)
        return True

    def read_file_bytes(self, key):
        return self.files[key]


class FakeLibTVRunner:
    def __init__(self):
        self.commands = []

    def __call__(self, command, env):
        del env
        self.commands.append(list(command))
        args = command[1:]
        if args[:2] == ["model", HAPPY_HORSE_MODEL_NAME]:
            payload = {"modelKey": HAPPY_HORSE_MODEL_KEY, "modelName": HAPPY_HORSE_MODEL_NAME}
            return subprocess.CompletedProcess(command, 0, json.dumps(payload), "")
        if args[:2] == ["node", "create"]:
            return subprocess.CompletedProcess(command, 0, json.dumps({"nodeKey": "video-1"}), "")
        if args and args[0] == "node" and "--run" in args:
            return subprocess.CompletedProcess(command, 0, json.dumps({"nodeKey": "video-1", "status": "success"}), "")
        if args and args[0] == "node":
            return subprocess.CompletedProcess(command, 1, "", "node not found")
        if args and args[0] == "upload":
            return subprocess.CompletedProcess(command, 0, json.dumps({"nodeKey": "input-1"}), "")
        if args and args[0] == "download":
            output_dir = Path(args[args.index("-o") + 1])
            output_dir.mkdir(parents=True, exist_ok=True)
            (output_dir / "result.mp4").write_bytes(b"0" * 2048)
            return subprocess.CompletedProcess(command, 0, json.dumps({"ok": True}), "")
        return subprocess.CompletedProcess(command, 1, "", "unexpected command")


class LibTVHappyHorseProviderTests(unittest.TestCase):
    def config(self, directory):
        return LibTVConfig(project_uuid="project-1", config_dir=directory)

    def test_fixed_model_and_idempotent_paid_run(self):
        storage = MemoryStorage()
        runner = FakeLibTVRunner()
        with tempfile.TemporaryDirectory() as directory:
            provider = LibTVHappyHorseProvider(storage, self.config(directory), runner)
            first = provider.submit(
                image_bytes=b"image",
                prompt="Animate the airflow.",
                aspect_ratio="16:9",
                client_business_id="image_motion_job-1",
            )
            second = provider.submit(
                image_bytes=b"image",
                prompt="This prompt must not start another paid run.",
                aspect_ratio="1:1",
                client_business_id="image_motion_job-1",
            )

        self.assertEqual(PROVIDER_NAME, first["provider"])
        self.assertEqual(first["task_id"], second["task_id"])
        paid_runs = [command for command in runner.commands if "--run" in command]
        self.assertEqual(1, len(paid_runs))
        create = next(command for command in runner.commands if command[1:3] == ["node", "create"])
        settings = [create[index + 1] for index, item in enumerate(create) if item == "-s"]
        self.assertIn(f"model={HAPPY_HORSE_MODEL_NAME}", settings)
        self.assertIn("modeType=frames2video", settings)
        self.assertIn("resolution=1080P", settings)
        self.assertIn("duration=5", settings)
        self.assertNotIn("Nova", " ".join(create))
        self.assertNotIn("Ray", " ".join(create))
        result = provider.poll(first["task_id"])
        self.assertEqual("succeeded", result["status"])
        self.assertEqual(2048, len(result["video_bytes"]))

    def test_restart_recovery_downloads_existing_node_without_rerun(self):
        storage = MemoryStorage()
        runner = FakeLibTVRunner()
        task_id = "image_motion_recovery"
        storage.write_json(
            f"image-motion/libtv-provider/{task_id}.json",
            {
                "task_id": task_id,
                "status": "uncertain",
                "run_requested": True,
                "video_node_key": "video-1",
                "created_at": "2026-08-11T00:00:00Z",
            },
        )
        with tempfile.TemporaryDirectory() as directory:
            provider = LibTVHappyHorseProvider(storage, self.config(directory), runner)
            result = provider.poll(task_id)

        self.assertEqual("succeeded", result["status"])
        self.assertFalse(any("--run" in command for command in runner.commands))
        self.assertTrue(any(command[1] == "download" for command in runner.commands))

    def test_missing_project_fails_before_libtv_command(self):
        storage = MemoryStorage()
        runner = FakeLibTVRunner()
        provider = LibTVHappyHorseProvider(storage, LibTVConfig(project_uuid=""), runner)
        with self.assertRaises(LibTVProviderError):
            provider.submit(
                image_bytes=b"image",
                prompt="Animate.",
                aspect_ratio="16:9",
                client_business_id="image_motion_missing-config",
            )
        self.assertEqual([], runner.commands)


if __name__ == "__main__":
    unittest.main()

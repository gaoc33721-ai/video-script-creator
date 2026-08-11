"""LibTV CLI provider locked to Happy Horse 1.1.

All LibTV canvas operations go through the official ``libtv`` executable.  The
provider deliberately favours at-most-once generation: once ``--run`` has been
requested, recovery only queries/downloads the existing node and never starts a
second paid generation automatically.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import re
import subprocess
import tempfile
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable


HAPPY_HORSE_MODEL_NAME = "Happy Horse 1.1"
HAPPY_HORSE_MODEL_KEY = "happy-horse-1.1"
PROVIDER_NAME = "libtv_happy_horse_1_1"


class LibTVProviderError(RuntimeError):
    pass


@dataclass(frozen=True)
class LibTVConfig:
    project_uuid: str
    cli_path: str = "libtv"
    config_dir: str = ""
    credentials_json: str = ""
    resolution: str = "1080P"
    duration_seconds: int = 5
    recovery_timeout_seconds: int = 1800

    @classmethod
    def from_env(cls) -> "LibTVConfig":
        app_data_dir = os.getenv("APP_DATA_DIR", ".")
        resolution = str(os.getenv("LIBTV_HAPPY_HORSE_RESOLUTION", "1080P")).strip().upper()
        if resolution not in {"720P", "1080P"}:
            resolution = "1080P"
        duration = max(3, min(15, int(os.getenv("LIBTV_HAPPY_HORSE_DURATION", "5"))))
        return cls(
            project_uuid=str(os.getenv("LIBTV_PROJECT_UUID", "")).strip(),
            cli_path=str(os.getenv("LIBTV_CLI_PATH", "libtv")).strip() or "libtv",
            config_dir=str(os.getenv("LIBTV_CONFIG_DIR", os.path.join(app_data_dir, "libtv"))).strip(),
            credentials_json=str(os.getenv("LIBTV_CREDENTIALS_JSON", "")).strip(),
            resolution=resolution,
            duration_seconds=duration,
            recovery_timeout_seconds=max(300, int(os.getenv("LIBTV_RECOVERY_TIMEOUT_SECONDS", "1800"))),
        )


class LibTVHappyHorseProvider:
    def __init__(
        self,
        storage,
        config: LibTVConfig | None = None,
        command_runner: Callable[[list[str], dict[str, str]], subprocess.CompletedProcess[str]] | None = None,
    ):
        self.storage = storage
        self.config = config or LibTVConfig.from_env()
        self.command_runner = command_runner or self._default_command_runner
        self._generation_slot = threading.Semaphore(1)
        self._active_tasks: set[str] = set()
        self._lock = threading.Lock()
        self._model_verified = False

    @staticmethod
    def _now() -> str:
        return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")

    @staticmethod
    def _safe_id(value: str) -> str:
        safe = re.sub(r"[^A-Za-z0-9_-]+", "_", str(value or "")).strip("_")
        return safe[:80] or "image_motion"

    def _state_key(self, task_id: str) -> str:
        return f"image-motion/libtv-provider/{self._safe_id(task_id)}.json"

    def _video_key(self, task_id: str) -> str:
        return f"image-motion/libtv-provider/{self._safe_id(task_id)}.mp4"

    def _read_state(self, task_id: str) -> dict[str, Any]:
        state = self.storage.read_json(self._state_key(task_id), {})
        return state if isinstance(state, dict) else {}

    def _write_state(self, task_id: str, **updates: Any) -> dict[str, Any]:
        state = self._read_state(task_id)
        state.update(updates)
        state["task_id"] = task_id
        state["provider"] = PROVIDER_NAME
        state["model_name"] = HAPPY_HORSE_MODEL_NAME
        state["model_key"] = HAPPY_HORSE_MODEL_KEY
        state["updated_at"] = self._now()
        if not state.get("created_at"):
            state["created_at"] = state["updated_at"]
        if not self.storage.write_json(self._state_key(task_id), state):
            raise LibTVProviderError("无法写入 LibTV 任务状态。")
        return state

    def _prepare_credentials(self) -> dict[str, str]:
        if not self.config.project_uuid:
            raise LibTVProviderError("未配置 LIBTV_PROJECT_UUID，快乐马1.1任务未提交，也不会扣点。")
        env = dict(os.environ)
        if self.config.config_dir:
            config_dir = Path(self.config.config_dir)
            config_dir.mkdir(parents=True, exist_ok=True)
            env["LIBTV_CONFIG_DIR"] = str(config_dir)
            if self.config.credentials_json:
                try:
                    payload = json.loads(self.config.credentials_json)
                except json.JSONDecodeError as exc:
                    raise LibTVProviderError("LIBTV_CREDENTIALS_JSON 不是有效 JSON。") from exc
                credentials_path = config_dir / "credentials.json"
                serialized = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
                if not credentials_path.exists() or credentials_path.read_text(encoding="utf-8") != serialized:
                    temporary_path = config_dir / "credentials.json.tmp"
                    temporary_path.write_text(serialized, encoding="utf-8")
                    os.chmod(temporary_path, 0o600)
                    temporary_path.replace(credentials_path)
        return env

    def _default_command_runner(self, args: list[str], env: dict[str, str]) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            args,
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )

    def _command(self, *args: str) -> dict[str, Any]:
        env = self._prepare_credentials()
        command = [self.config.cli_path, *args]
        result = self.command_runner(command, env)
        if result.returncode != 0:
            detail = str(result.stderr or result.stdout or "LibTV CLI command failed").strip()
            detail = detail[-1200:]
            raise LibTVProviderError(detail)
        output = str(result.stdout or "").strip()
        if not output:
            return {}
        try:
            parsed = json.loads(output)
            return parsed if isinstance(parsed, dict) else {"result": parsed}
        except json.JSONDecodeError:
            for line in reversed(output.splitlines()):
                try:
                    parsed = json.loads(line)
                except json.JSONDecodeError:
                    continue
                return parsed if isinstance(parsed, dict) else {"result": parsed}
        raise LibTVProviderError("LibTV CLI 未返回可解析的 JSON。")

    @staticmethod
    def _node_key(payload: dict[str, Any]) -> str:
        for key in ("nodeKey", "newNodeKey", "id", "node_id"):
            if payload.get(key):
                return str(payload[key])
        node = payload.get("node")
        if isinstance(node, dict):
            for key in ("nodeKey", "newNodeKey", "id"):
                if node.get(key):
                    return str(node[key])
        raise LibTVProviderError("LibTV CLI 返回结果中没有节点 ID。")

    def _verify_model(self) -> None:
        with self._lock:
            if self._model_verified:
                return
            payload = self._command("model", HAPPY_HORSE_MODEL_NAME)
            if str(payload.get("modelKey") or "") != HAPPY_HORSE_MODEL_KEY:
                raise LibTVProviderError("LibTV 当前目录未提供指定的 Happy Horse 1.1 模型。")
            self._model_verified = True

    def _query_node(self, node: str) -> dict[str, Any]:
        return self._command("node", node, "-p", self.config.project_uuid)

    def _find_node(self, name: str) -> str:
        try:
            return self._node_key(self._query_node(name))
        except LibTVProviderError:
            return ""

    def _download_video(self, video_node_key: str, output_dir: str) -> bytes:
        self._command(
            "download",
            "-p",
            self.config.project_uuid,
            "-n",
            video_node_key,
            "-o",
            output_dir,
            "--without-ai-watermark",
            "--vip",
        )
        candidates = sorted(Path(output_dir).rglob("*.mp4"), key=lambda item: item.stat().st_mtime, reverse=True)
        if not candidates:
            raise LibTVProviderError("LibTV 节点尚未返回可下载的视频。")
        data = candidates[0].read_bytes()
        if len(data) < 1024:
            raise LibTVProviderError("LibTV 下载结果为空或不是有效视频。")
        return data

    def submit(
        self,
        *,
        image_bytes: bytes,
        prompt: str,
        aspect_ratio: str,
        client_business_id: str,
        lock_end_frame: bool = False,
    ) -> dict[str, Any]:
        del aspect_ratio, lock_end_frame
        task_id = self._safe_id(client_business_id)
        existing = self._read_state(task_id)
        if existing.get("status") == "succeeded":
            return self._submit_result(task_id, existing)
        if existing.get("run_requested"):
            return self._submit_result(task_id, existing)

        self._write_state(task_id, status="queued", current_step="等待 LibTV 快乐马1.1生成槽位")
        with self._generation_slot:
            with self._lock:
                self._active_tasks.add(task_id)
            try:
                self._verify_model()
                with tempfile.TemporaryDirectory(prefix=f"libtv-{task_id}-") as temp_dir:
                    source_path = Path(temp_dir) / "source.png"
                    source_path.write_bytes(image_bytes)
                    input_name = f"vsp-{task_id}-input"
                    video_name = f"vsp-{task_id}-video"

                    state = self._read_state(task_id)
                    input_node_key = str(state.get("input_node_key") or self._find_node(input_name))
                    if not input_node_key:
                        uploaded = self._command(
                            "upload",
                            input_name,
                            "-p",
                            self.config.project_uuid,
                            "-f",
                            str(source_path),
                            "-t",
                            "image",
                        )
                        input_node_key = self._node_key(uploaded)
                    self._write_state(task_id, status="preparing", input_node_key=input_node_key)
                    state = self._read_state(task_id)

                    video_node_key = str(state.get("video_node_key") or self._find_node(video_name))
                    if not video_node_key:
                        created = self._command(
                            "node",
                            "create",
                            video_name,
                            "-p",
                            self.config.project_uuid,
                            "-t",
                            "video",
                            "--left",
                            input_node_key,
                            "-s",
                            f"model={HAPPY_HORSE_MODEL_NAME}",
                            "-s",
                            "modeType=frames2video",
                            "-s",
                            f"resolution={self.config.resolution}",
                            "-s",
                            f"duration={self.config.duration_seconds}",
                            "--prompt",
                            prompt,
                        )
                        video_node_key = self._node_key(created)
                    self._write_state(
                        task_id,
                        status="processing",
                        current_step="LibTV 快乐马1.1生成中",
                        input_node_key=input_node_key,
                        video_node_key=video_node_key,
                        run_requested=True,
                        run_requested_at=self._now(),
                    )

                    self._command("node", video_node_key, "-p", self.config.project_uuid, "--run")
                    video_bytes = self._download_video(video_node_key, temp_dir)
                    video_key = self._video_key(task_id)
                    self.storage.write_file_bytes(video_key, video_bytes, content_type="video/mp4")
                    state = self._write_state(
                        task_id,
                        status="succeeded",
                        current_step="LibTV 快乐马1.1生成完成",
                        video_key=video_key,
                        completed_at=self._now(),
                    )
                    return self._submit_result(task_id, state)
            except Exception as exc:
                state = self._read_state(task_id)
                uncertain = bool(state.get("run_requested"))
                self._write_state(
                    task_id,
                    status="uncertain" if uncertain else "failed",
                    current_step="LibTV 任务结果待核对" if uncertain else "LibTV 任务提交失败",
                    failure_message=str(exc),
                    completed_at=self._now(),
                )
                raise
            finally:
                with self._lock:
                    self._active_tasks.discard(task_id)

    def _submit_result(self, task_id: str, state: dict[str, Any]) -> dict[str, Any]:
        return {
            "task_id": task_id,
            "provider": PROVIDER_NAME,
            "metadata": {
                "provider": PROVIDER_NAME,
                "model_name": HAPPY_HORSE_MODEL_NAME,
                "model_key": HAPPY_HORSE_MODEL_KEY,
                "project_uuid": self.config.project_uuid,
                "input_node_key": state.get("input_node_key", ""),
                "video_node_key": state.get("video_node_key", ""),
                "resolution": self.config.resolution,
                "duration_seconds": self.config.duration_seconds,
            },
        }

    def poll(self, task_id: str) -> dict[str, Any]:
        state = self._read_state(task_id)
        if not state:
            return {"status": "failed", "message": "LibTV 任务状态不存在，未自动重新扣点。"}
        if state.get("status") == "succeeded" and state.get("video_key"):
            try:
                video_bytes = self.storage.read_file_bytes(str(state["video_key"]))
            except Exception as exc:
                return {"status": "failed", "message": f"读取 LibTV 视频失败：{exc}"}
            return {"status": "succeeded", "video_bytes": video_bytes, "message": ""}
        if state.get("status") == "failed":
            return {"status": "failed", "message": str(state.get("failure_message") or "LibTV 任务失败。")}
        with self._lock:
            active = task_id in self._active_tasks
        if active:
            return {"status": "processing", "message": "LibTV 快乐马1.1生成中"}

        video_node_key = str(state.get("video_node_key") or "")
        if state.get("run_requested") and video_node_key:
            try:
                with tempfile.TemporaryDirectory(prefix=f"libtv-recover-{self._safe_id(task_id)}-") as temp_dir:
                    video_bytes = self._download_video(video_node_key, temp_dir)
                video_key = self._video_key(task_id)
                self.storage.write_file_bytes(video_key, video_bytes, content_type="video/mp4")
                self._write_state(task_id, status="succeeded", video_key=video_key, completed_at=self._now())
                return {"status": "succeeded", "video_bytes": video_bytes, "message": ""}
            except Exception:
                pass

        requested_at = str(state.get("run_requested_at") or state.get("created_at") or "")
        try:
            age = (dt.datetime.now(dt.timezone.utc) - dt.datetime.fromisoformat(requested_at.replace("Z", "+00:00"))).total_seconds()
        except (TypeError, ValueError):
            age = 0
        if age >= self.config.recovery_timeout_seconds or state.get("status") == "uncertain":
            return {
                "status": "failed",
                "message": "LibTV 任务结果无法确认。为避免重复扣点，系统未自动重新运行，请在 LibTV 画布核对原节点。",
            }
        return {"status": "processing", "message": "等待 LibTV 快乐马1.1结果"}

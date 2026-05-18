import base64
import dataclasses
import hashlib
import hmac
import time
import uuid
from typing import Any
from urllib.parse import urlencode

import requests


class LiblibAIError(RuntimeError):
    pass


@dataclasses.dataclass(frozen=True)
class LiblibAIConfig:
    access_key: str
    secret_key: str
    base_url: str = "https://openapi.liblibai.cloud"
    template_uuid: str = "5d7e67009b344550bc1aa6ccbfa1d7f4"
    aspect_ratio: str = "landscape"
    width: int = 1280
    height: int = 720
    include_image_size: bool = False
    steps: int = 20
    image_count: int = 1
    request_timeout_seconds: int = 30
    poll_timeout_seconds: int = 240
    poll_interval_seconds: float = 3.0
    max_prompt_length: int = 1800


class LiblibAIClient:
    SUCCESS_STATUSES = {5}
    FAILED_STATUSES = {6, 7}

    def __init__(self, config: LiblibAIConfig):
        self.config = config
        self.base_url = config.base_url.rstrip("/")
        self.session = requests.Session()

    def generate_image(self, prompt: str) -> tuple[bytes, dict[str, Any]]:
        generate_uuid = self.submit_text_to_image(prompt)
        status_payload = self.wait_for_result(generate_uuid)
        image_info = self._first_image(status_payload)
        image_url = image_info.get("imageUrl") or image_info.get("image_url") or image_info.get("url")
        if not image_url:
            raise LiblibAIError(f"LibLibAI returned no image URL for task {generate_uuid}.")
        image_bytes = self.download_image(str(image_url))
        return image_bytes, {
            "generate_uuid": generate_uuid,
            "image_url": image_url,
            "seed": image_info.get("seed") or status_payload.get("seed"),
            "raw_status": self._compact_status_payload(status_payload),
        }

    def submit_text_to_image(self, prompt: str) -> str:
        prompt_text = " ".join(str(prompt or "").split())[: self.config.max_prompt_length]
        payload = {
            "templateUuid": self.config.template_uuid,
            "generateParams": {
                "prompt": prompt_text,
                "aspectRatio": self.config.aspect_ratio,
                "imgCount": int(self.config.image_count),
                "steps": int(self.config.steps),
            },
        }
        if self.config.include_image_size:
            payload["generateParams"]["imageSize"] = {
                "width": int(self.config.width),
                "height": int(self.config.height),
            }
        body = self._post("/api/generate/webui/text2img/ultra", payload)
        data = body.get("data") if isinstance(body.get("data"), dict) else {}
        generate_uuid = data.get("generateUuid") or data.get("generate_uuid") or body.get("generateUuid")
        if not generate_uuid:
            raise LiblibAIError(f"LibLibAI did not return generateUuid: {body}")
        return str(generate_uuid)

    def wait_for_result(self, generate_uuid: str) -> dict[str, Any]:
        deadline = time.monotonic() + max(1, int(self.config.poll_timeout_seconds))
        last_payload: dict[str, Any] = {}
        while time.monotonic() < deadline:
            payload = self.query_status(generate_uuid)
            last_payload = payload
            status = self._status_code(payload)
            if status in self.SUCCESS_STATUSES:
                return payload
            if status in self.FAILED_STATUSES:
                message = payload.get("generateMsg") or payload.get("msg") or payload.get("message") or "unknown error"
                raise LiblibAIError(f"LibLibAI image generation failed for {generate_uuid}: {message}")
            time.sleep(max(0.5, float(self.config.poll_interval_seconds)))
        raise LiblibAIError(f"LibLibAI image generation timed out for {generate_uuid}: {last_payload}")

    def query_status(self, generate_uuid: str) -> dict[str, Any]:
        body = self._post("/api/generate/webui/status", {"generateUuid": generate_uuid})
        data = body.get("data")
        return data if isinstance(data, dict) else body

    def download_image(self, image_url: str) -> bytes:
        response = self.session.get(
            image_url,
            timeout=max(10, int(self.config.request_timeout_seconds)),
            headers={"User-Agent": "video-script-liblibai-poc/1.0"},
        )
        response.raise_for_status()
        content_type = response.headers.get("Content-Type", "")
        if "image" not in content_type.lower() and len(response.content) < 1024:
            raise LiblibAIError(f"LibLibAI image download did not return image data: {content_type}")
        return response.content

    def _post(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        response = self.session.post(
            self._signed_url(path),
            json=payload,
            timeout=max(10, int(self.config.request_timeout_seconds)),
            headers={"Content-Type": "application/json"},
        )
        try:
            body = response.json()
        except ValueError as exc:
            raise LiblibAIError(f"LibLibAI returned non-JSON response HTTP {response.status_code}") from exc
        if response.status_code >= 400:
            raise LiblibAIError(f"LibLibAI HTTP {response.status_code}: {body}")
        code = body.get("code")
        if code not in (None, 0, "0"):
            message = body.get("msg") or body.get("message") or body.get("error") or body
            raise LiblibAIError(f"LibLibAI API error {code}: {message}")
        return body

    def _signed_url(self, path: str) -> str:
        normalized_path = "/" + str(path or "").lstrip("/")
        timestamp = str(int(time.time() * 1000))
        nonce = uuid.uuid4().hex
        signature_text = f"{normalized_path}&{timestamp}&{nonce}"
        digest = hmac.new(
            self.config.secret_key.encode("utf-8"),
            signature_text.encode("utf-8"),
            hashlib.sha1,
        ).digest()
        signature = base64.urlsafe_b64encode(digest).decode("utf-8").rstrip("=")
        query = urlencode(
            {
                "AccessKey": self.config.access_key,
                "Signature": signature,
                "Timestamp": timestamp,
                "SignatureNonce": nonce,
            }
        )
        return f"{self.base_url}{normalized_path}?{query}"

    @staticmethod
    def _status_code(payload: dict[str, Any]) -> int | None:
        status = payload.get("generateStatus") or payload.get("status")
        try:
            return int(status)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _first_image(payload: dict[str, Any]) -> dict[str, Any]:
        images = payload.get("images") or payload.get("imageList") or []
        if isinstance(images, list) and images:
            first = images[0]
            if isinstance(first, dict):
                return first
            if isinstance(first, str):
                return {"imageUrl": first}
        return {}

    @staticmethod
    def _compact_status_payload(payload: dict[str, Any]) -> dict[str, Any]:
        compact = dict(payload or {})
        images = compact.get("images")
        if isinstance(images, list):
            compact["images"] = [
                {
                    key: value
                    for key, value in (item.items() if isinstance(item, dict) else [])
                    if key in {"imageUrl", "seed", "auditStatus"}
                }
                for item in images[:3]
            ]
        return compact

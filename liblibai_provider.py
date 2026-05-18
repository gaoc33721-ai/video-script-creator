import base64
import dataclasses
import hashlib
import hmac
import time
import uuid
from typing import Any
from urllib.parse import urlencode, urljoin

import requests


class LiblibAIError(RuntimeError):
    pass


@dataclasses.dataclass(frozen=True)
class LiblibAIConfig:
    access_key: str
    secret_key: str
    base_url: str = "https://openapi.liblibai.cloud"
    template_uuid: str = "5d7e67009b344550bc1aa6ccbfa1d7f4"
    img2img_template_uuid: str = "07e00af4fc464c7ab55ff906f8acf1b7"
    aspect_ratio: str = "landscape"
    width: int = 1280
    height: int = 720
    include_image_size: bool = False
    steps: int = 20
    image_count: int = 1
    request_timeout_seconds: int = 90
    poll_timeout_seconds: int = 240
    poll_interval_seconds: float = 3.0
    max_prompt_length: int = 1800
    reference_control_type: str = "depth"
    reference_mode: str = "img2img"
    fallback_to_controlnet: bool = True


class LiblibAIClient:
    SUCCESS_STATUSES = {5}
    FAILED_STATUSES = {6, 7}

    def __init__(self, config: LiblibAIConfig):
        self.config = config
        self.base_url = config.base_url.rstrip("/")
        self.session = requests.Session()

    def generate_image(self, prompt: str, reference_image_bytes: bytes | None = None) -> tuple[bytes, dict[str, Any]]:
        reference_image_url = ""
        fallback_error = ""
        if reference_image_bytes:
            reference_image_url = self.upload_image(reference_image_bytes)
            if self.config.reference_mode.lower() in {"img2img", "image_to_image", "image-to-image"}:
                try:
                    generate_uuid = self.submit_image_to_image(prompt, reference_image_url)
                    mode = "image-to-image"
                except LiblibAIError as exc:
                    if not self.config.fallback_to_controlnet:
                        raise
                    fallback_error = str(exc)
                    generate_uuid = self.submit_reference_to_image(prompt, reference_image_url)
                    mode = "reference-controlnet"
            else:
                generate_uuid = self.submit_reference_to_image(prompt, reference_image_url)
                mode = "reference-controlnet"
        else:
            generate_uuid = self.submit_text_to_image(prompt)
            mode = "text-to-image"
        status_payload = self.wait_for_result(generate_uuid)
        image_info = self._first_image(status_payload)
        image_url = image_info.get("imageUrl") or image_info.get("image_url") or image_info.get("url")
        if not image_url:
            raise LiblibAIError(f"LibLibAI returned no image URL for task {generate_uuid}.")
        image_bytes = self.download_image(str(image_url))
        return image_bytes, {
            "generate_uuid": generate_uuid,
            "image_url": image_url,
            "reference_image_url": reference_image_url,
            "mode": mode,
            "control_type": self.config.reference_control_type if reference_image_url else "",
            "fallback_error": fallback_error,
            "seed": image_info.get("seed") or status_payload.get("seed"),
            "raw_status": self._compact_status_payload(status_payload),
        }

    def submit_text_to_image(self, prompt: str) -> str:
        payload = self._ultra_payload(prompt)
        body = self._post("/api/generate/webui/text2img/ultra", payload)
        data = body.get("data") if isinstance(body.get("data"), dict) else {}
        generate_uuid = data.get("generateUuid") or data.get("generate_uuid") or body.get("generateUuid")
        if not generate_uuid:
            raise LiblibAIError(f"LibLibAI did not return generateUuid: {body}")
        return str(generate_uuid)

    def submit_reference_to_image(self, prompt: str, control_image_url: str) -> str:
        payload = self._ultra_payload(prompt)
        payload["generateParams"]["controlnet"] = {
            "controlType": self.config.reference_control_type,
            "controlImage": control_image_url,
        }
        body = self._post("/api/generate/webui/text2img/ultra", payload)
        data = body.get("data") if isinstance(body.get("data"), dict) else {}
        generate_uuid = data.get("generateUuid") or data.get("generate_uuid") or body.get("generateUuid")
        if not generate_uuid:
            raise LiblibAIError(f"LibLibAI did not return generateUuid: {body}")
        return str(generate_uuid)

    def submit_image_to_image(self, prompt: str, source_image_url: str) -> str:
        prompt_text = " ".join(str(prompt or "").split())[: self.config.max_prompt_length]
        payload = {
            "templateUuid": self.config.img2img_template_uuid,
            "generateParams": {
                "prompt": prompt_text,
                "imgCount": int(self.config.image_count),
                "sourceImage": source_image_url,
                "controlnet": {
                    "controlType": self.config.reference_control_type,
                    "controlImage": source_image_url,
                },
            },
        }
        body = self._post("/api/generate/webui/img2img/ultra", payload)
        data = body.get("data") if isinstance(body.get("data"), dict) else {}
        generate_uuid = data.get("generateUuid") or data.get("generate_uuid") or body.get("generateUuid")
        if not generate_uuid:
            raise LiblibAIError(f"LibLibAI did not return generateUuid: {body}")
        return str(generate_uuid)

    def upload_image(self, image_bytes: bytes, filename: str | None = None) -> str:
        if not image_bytes:
            raise LiblibAIError("Cannot upload an empty LibLibAI reference image.")
        safe_filename = filename or f"storyboard_reference_{uuid.uuid4().hex}.png"
        if "." in safe_filename:
            name, extension = safe_filename.rsplit(".", 1)
        else:
            name, extension = safe_filename, "png"
            safe_filename = f"{safe_filename}.png"
        signature_body = self._post(
            "/api/generate/upload/signature",
            {"name": name or "storyboard_reference", "extension": extension.lower() or "png"},
        )
        sign_data = signature_body.get("data") if isinstance(signature_body.get("data"), dict) else {}
        post_url = str(sign_data.get("postUrl") or "").strip()
        key = str(sign_data.get("key") or "").strip()
        if not post_url or not key:
            raise LiblibAIError(f"LibLibAI upload signature missing postUrl/key: {signature_body}")
        form_fields = {
            "x-oss-signature": sign_data.get("xossSignature") or sign_data.get("xOssSignature") or sign_data.get("x-oss-signature"),
            "x-oss-date": sign_data.get("xossDate") or sign_data.get("xOssDate") or sign_data.get("x-oss-date"),
            "x-oss-signature-version": sign_data.get("xossSignatureVersion") or sign_data.get("xOssSignatureVersion") or sign_data.get("x-oss-signature-version"),
            "policy": sign_data.get("policy"),
            "key": key,
            "x-oss-credential": sign_data.get("xossCredential") or sign_data.get("xOssCredential") or sign_data.get("x-oss-credential"),
            "x-oss-expires": sign_data.get("xossExpires") or sign_data.get("xOssExpires") or sign_data.get("x-oss-expires"),
        }
        data = {field: str(value) for field, value in form_fields.items() if value is not None and value != ""}
        files = {"file": (safe_filename, image_bytes, self._mime_type_for_extension(extension))}
        response = self.session.post(
            post_url,
            data=data,
            files=files,
            timeout=max(10, int(self.config.request_timeout_seconds)),
        )
        if response.status_code >= 400:
            raise LiblibAIError(f"LibLibAI reference image upload HTTP {response.status_code}: {response.text[:300]}")
        return urljoin(f"{post_url.rstrip('/')}/", key.lstrip("/"))

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

    def _ultra_payload(self, prompt: str) -> dict[str, Any]:
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
        return payload

    def _post(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        try:
            response = self.session.post(
                self._signed_url(path),
                json=payload,
                timeout=max(10, int(self.config.request_timeout_seconds)),
                headers={"Content-Type": "application/json"},
            )
        except requests.RequestException as exc:
            raise LiblibAIError(f"LibLibAI request failed: {exc}") from exc
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
    def _mime_type_for_extension(extension: str) -> str:
        normalized = str(extension or "png").lower().lstrip(".")
        if normalized in {"jpg", "jpeg"}:
            return "image/jpeg"
        if normalized == "webp":
            return "image/webp"
        return "image/png"

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

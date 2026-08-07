"""Independent API workflow for selling-point image motion assets and jobs."""

from __future__ import annotations

import hashlib
import io
import json
import os
import re
import threading
import urllib.parse
import uuid
import zipfile
from typing import Any, Callable

from fastapi import Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel, Field

from image_motion_service import (
    analyze_creative_image,
    assess_component_motion,
    assess_video_fidelity,
    build_motion_prompt,
    enhance_image_resolution,
    mute_video,
    normalize_generated_video,
    prepare_motion_source,
    render_stable_motion_video,
    validate_motion_plan,
)


CREATIVE_ASSETS_KEY = "creative_assets.json"
MOTION_PLANS_KEY = "motion_plans.json"
IMAGE_MOTION_JOBS_KEY = "image_motion_jobs.json"
GENERATIVE_PRESETS = {"steam", "liquid"}
RAY2_PRESETS = {"component", "flow"}
CONTENT_TYPES = {"image/png", "image/jpeg", "image/webp"}
MAX_FILE_BYTES = 20 * 1024 * 1024


class MotionPlanRequest(BaseModel):
    text_policy: str = Field(default="visual_only", pattern="^(visual_only|preserve_title_logo)$")
    focus: str = Field(default="effect", pattern="^(product|effect|background)$")
    preset: str = Field(default="auto", pattern="^(auto|flow|liquid|steam|glow|component|camera)$")
    direction: str = Field(default="", max_length=120)
    intensity: str = Field(default="standard", pattern="^(subtle|standard|strong)$")
    aspect_ratio: str = Field(default="source", pattern="^(source|1:1|9:16|16:9)$")
    custom_instruction: str = Field(default="", max_length=1000)


class ImageMotionSubmitRequest(BaseModel):
    creative_asset_ids: list[str] = Field(min_length=1, max_length=10)
    idempotency_key: str = Field(default="", max_length=160)


class ImageMotionRegenerateRequest(BaseModel):
    action: str = Field(default="preserve_composition", pattern="^(preserve_composition|lower_intensity|alternate_effect)$")


class ImageMotionExportRequest(BaseModel):
    job_ids: list[str] = Field(min_length=1, max_length=50)
    muted: bool = False


class ImageMotionWorkflow:
    def __init__(
        self,
        storage,
        script_tasks_loader: Callable[[], list[dict]],
        provider_submit: Callable[..., dict] | None = None,
        provider_poll: Callable[[str], dict] | None = None,
        component_provider_submit: Callable[..., dict] | None = None,
        component_provider_poll: Callable[[str], dict] | None = None,
    ):
        self.storage = storage
        self.script_tasks_loader = script_tasks_loader
        self.provider_submit = provider_submit
        self.provider_poll = provider_poll
        self.component_provider_submit = component_provider_submit
        self.component_provider_poll = component_provider_poll
        self.lock = threading.Lock()
        self.enabled = os.getenv("IMAGE_MOTION_ENABLED", "true").strip().lower() in {"1", "true", "yes", "on"}
        self.generative_enabled = os.getenv("IMAGE_MOTION_GENERATIVE_ENABLED", "true").strip().lower() in {"1", "true", "yes", "on"}

    @staticmethod
    def now() -> str:
        import datetime as dt

        return dt.datetime.utcnow().isoformat(timespec="seconds") + "Z"

    def read_list(self, key: str) -> list[dict]:
        value = self.storage.read_json(key, [])
        return value if isinstance(value, list) else []

    def write_list(self, key: str, value: list[dict], limit: int) -> None:
        if not self.storage.write_json(key, value[:limit]):
            raise RuntimeError(f"无法写入任务存储：{key}")

    def assets(self) -> list[dict]:
        return self.read_list(CREATIVE_ASSETS_KEY)

    def plans(self) -> list[dict]:
        return self.read_list(MOTION_PLANS_KEY)

    def jobs(self) -> list[dict]:
        return self.read_list(IMAGE_MOTION_JOBS_KEY)

    def asset(self, asset_id: str) -> dict | None:
        return next((item for item in self.assets() if item.get("id") == asset_id), None)

    @staticmethod
    def working_image_key(asset: dict) -> str:
        return str(asset.get("working_key") or asset.get("original_key") or "")

    def enhance_asset_record(self, asset: dict, *, force: bool = False) -> tuple[dict, bool]:
        current = dict(asset.get("resolution_enhancement") or {})
        if current.get("status") and not force:
            return asset, False
        source_bytes = self.storage.read_file_bytes(asset["original_key"])
        working_bytes, enhancement = enhance_image_resolution(source_bytes)
        working_key = asset["original_key"]
        working_content_type = asset.get("content_type") or "image/png"
        if enhancement.get("applied"):
            working_key = f"creative-assets/{asset['id']}/enhanced.png"
            working_content_type = "image/png"
            self.storage.write_file_bytes(working_key, working_bytes, content_type=working_content_type)

        analysis = analyze_creative_image(
            working_bytes,
            filename=asset.get("filename") or "",
            category=asset.get("category") or "",
            model=asset.get("model") or "",
            feature=asset.get("feature") or "",
        )
        if enhancement.get("applied"):
            analysis["warnings"] = [
                item for item in (analysis.get("warnings") or []) if "960" not in str(item)
            ]
            analysis["warnings"].insert(
                0,
                f"原图 {enhancement['source_width']}×{enhancement['source_height']}，"
                f"已自动保真增强至 {enhancement['output_width']}×{enhancement['output_height']}。",
            )
        elif enhancement.get("status") == "too_small":
            analysis["blocking_issues"] = [
                "素材短边低于360像素，无法安全自动增强，请上传更清晰的卖点图。"
                if "720" in str(item)
                else item
                for item in (analysis.get("blocking_issues") or [])
            ]
        elif enhancement.get("status") == "fidelity_failed":
            analysis["blocking_issues"] = [
                "素材自动增强未通过保真检查，请上传更清晰的卖点图。"
                if "720" in str(item)
                else item
                for item in (analysis.get("blocking_issues") or [])
            ]
        analysis["ready"] = not (analysis.get("blocking_issues") or [])
        analysis["resolution_enhancement"] = enhancement

        updated = dict(asset)
        updated.update(
            {
                "source_width": enhancement.get("source_width"),
                "source_height": enhancement.get("source_height"),
                "working_width": enhancement.get("output_width"),
                "working_height": enhancement.get("output_height"),
                "working_key": working_key,
                "working_content_type": working_content_type,
                "working_file_hash": hashlib.sha256(working_bytes).hexdigest(),
                "resolution_enhancement": enhancement,
                "analysis": analysis,
                "updated_at": self.now(),
            }
        )
        return updated, True

    def ensure_resolution_enhancements(self, category: str = "", model: str = "") -> list[dict]:
        with self.lock:
            assets = self.assets()
            changed = False
            for index, asset in enumerate(assets):
                if category and asset.get("category") != category:
                    continue
                if model and asset.get("model") != model:
                    continue
                if (asset.get("resolution_enhancement") or {}).get("status"):
                    continue
                short_side = min(int(asset.get("source_width") or 0), int(asset.get("source_height") or 0))
                if not short_side or short_side >= 720:
                    continue
                try:
                    assets[index], enhanced = self.enhance_asset_record(asset)
                    changed = changed or enhanced
                except Exception as exc:
                    failed = dict(asset)
                    failed["resolution_enhancement"] = {
                        "applied": False,
                        "status": "error",
                        "message": str(exc),
                    }
                    assets[index] = failed
                    changed = True
            if changed:
                self.write_list(CREATIVE_ASSETS_KEY, assets, 1000)
            return assets

    def plan(self, asset_id: str) -> dict | None:
        return next((item for item in self.plans() if item.get("creative_asset_id") == asset_id), None)

    def public_asset(self, asset: dict) -> dict:
        result = dict(asset)
        result.pop("original_key", None)
        result.pop("working_key", None)
        result["preview_url"] = f"/api/creative-assets/{urllib.parse.quote(str(asset.get('id') or ''), safe='')}/image"
        result["motion_plan"] = self.plan(str(asset.get("id") or ""))
        return result

    def public_job(self, job: dict) -> dict:
        result = dict(job)
        result.pop("video_key", None)
        result.pop("prepared_image_key", None)
        result["task_type"] = "image_motion"
        result["type_label"] = "卖点图动效"
        result["download_allowed"] = bool(job.get("status") == "succeeded" and job.get("qa_status") == "passed")
        if result["download_allowed"]:
            job_id = urllib.parse.quote(str(job.get("id") or ""), safe="")
            version = urllib.parse.quote(str(job.get("updated_at") or ""), safe="")
            result["preview_url"] = f"/api/image-motion/jobs/{job_id}/video?v={version}"
            result["download_url"] = f"/api/image-motion/jobs/{job_id}/video?download=1&v={version}"
            result["muted_download_url"] = f"/api/image-motion/jobs/{job_id}/video?download=1&muted=1&v={version}"
        else:
            result["preview_url"] = ""
            result["download_url"] = ""
            result["muted_download_url"] = ""
        return result

    def default_plan(self, asset: dict) -> dict:
        analysis = asset.get("analysis") or {}
        return validate_motion_plan(
            {
                "id": uuid.uuid4().hex[:12],
                "creative_asset_id": asset["id"],
                "text_policy": "visual_only",
                "focus": analysis.get("recommended_focus") or "effect",
                "preset": analysis.get("recommended_preset") or "camera",
                "direction": analysis.get("recommended_direction") or "轻微推进",
                "intensity": "standard",
                "aspect_ratio": "source",
                "duration_seconds": 5,
                "revision": 1,
                "created_at": self.now(),
                "updated_at": self.now(),
            }
        )

    def save_plan(self, asset_id: str, values: dict[str, Any]) -> dict:
        asset = self.asset(asset_id)
        if not asset:
            raise HTTPException(status_code=404, detail="卖点图素材不存在。")
        with self.lock:
            plans = self.plans()
            current = next((item for item in plans if item.get("creative_asset_id") == asset_id), None)
            base = current or self.default_plan(asset)
            base.update({key: value for key, value in values.items() if value is not None})
            base["creative_asset_id"] = asset_id
            base["revision"] = int((current or {}).get("revision") or 0) + (1 if current else 0)
            base["updated_at"] = self.now()
            normalized = validate_motion_plan(base)
            plans = [item for item in plans if item.get("creative_asset_id") != asset_id]
            plans.insert(0, normalized)
            self.write_list(MOTION_PLANS_KEY, plans, 1000)
        return normalized

    def next_version(self, asset_id: str) -> int:
        versions = [int(item.get("version") or 0) for item in self.jobs() if item.get("creative_asset_id") == asset_id]
        return max(versions, default=0) + 1

    def create_job(self, asset: dict, plan: dict, idempotency_key: str, parent_job_id: str = "") -> tuple[dict, bool]:
        fingerprint_source = idempotency_key or f"{asset['id']}:{plan.get('revision', 1)}"
        fingerprint = hashlib.sha256(fingerprint_source.encode("utf-8")).hexdigest()
        with self.lock:
            jobs = self.jobs()
            existing = next((item for item in jobs if item.get("idempotency_fingerprint") == fingerprint), None)
            if existing:
                return existing, True
            now = self.now()
            job = {
                "id": uuid.uuid4().hex[:12],
                "source": "selling_point_image",
                "creative_asset_id": asset["id"],
                "category": asset.get("category", ""),
                "model": asset.get("model", ""),
                "feature": asset.get("feature", ""),
                "asset_filename": asset.get("filename", ""),
                "motion_plan": dict(plan),
                "version": self.next_version(asset["id"]),
                "parent_job_id": parent_job_id,
                "idempotency_fingerprint": fingerprint,
                "status": "queued",
                "progress": 5,
                "current_step": "已提交，等待生成",
                "created_at": now,
                "updated_at": now,
                "qa_status": "pending",
                "qa_result": {},
                "generation_mode": "",
                "fallback_reason": "",
                "failure_message": "",
            }
            jobs.insert(0, job)
            self.write_list(IMAGE_MOTION_JOBS_KEY, jobs, 1000)
        threading.Thread(target=self.run_job, args=(job["id"],), daemon=True).start()
        return job, False

    def update_job(self, job_id: str, **updates) -> dict | None:
        with self.lock:
            jobs = self.jobs()
            found = None
            for item in jobs:
                if item.get("id") == job_id:
                    item.update(updates)
                    item["updated_at"] = self.now()
                    found = item
                    break
            if found:
                self.write_list(IMAGE_MOTION_JOBS_KEY, jobs, 1000)
            return found


    def fail_job(self, job_id: str, message: str, qa_result: dict[str, Any] | None = None) -> None:
        self.update_job(
            job_id,
            status="failed",
            progress=100,
            current_step="\u672a\u751f\u6210\u5408\u683c\u7684\u76ee\u6807\u52a8\u6548",
            completed_at=self.now(),
            qa_status="failed",
            qa_result=qa_result or {"status": "failed", "message": message},
            failure_message=message,
        )

    def render_fallback(self, job_id: str, reason: str = "") -> None:
        job = next((item for item in self.jobs() if item.get("id") == job_id), None)
        asset = self.asset(str((job or {}).get("creative_asset_id") or ""))
        if not job or not asset:
            raise RuntimeError("动效任务关联的素材不存在。")
        plan = validate_motion_plan(job.get("motion_plan") or {})
        source_bytes = self.storage.read_file_bytes(self.working_image_key(asset))
        prepared, prepared_meta = prepare_motion_source(
            source_bytes,
            asset.get("analysis") or {},
            ratio=plan["aspect_ratio"],
            text_policy=plan["text_policy"],
            quality=plan["quality"],
        )
        prepared_key = f"image-motion/prepared/{job_id}.png"
        self.storage.write_file_bytes(prepared_key, prepared, content_type="image/png")
        preset = plan["preset"]
        if preset == "auto":
            preset = str((asset.get("analysis") or {}).get("recommended_preset") or "camera")
        video = render_stable_motion_video(
            prepared,
            preset=preset,
            intensity=plan["intensity"],
            text_policy=plan["text_policy"],
            duration_seconds=5,
            fps=24,
            protected_regions=(asset.get("analysis") or {}).get("protected_regions") or [],
            metadata={"AssetId": asset["id"], "JobId": job_id, "Version": job.get("version")},
        )
        motion_qa = None
        if preset == "flow":
            motion_qa = assess_component_motion(
                video,
                target_region={"x": 0.10, "y": 0.16, "width": 0.80, "height": 0.72},
                motion_name="\u70ed\u6d41",
            )
            if motion_qa.get("status") != "passed":
                self.fail_job(job_id, f"\u70ed\u6d41\u52a8\u6548\u8d28\u68c0\u672a\u901a\u8fc7\uff1a{motion_qa.get('message')}", qa_result=motion_qa)
                return
        video_key = f"image-motion/videos/{job_id}_v{job.get('version', 1)}.mp4"
        self.storage.write_file_bytes(video_key, video, content_type="video/mp4")
        self.update_job(
            job_id,
            status="succeeded",
            progress=100,
            current_step="生成完成",
            completed_at=self.now(),
            generation_mode="stable_flow_overlay" if preset == "flow" else "stable_template",
            fallback_reason=reason,
            prepared_image_key=prepared_key,
            prepared_metadata=prepared_meta,
            video_key=video_key,
            qa_status="passed",
            qa_result={"status": "passed", "score": 100, "message": "原图像素合成模板已通过保真检查。"},
        )

    def run_job(self, job_id: str) -> None:
        preset = ""
        try:
            job = self.update_job(job_id, status="processing", progress=18, current_step="正在准备原图与保护区")
            asset = self.asset(str((job or {}).get("creative_asset_id") or ""))
            if not job or not asset:
                raise RuntimeError("任务关联的卖点图不存在。")
            analysis = asset.get("analysis") or {}
            if not analysis.get("ready"):
                raise RuntimeError("素材未通过生成前检查。")
            plan = validate_motion_plan(job.get("motion_plan") or {})
            preset = plan["preset"] if plan["preset"] != "auto" else analysis.get("recommended_preset") or "camera"
            is_component = preset == "component"
            is_ray2 = preset in RAY2_PRESETS
            if is_ray2 and plan["text_policy"] != "visual_only":
                raise RuntimeError("\u751f\u6210\u5f0f\u76ee\u6807\u52a8\u6548\u6682\u4e0d\u652f\u6301\u201c\u4fdd\u7559\u6807\u9898\u4e0eLogo\u201d\uff1b\u8bf7\u4f7f\u7528\u7eaf\u753b\u9762\u52a8\u6548\u4ee5\u907f\u514d\u6a21\u578b\u91cd\u753b\u54c1\u724c\u6587\u5b57\u3002")

            provider_submit = self.component_provider_submit if is_ray2 else self.provider_submit
            provider_poll = self.component_provider_poll if is_ray2 else self.provider_poll
            use_provider = (
                self.generative_enabled
                and plan["text_policy"] == "visual_only"
                and (is_ray2 or preset in GENERATIVE_PRESETS)
                and provider_submit is not None
                and provider_poll is not None
            )
            if not use_provider:
                if is_ray2:
                    raise RuntimeError("Bedrock Luma Ray 2 \u76ee\u6807\u52a8\u6548\u8def\u5f84\u4e0d\u53ef\u7528\uff0c\u4efb\u52a1\u672a\u964d\u7ea7\u4e3a\u626b\u5149\u6216\u63a8\u955c\u6a21\u677f\u3002")
                self.render_fallback(job_id)
                return

            source_bytes = self.storage.read_file_bytes(self.working_image_key(asset))
            prepared, prepared_meta = prepare_motion_source(
                source_bytes,
                analysis,
                ratio=plan["aspect_ratio"],
                text_policy=plan["text_policy"],
                quality=plan["quality"],
            )
            prepared_key = f"image-motion/prepared/{job_id}.png"
            self.storage.write_file_bytes(prepared_key, prepared, content_type="image/png")
            prompt = build_motion_prompt(asset, {**plan, "preset": preset})
            provider_result = provider_submit(
                image_bytes=prepared,
                prompt=prompt,
                aspect_ratio=prepared_meta["provider_aspect_ratio"],
                client_business_id=f"image_motion_{job_id}",
            )
            self.update_job(
                job_id,
                status="processing",
                progress=42,
                current_step="生成式自然动效处理中",
                generation_mode=f"luma_ray2_{preset}" if is_ray2 else "generative_video",
                provider_name="luma_ray2" if is_ray2 else str(provider_result.get("provider") or "default"),
                prepared_image_key=prepared_key,
                prepared_metadata=prepared_meta,
                external_task_id=provider_result["task_id"],
                provider_metadata=provider_result.get("metadata") or {},
            )
        except Exception as exc:
            if preset in RAY2_PRESETS:
                self.fail_job(job_id, str(exc))
                return
            try:
                self.render_fallback(job_id, reason=f"生成式路径不可用，已自动降级：{exc}")
            except Exception as fallback_exc:
                self.update_job(
                    job_id,
                    status="failed",
                    progress=100,
                    current_step="生成失败",
                    completed_at=self.now(),
                    qa_status="failed",
                    failure_message=str(fallback_exc),
                )
    def refresh_provider_jobs(self) -> list[dict]:
        active = [item for item in self.jobs() if item.get("status") == "processing" and item.get("external_task_id")]
        for job in active:
            plan = validate_motion_plan(job.get("motion_plan") or {})
            asset = self.asset(str(job.get("creative_asset_id") or ""))
            recommended = str(((asset or {}).get("analysis") or {}).get("recommended_preset") or "camera")
            preset = plan["preset"] if plan["preset"] != "auto" else recommended
            is_component = preset == "component"
            is_flow = preset == "flow"
            is_ray2 = job.get("provider_name") == "luma_ray2" or preset in RAY2_PRESETS
            provider_poll = self.component_provider_poll if is_ray2 else self.provider_poll
            try:
                result = provider_poll(str(job["external_task_id"])) if provider_poll else {"status": "failed"}
                if result.get("status") == "processing":
                    self.update_job(job["id"], progress=65, current_step="供应商生成中")
                    continue
                if is_ray2 and (result.get("status") != "succeeded" or not result.get("video_bytes")):
                    self.fail_job(job["id"], f"Ray 2 \u76ee\u6807\u52a8\u6548\u751f\u6210\u5931\u8d25\uff1a{result.get('message') or 'no video returned'}")
                    continue

                if result.get("status") != "succeeded" or not result.get("video_bytes"):
                    self.render_fallback(job["id"], reason=f"供应商失败，已自动降级：{result.get('message') or '未返回视频'}")
                    continue
                prepared = self.storage.read_file_bytes(job["prepared_image_key"])
                normalized = normalize_generated_video(
                    result["video_bytes"],
                    duration_seconds=5,
                    metadata={"AssetId": job["creative_asset_id"], "JobId": job["id"], "Version": job.get("version")},
                    output_size=(int(job["prepared_metadata"]["width"]), int(job["prepared_metadata"]["height"])),
                )
                qa = assess_video_fidelity(prepared, normalized)
                if is_component or is_flow:
                    motion_region = ((asset or {}).get("analysis") or {}).get("motion_region")
                    if is_flow and not motion_region:
                        motion_region = {"x": 0.08, "y": 0.24, "width": 0.84, "height": 0.70}
                    motion_label = "\u70ed\u6d41" if is_flow else "\u90e8\u4ef6"
                    motion_qa = assess_component_motion(
                        normalized,
                        target_region=motion_region,
                        motion_name=motion_label,
                        minimum_motion_coverage=0.22 if is_flow else 0.0,
                    )
                    passed = qa.get("status") == "passed" and motion_qa.get("status") == "passed"
                    qa = {
                        "status": "passed" if passed else "failed",
                        "score": min(int(qa.get("score") or 0), int(motion_qa.get("score") or 0)),
                        "message": motion_qa.get("message") if qa.get("status") == "passed" else qa.get("message"),
                        "fidelity": qa,
                        "flow_motion" if is_flow else "component_motion": motion_qa,
                    }
                    if not passed:
                        self.fail_job(job["id"], f"{motion_label}\u52a8\u6548\u8d28\u68c0\u672a\u901a\u8fc7\uff1a{qa.get('message')}", qa_result=qa)
                        continue
                if qa.get("status") != "passed":
                    self.render_fallback(job["id"], reason=f"AI 保真检查未通过，已自动降级：{qa.get('message')}")
                    continue
                video_key = f"image-motion/videos/{job['id']}_v{job.get('version', 1)}.mp4"
                self.storage.write_file_bytes(video_key, normalized, content_type="video/mp4")
                self.update_job(
                    job["id"],
                    status="succeeded",
                    progress=100,
                    current_step="生成完成",
                    completed_at=self.now(),
                    video_key=video_key,
                    qa_status="passed",
                    qa_result=qa,
                )
            except Exception as exc:
                if is_ray2:
                    self.fail_job(job["id"], f"\u5237\u65b0 Ray 2 \u7ed3\u679c\u5931\u8d25\uff1a{exc}")
                    continue
                try:
                    self.render_fallback(job["id"], reason=f"刷新供应商结果失败，已自动降级：{exc}")
                except Exception as fallback_exc:
                    self.update_job(job["id"], status="failed", progress=100, qa_status="failed", failure_message=str(fallback_exc))
        return self.jobs()
def _safe_name(value: str, fallback: str) -> str:
    name = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(value or "")).strip("_")
    return name[:80] or fallback


def register_image_motion_routes(
    app,
    *,
    verify_access,
    storage,
    script_tasks_loader: Callable[[], list[dict]],
    provider_submit: Callable[..., dict] | None = None,
    provider_poll: Callable[[str], dict] | None = None,
    component_provider_submit: Callable[..., dict] | None = None,
    component_provider_poll: Callable[[str], dict] | None = None,
):
    workflow = ImageMotionWorkflow(
        storage,
        script_tasks_loader,
        provider_submit,
        provider_poll,
        component_provider_submit,
        component_provider_poll,
    )
    protected = [Depends(verify_access)]

    @app.get("/api/creative-assets", dependencies=protected)
    def list_creative_assets(category: str = "", model: str = ""):
        assets = workflow.ensure_resolution_enhancements(category=category, model=model)
        if category:
            assets = [item for item in assets if item.get("category") == category]
        if model:
            assets = [item for item in assets if item.get("model") == model]
        return {"assets": [workflow.public_asset(item) for item in assets[:100]], "enabled": workflow.enabled}

    @app.post("/api/creative-assets", dependencies=protected)
    async def create_creative_assets(
        files: list[UploadFile] = File(...),
        category: str = Form(...),
        model: str = Form(...),
        feature: str = Form(""),
    ):
        if not workflow.enabled:
            raise HTTPException(status_code=404, detail="卖点图动效功能尚未开启。")
        category, model = category.strip(), model.strip()
        if not category or not model:
            raise HTTPException(status_code=400, detail="请先确认产品品类和型号。")
        if not files or len(files) > 10:
            raise HTTPException(status_code=400, detail="同一批必须上传 1 至 10 张同型号卖点图。")
        created = []
        for upload in files:
            media_type = str(upload.content_type or "").lower()
            if media_type not in CONTENT_TYPES:
                raise HTTPException(status_code=400, detail=f"{upload.filename} 不是支持的 PNG/JPEG/WebP 图片。")
            data = await upload.read()
            if not data or len(data) > MAX_FILE_BYTES:
                raise HTTPException(status_code=400, detail=f"{upload.filename} 为空或超过 20MB。")
            asset_id = uuid.uuid4().hex[:12]
            extension = "jpg" if media_type == "image/jpeg" else "webp" if media_type == "image/webp" else "png"
            source_key = f"creative-assets/{asset_id}/original.{extension}"
            storage.write_file_bytes(source_key, data, content_type=media_type)
            now = workflow.now()
            asset = {
                "id": asset_id,
                "category": category,
                "model": model,
                "feature": feature,
                "filename": upload.filename or f"{asset_id}.{extension}",
                "content_type": media_type,
                "file_hash": hashlib.sha256(data).hexdigest(),
                "original_key": source_key,
                "created_at": now,
                "updated_at": now,
            }
            try:
                asset, _ = workflow.enhance_asset_record(asset, force=True)
            except Exception as exc:
                raise HTTPException(status_code=400, detail=f"{upload.filename} 无法识别或增强：{exc}") from exc
            asset["feature"] = feature or (asset.get("analysis") or {}).get("selling_point_summary") or ""
            created.append(asset)
        with workflow.lock:
            assets = created + workflow.assets()
            workflow.write_list(CREATIVE_ASSETS_KEY, assets, 1000)
            plans = [workflow.default_plan(asset) for asset in created] + workflow.plans()
            workflow.write_list(MOTION_PLANS_KEY, plans, 1000)
        return {"ok": True, "assets": [workflow.public_asset(item) for item in created]}

    @app.get("/api/creative-assets/{asset_id}/image", dependencies=protected)
    def creative_asset_image(asset_id: str):
        asset = workflow.asset(asset_id)
        if not asset:
            raise HTTPException(status_code=404, detail="卖点图素材不存在。")
        return Response(
            storage.read_file_bytes(workflow.working_image_key(asset)),
            media_type=asset.get("working_content_type") or asset.get("content_type") or "image/png",
        )

    @app.post("/api/creative-assets/{asset_id}/motion-plan", dependencies=protected)
    def save_motion_plan(asset_id: str, request: MotionPlanRequest):
        return {"ok": True, "motion_plan": workflow.save_plan(asset_id, request.model_dump())}

    @app.post("/api/image-motion/jobs", dependencies=protected)
    def submit_image_motion_jobs(request: ImageMotionSubmitRequest):
        asset_ids = list(dict.fromkeys(request.creative_asset_ids))
        if len(asset_ids) > 10:
            raise HTTPException(status_code=400, detail="单批最多提交 10 张卖点图。")
        results = []
        for asset_id in asset_ids:
            asset = workflow.asset(asset_id)
            if not asset:
                raise HTTPException(status_code=404, detail=f"卖点图素材 {asset_id} 不存在。")
            analysis = asset.get("analysis") or {}
            if not analysis.get("ready"):
                raise HTTPException(status_code=400, detail={"asset_id": asset_id, "issues": analysis.get("blocking_issues") or ["素材未通过检查"]})
            plan = workflow.plan(asset_id) or workflow.default_plan(asset)
            key = f"{request.idempotency_key}:{asset_id}" if request.idempotency_key else ""
            job, reused = workflow.create_job(asset, plan, key)
            results.append({**workflow.public_job(job), "idempotent_reuse": reused})
        return {"ok": True, "jobs": results}

    @app.get("/api/image-motion/jobs", dependencies=protected)
    def list_image_motion_jobs(creative_asset_id: str = ""):
        jobs = workflow.jobs()
        if creative_asset_id:
            jobs = [item for item in jobs if item.get("creative_asset_id") == creative_asset_id]
        return {"jobs": [workflow.public_job(item) for item in jobs[:200]]}

    @app.get("/api/image-motion/jobs/{job_id}", dependencies=protected)
    def get_image_motion_job(job_id: str):
        job = next((item for item in workflow.jobs() if item.get("id") == job_id), None)
        if not job:
            raise HTTPException(status_code=404, detail="动效任务不存在。")
        return workflow.public_job(job)

    @app.post("/api/image-motion/jobs/refresh", dependencies=protected)
    def refresh_image_motion_jobs():
        return {"jobs": [workflow.public_job(item) for item in workflow.refresh_provider_jobs()[:200]]}

    @app.post("/api/image-motion/jobs/{job_id}/regenerate", dependencies=protected)
    def regenerate_image_motion_job(job_id: str, request: ImageMotionRegenerateRequest):
        job = next((item for item in workflow.jobs() if item.get("id") == job_id), None)
        if not job:
            raise HTTPException(status_code=404, detail="动效任务不存在。")
        asset_id = str(job.get("creative_asset_id") or "")
        plan = dict(workflow.plan(asset_id) or job.get("motion_plan") or {})
        if request.action == "lower_intensity":
            plan["intensity"] = {"strong": "standard", "standard": "subtle", "subtle": "subtle"}.get(plan.get("intensity"), "subtle")
        elif request.action == "alternate_effect":
            current = plan.get("preset") or "camera"
            choices = ["flow", "steam", "glow", "component", "camera"]
            plan["preset"] = choices[(choices.index(current) + 1) % len(choices)] if current in choices else "flow"
        saved_plan = workflow.save_plan(asset_id, plan)
        asset = workflow.asset(asset_id)
        new_job, _ = workflow.create_job(asset, saved_plan, f"retry:{job_id}:{request.action}:{uuid.uuid4().hex}", parent_job_id=job_id)
        return {"ok": True, "job": workflow.public_job(new_job)}

    @app.get("/api/image-motion/jobs/{job_id}/video", dependencies=protected)
    def image_motion_video(job_id: str, request: Request, download: bool = False, muted: bool = False):
        job = next((item for item in workflow.jobs() if item.get("id") == job_id), None)
        if not job or job.get("status") != "succeeded" or job.get("qa_status") != "passed" or not job.get("video_key"):
            raise HTTPException(status_code=404, detail="正式视频不存在或尚未通过保真检查。")
        data = storage.read_file_bytes(job["video_key"])
        if muted:
            data = mute_video(data)
        range_header = request.headers.get("range", "")
        headers = {"Accept-Ranges": "bytes", "Cache-Control": "private, max-age=60"}
        filename = f"{_safe_name(job.get('model'), 'product')}_{_safe_name(job.get('creative_asset_id'), 'asset')}_v{job.get('version', 1)}{'_muted' if muted else ''}.mp4"
        headers["Content-Disposition"] = f"{'attachment' if download else 'inline'}; filename=\"{filename}\""
        if range_header.startswith("bytes=") and not muted:
            try:
                start_raw, end_raw = range_header[6:].split("-", 1)
                start = int(start_raw or 0)
                end = min(len(data) - 1, int(end_raw) if end_raw else len(data) - 1)
                if start <= end:
                    body = data[start : end + 1]
                    headers["Content-Range"] = f"bytes {start}-{end}/{len(data)}"
                    return Response(body, status_code=206, media_type="video/mp4", headers=headers)
            except (ValueError, TypeError):
                pass
        return Response(data, media_type="video/mp4", headers=headers)

    @app.post("/api/image-motion/exports", dependencies=protected)
    def export_image_motion_jobs(request: ImageMotionExportRequest):
        jobs_by_id = {item.get("id"): item for item in workflow.jobs()}
        selected = []
        for job_id in list(dict.fromkeys(request.job_ids)):
            job = jobs_by_id.get(job_id)
            if not job or job.get("status") != "succeeded" or job.get("qa_status") != "passed" or not job.get("video_key"):
                raise HTTPException(status_code=400, detail=f"任务 {job_id} 尚不可正式下载。")
            selected.append(job)
        output = io.BytesIO()
        with zipfile.ZipFile(output, mode="w", compression=zipfile.ZIP_DEFLATED) as archive:
            for job in selected:
                data = storage.read_file_bytes(job["video_key"])
                if request.muted:
                    data = mute_video(data)
                name = f"{_safe_name(job.get('model'), 'product')}_{_safe_name(job.get('creative_asset_id'), 'asset')}_v{job.get('version', 1)}{'_muted' if request.muted else ''}.mp4"
                archive.writestr(name, data)
            archive.writestr(
                "manifest.json",
                json.dumps(
                    {"workflow": "selling_point_image_motion", "exported_at": workflow.now(), "jobs": [workflow.public_job(item) for item in selected]},
                    ensure_ascii=False,
                    indent=2,
                ),
            )
        return Response(
            output.getvalue(),
            media_type="application/zip",
            headers={"Content-Disposition": 'attachment; filename="selling_point_image_motion.zip"'},
        )

    @app.get("/api/tasks", dependencies=protected)
    def unified_tasks(task_type: str = "all", limit: int = 100):
        limit = max(1, min(int(limit or 100), 300))
        tasks = []
        if task_type in {"all", "script"}:
            for item in script_tasks_loader():
                task = dict(item)
                task["task_type"] = "script"
                task["type_label"] = "脚本任务"
                tasks.append(task)
        if task_type in {"all", "image_motion"}:
            tasks.extend(workflow.public_job(item) for item in workflow.jobs())
        tasks.sort(key=lambda item: str(item.get("created_at") or ""), reverse=True)
        return {"tasks": tasks[:limit], "enabled": workflow.enabled}

    return workflow

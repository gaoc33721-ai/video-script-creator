"""Pure helpers for the selling-point image motion workflow.

The API layer owns persistence and provider calls.  This module keeps image
analysis, prompt construction and deterministic fallback rendering isolated so
the existing script/storyboard pipeline does not need to know about them.
"""

from __future__ import annotations

import hashlib
import io
import json
import math
import os
import shutil
import subprocess
import tempfile
from typing import Any


ALLOWED_RATIOS = {"source", "1:1", "9:16", "16:9"}
ALLOWED_TEXT_POLICIES = {"visual_only", "preserve_title_logo"}
ALLOWED_PRESETS = {"auto", "flow", "liquid", "steam", "glow", "component", "camera"}
ALLOWED_INTENSITIES = {"subtle", "standard", "strong"}

def enhance_image_resolution(
    image_bytes: bytes,
    *,
    target_short_side: int = 720,
    minimum_source_short_side: int = 360,
) -> tuple[bytes, dict[str, Any]]:
    """Create a deterministic fidelity-first upscale without redrawing pixels."""
    from PIL import Image, ImageChops, ImageFilter, ImageOps, ImageStat

    source = ImageOps.exif_transpose(Image.open(io.BytesIO(image_bytes))).convert("RGB")
    width, height = source.size
    short_side = min(width, height)
    metadata = {
        "applied": False,
        "status": "not_needed",
        "method": "lanczos-denoise-unsharp-v1",
        "source_width": width,
        "source_height": height,
        "output_width": width,
        "output_height": height,
        "scale": 1.0,
        "fidelity_score": 100.0,
    }
    if short_side >= target_short_side:
        return image_bytes, metadata
    if short_side < minimum_source_short_side:
        metadata["status"] = "too_small"
        return image_bytes, metadata

    scale = target_short_side / float(short_side)
    target_width = max(2, int(round(width * scale / 2) * 2))
    target_height = max(2, int(round(height * scale / 2) * 2))
    denoised = source.filter(ImageFilter.GaussianBlur(radius=0.25))
    enhanced = denoised.resize((target_width, target_height), Image.Resampling.LANCZOS)
    enhanced = enhanced.filter(ImageFilter.UnsharpMask(radius=1.1, percent=110, threshold=3))

    comparison = enhanced.resize(source.size, Image.Resampling.LANCZOS)
    difference = ImageChops.difference(source, comparison)
    mean_difference = sum(float(value) for value in ImageStat.Stat(difference).mean) / 3.0
    fidelity_score = round(max(0.0, 100.0 - mean_difference * 2.0), 1)
    if fidelity_score < 72.0:
        metadata.update({"status": "fidelity_failed", "fidelity_score": fidelity_score})
        return image_bytes, metadata

    output = io.BytesIO()
    enhanced.save(output, format="PNG", optimize=True)
    metadata.update(
        {
            "applied": True,
            "status": "enhanced",
            "output_width": target_width,
            "output_height": target_height,
            "scale": round(scale, 4),
            "fidelity_score": fidelity_score,
        }
    )
    return output.getvalue(), metadata


def normalize_ratio(value: str, width: int = 1, height: int = 1) -> str:
    ratio = str(value or "source").strip().lower()
    if ratio not in ALLOWED_RATIOS:
        ratio = "source"
    return ratio


def output_dimensions(ratio: str, quality: str = "1080p", width: int = 1, height: int = 1) -> tuple[int, int]:
    # 1080 is the business default; the smaller option is useful for local QA.
    short_side = 1080 if str(quality or "1080p").lower() == "1080p" else 720
    normalized = normalize_ratio(ratio, width, height)
    if normalized == "9:16":
        return short_side, int(round(short_side * 16 / 9 / 2) * 2)
    if normalized == "16:9":
        return int(round(short_side * 16 / 9 / 2) * 2), short_side
    if normalized == "source":
        source_ratio = float(width or 1) / max(1.0, float(height or 1))
        if source_ratio >= 1:
            return int(round(short_side * source_ratio / 2) * 2), short_side
        return short_side, int(round(short_side / max(source_ratio, 0.01) / 2) * 2)
    return short_side, short_side


def _category_hint(text: str) -> str:
    lowered = str(text or "").lower()
    mappings = (
        (("coffee", "espresso", "咖啡"), "coffee machine"),
        (("microwave", "微波"), "microwave"),
        (("air fryer", "airfryer", "空气炸", "炸锅"), "air fryer"),
        (("oven", "烤箱", "bake", "inverter technology"), "oven"),
        (("refrigerator", "fridge", "冰箱"), "refrigerator"),
        (("washer", "washing machine", "洗衣"), "washing machine"),
        (("dryer", "烘干"), "dryer"),
        (("dishwasher", "洗碗"), "dishwasher"),
        (("television", " tv ", "电视"), "television"),
    )
    for tokens, category in mappings:
        if any(token in lowered for token in tokens):
            return category
    return ""


def _category_matches(selected: str, detected: str) -> bool:
    if not detected:
        return True
    selected_hint = _category_hint(selected)
    return not selected_hint or selected_hint == detected


def _preset_for_text(text: str) -> str:
    lowered = str(text or "").lower()
    if any(
        token in lowered
        for token in (
            "dual cooking",
            "cooking zone",
            "dual zone",
            "airflow",
            "air flow",
            "hot air",
            "flow",
            "heat",
            "inverter",
            "circul",
            "风",
            "热",
            "循环",
        )
    ):
        return "flow"
    if any(token in lowered for token in ("steam", "蒸汽", "蒸")):
        return "steam"
    if any(token in lowered for token in ("water", "wash", "liquid", "水", "洗", "液")):
        return "liquid"
    if any(token in lowered for token in ("display", "led", "light", "screen", "panel", "touch", "灯", "屏")):
        return "glow"
    if any(token in lowered for token in ("door", "drawer", "rotate", "spin", "门", "抽屉", "旋转")):
        return "component"
    return "camera"


def analyze_creative_image(
    image_bytes: bytes,
    *,
    filename: str = "",
    category: str = "",
    model: str = "",
    feature: str = "",
) -> dict[str, Any]:
    """Return an editable, deterministic motion recommendation.

    The local analyzer intentionally errs toward preserving the source.  A
    multimodal analyzer can later enrich the same contract without changing
    the UI or job schema.
    """
    from PIL import Image, ImageFilter, ImageOps, ImageStat

    image = ImageOps.exif_transpose(Image.open(io.BytesIO(image_bytes))).convert("RGB")
    width, height = image.size
    if width < 2 or height < 2:
        raise ValueError("图片尺寸无效。")

    grayscale = image.convert("L")
    top_end = max(1, int(height * 0.32))
    top = grayscale.crop((0, 0, width, top_end))
    lower = grayscale.crop((0, top_end, width, height))
    top_edges = top.filter(ImageFilter.FIND_EDGES)
    lower_edges = lower.filter(ImageFilter.FIND_EDGES)
    top_edge_mean = float(ImageStat.Stat(top_edges).mean[0])
    lower_edge_mean = float(ImageStat.Stat(lower_edges).mean[0])
    top_mean = float(ImageStat.Stat(top).mean[0])
    top_extrema = top.getextrema()
    has_header = bool(
        0.82 <= width / max(1, height) <= 1.22
        and lower_edge_mean > max(6.0, top_edge_mean * 1.10)
        and (top_mean < 105 or (top_extrema[1] - top_extrema[0]) > 150)
    )

    crop_top = int(height * 0.30) if has_header else 0
    available_h = height - crop_top
    if has_header:
        side = min(width, available_h)
        crop_left = max(0, (width - side) // 2)
        visual_crop = {
            "x": round(crop_left / width, 4),
            "y": round(crop_top / height, 4),
            "width": round(side / width, 4),
            "height": round(side / height, 4),
        }
    else:
        visual_crop = {"x": 0.0, "y": 0.0, "width": 1.0, "height": 1.0}

    source_text = " ".join((filename, category, model, feature))
    filename_hint = str(filename or "").lower()
    appliance_count = 2 if any(token in filename_hint for token in ("multi", "double", "two-", "2unit", "2-unit", "多台", "两台", "双机")) else 1
    detected_category = _category_hint(" ".join((filename, feature)))
    category_mismatch = not _category_matches(category, detected_category)
    preset = _preset_for_text(source_text)
    protected_regions = []
    if has_header:
        protected_regions.append(
            {"type": "text", "x": 0.0, "y": 0.0, "width": 1.0, "height": round(crop_top / height, 4)}
        )

    blocking_issues = []
    warnings = []
    if min(width, height) < 720:
        blocking_issues.append("素材短边低于720像素，请上传更清晰的卖点图。")
    elif min(width, height) < 960:
        warnings.append("素材短边低于960像素，建议更换高清原图。")
    if category_mismatch:
        blocking_issues.append(f"图片线索更接近 {detected_category}，与所选品类 {category} 不一致。")
    if appliance_count != 1:
        blocking_issues.append("检测到多台目标家电，请裁切或更换为单一产品素材。")
    if not detected_category:
        warnings.append("本地分析未识别出明确品类，已以人工选择的产品品类为准。")

    return {
        "analyzer": "local-fidelity-v1",
        "width": width,
        "height": height,
        "sha256": hashlib.sha256(image_bytes).hexdigest(),
        "detected_category": detected_category,
        "category_confidence": 0.86 if detected_category else 0.0,
        "category_matches": not category_mismatch,
        "appliance_count": appliance_count,
        "visual_crop": visual_crop,
        "protected_regions": protected_regions,
        "has_copy_header": has_header,
        "selling_point_summary": str(feature or os.path.splitext(os.path.basename(filename))[0]).strip(),
        "recommended_preset": preset,
        "recommended_focus": "effect" if preset in {"flow", "steam", "liquid", "glow"} else "product",
        "recommended_direction": "循环扩散" if preset == "flow" else "由下向上" if preset == "steam" else "轻微推进",
        "blocking_issues": blocking_issues,
        "warnings": warnings,
        "ready": not blocking_issues,
    }


def _crop_box(plan: dict[str, Any], width: int, height: int) -> tuple[int, int, int, int]:
    crop = dict((plan or {}).get("visual_crop") or {})
    left = max(0, min(width - 1, int(float(crop.get("x", 0)) * width)))
    top = max(0, min(height - 1, int(float(crop.get("y", 0)) * height)))
    right = max(left + 1, min(width, int((float(crop.get("x", 0)) + float(crop.get("width", 1))) * width)))
    bottom = max(top + 1, min(height, int((float(crop.get("y", 0)) + float(crop.get("height", 1))) * height)))
    return left, top, right, bottom


def prepare_motion_source(
    image_bytes: bytes,
    plan: dict[str, Any],
    *,
    ratio: str,
    text_policy: str,
    quality: str = "1080p",
    max_dimension: int | None = None,
) -> tuple[bytes, dict[str, Any]]:
    from PIL import Image, ImageFilter, ImageOps

    source = ImageOps.exif_transpose(Image.open(io.BytesIO(image_bytes))).convert("RGB")
    policy = text_policy if text_policy in ALLOWED_TEXT_POLICIES else "visual_only"
    working = source.crop(_crop_box(plan, *source.size)) if policy == "visual_only" else source
    normalized_ratio = normalize_ratio(ratio, *working.size)
    target_size = output_dimensions(normalized_ratio, quality, *working.size)
    dimension_limit = max(2, int(max_dimension)) if max_dimension else 0
    if dimension_limit and max(target_size) > dimension_limit:
        scale = dimension_limit / float(max(target_size))
        target_size = tuple(max(2, int(dimension * scale) // 2 * 2) for dimension in target_size)
    if policy == "preserve_title_logo" or normalized_ratio != "source":
        background = ImageOps.fit(working, target_size, method=Image.Resampling.LANCZOS).filter(ImageFilter.GaussianBlur(28))
        background = background.point(lambda value: int(value * 0.55))
        foreground = ImageOps.contain(working, target_size, method=Image.Resampling.LANCZOS)
        x = (target_size[0] - foreground.width) // 2
        y = (target_size[1] - foreground.height) // 2
        background.paste(foreground, (x, y))
        prepared = background
    else:
        prepared = ImageOps.fit(working, target_size, method=Image.Resampling.LANCZOS)
    output = io.BytesIO()
    prepared.save(output, format="PNG", optimize=True)
    return output.getvalue(), {
        "aspect_ratio": normalized_ratio,
        "provider_aspect_ratio": (
            "16:9" if target_size[0] / max(1, target_size[1]) >= 1.35
            else "9:16" if target_size[0] / max(1, target_size[1]) <= 0.74
            else "1:1"
        ),
        "width": target_size[0],
        "height": target_size[1],
        "text_policy": policy,
        "max_dimension": dimension_limit or None,
    }


def build_motion_prompt(asset: dict[str, Any], plan: dict[str, Any]) -> str:
    category = str(asset.get("category") or "home appliance")
    model = str(asset.get("model") or "").strip()
    feature = str(asset.get("feature") or (asset.get("analysis") or {}).get("selling_point_summary") or "product benefit")
    preset = str(plan.get("preset") or "auto")
    focus = str(plan.get("focus") or "effect")
    intensity = str(plan.get("intensity") or "standard")
    direction = str(plan.get("direction") or (asset.get("analysis") or {}).get("recommended_direction") or "gentle forward motion")
    if preset == "glow":
        direction = "localized illumination evolving inside the existing selling-point feature"
    custom = str(plan.get("custom_instruction") or "").strip()
    premium_finish = (
        "Premium technology-commercial finish: crisp product edges, controlled high dynamic range, realistic material-aware reflections, "
        "subtle volumetric depth, clean contrast, physically plausible occlusion and refined micro-highlights; the result must look filmed, not like a cheap 2D overlay. "
    )
    temporal_sequence = (
        "Temporal choreography: 0.0-0.8 seconds hold the exact source composition while the feature gently awakens; "
        "0.8-3.8 seconds develop clear continuous feature motion with layered depth and evolving detail; "
        "3.8-5.0 seconds reach a confident hero state and settle smoothly without a cut. "
    )
    if preset == "steam":
        lowered_feature = feature.lower()
        dual_zone = any(token in lowered_feature for token in ("dual cooking", "cooking zone", "dual zone", "two zone", "2 zone"))
        steam_action = (
            "Natural steam must emerge independently from the hot food surfaces in both visible cooking baskets, with non-identical timing and shapes in the left and right zones."
            if dual_zone
            else "Natural steam must emerge from the visible hot food surface or real appliance vent shown in the source image."
        )
        prompt = (
            "Create one continuous five-second premium e-commerce product demonstration based strictly on the supplied source image. "
            f"Product lock: exactly one Hisense {category}, model {model or 'as shown in the source'}, and no other appliance. "
            "The supplied image is the absolute truth for product structure, proportions, materials, control layout, text and brand placement. "
            f"Required volumetric steam motion: {steam_action} Selling point: {feature}. Direction note: {direction}. Intensity: {intensity}. "
            "Render translucent, semi-transparent vapor with layered density, fine wisps and broader soft plumes, buoyant upward drift, irregular turbulent curls, and natural expansion and dissipation. "
            "The steam shape, position and opacity must evolve continuously frame by frame and feel lively, warm and appetizing without hiding the product. "
            f"{premium_finish}{temporal_sequence} "
            "No drawn white lines, vector curves, ribbons, outline strokes, repeated sine waves, sticker overlays, flat glow sweeps or artificial smoke-machine clouds. "
            "Use a locked-off camera: absolutely no zoom, dolly, pan, tilt, orbit, crop animation, camera shake, cuts or transitions. "
            "Keep the appliance shell, baskets, food, control panel, labels, logo and background stationary and structurally unchanged. "
            "Do not add people, hands, rooms, extra products, text or logos. Lighting stays bright, positive, clean and consistent. "
        )
        if custom:
            prompt += f"Creator instruction: {custom}."
        return prompt.strip()[:3000]
    if preset == "liquid":
        prompt = (
            "Create one continuous five-second premium e-commerce product demonstration based strictly on the supplied source image. "
            f"Product lock: exactly one Hisense {category}, model {model or 'as shown in the source'}, and no other appliance. "
            "The supplied image is the absolute truth for product structure, proportions, materials, control layout, text and brand placement. "
            f"Required physically coherent liquid motion: animate liquid only from a visible real source and along a plausible path in the product scene. Selling point: {feature}. Direction note: {direction}. Intensity: {intensity}. "
            "Show changing surface ripples, glossy refraction, small droplets and restrained splashes with continuous gravity-driven movement and natural variation frame by frame. "
            f"{premium_finish}{temporal_sequence} "
            "No blue lines, vector ribbons, flat mask wipes, glow sweeps, sticker overlays or repeated identical streams. "
            "Use a locked-off camera: absolutely no zoom, dolly, pan, tilt, orbit, crop animation, camera shake, cuts or transitions. "
            "Keep the appliance structure, control panel, labels, logo and background stationary and unchanged. Do not add people, hands, rooms, extra products, text or logos. "
            "Lighting stays bright, clean, positive and consistent. "
        )
        if custom:
            prompt += f"Creator instruction: {custom}."
        return prompt.strip()[:3000]
    if preset == "flow":
        lowered_feature = feature.lower()
        dual_zone = any(token in lowered_feature for token in ("dual cooking", "cooking zone", "dual zone", "two zone", "2 zone"))
        flow_action = (
            "Both visible cooking zones must show separate, continuous circulating hot-air currents inside each basket; "
            "the left and right currents remain spatially separated and clearly travel along curved paths."
            if dual_zone
            else "Any existing airflow arrows or heat cues must become continuously moving directional hot-air currents that follow their source paths."
        )
        prompt = (
            "Create one continuous five-second premium e-commerce product demonstration based strictly on the supplied source image. "
            f"Product lock: exactly one Hisense {category}, model {model or 'as shown in the source'}, and no other appliance. "
            "The supplied image is the absolute truth for product structure, proportions, materials, control layout, text and brand placement. "
            f"Required airflow motion: {flow_action} Selling point: {feature}. Direction note: {direction}. Intensity: {intensity}. "
            "The primary airflow motion must visibly change position frame by frame and persist throughout the shot. "
            "Give the airflow premium spatial depth with warm translucent energy, subtle particle advection, heat shimmer and realistic occlusion behind product edges; follow existing arrows or heat cues instead of inventing unrelated paths. "
            f"{premium_finish}{temporal_sequence} "
            "It must not be a static glow, horizontal highlight sweep, brightness pulse, mask wipe or camera motion. "
            "Use a locked-off camera: absolutely no zoom, dolly, pan, tilt, orbit, crop animation, camera shake, cuts or transitions. "
            "Keep the appliance shell, baskets, food, control panel, labels, logo and background stationary and structurally unchanged. "
            "Do not add people, hands, rooms, extra products, text or logos. Lighting stays bright, positive, clean and consistent. "
        )
        if custom:
            prompt += f"Creator instruction: {custom}."
        return prompt.strip()[:3000]
    if preset == "glow":
        lowered_feature = feature.lower()
        if any(token in lowered_feature for token in ("window", "viewing", "glass")):
            glow_target = "the existing viewing window and the real interior visible through it"
        elif any(token in lowered_feature for token in ("touch", "panel", "display", "control")):
            glow_target = "the existing touch-control panel and its original interface elements"
        else:
            glow_target = "the exact visible selling-point feature already present in the source image"
        prompt = (
            "Create one continuous five-second premium technology e-commerce product film from the supplied source image. "
            f"Product lock: exactly one Hisense {category}, model {model or 'as shown in the source'}, and no other appliance. "
            "Treat the supplied image as an immutable product reference: preserve the exact silhouette, geometry, seams, handles, baskets, glass, materials, colors, control layout, printed text, logo and background. "
            f"Selling point: {feature}. Animate only {glow_target}. Direction note: {direction}. Intensity: {intensity}. "
            "Build localized multilayer illumination with inner emissive depth, soft edge bloom, tiny drifting energy particles and restrained material-aware micro-reflections on nearby fixed surfaces. "
            "The light must originate from the feature, breathe with refined irregular timing and reveal depth inside the feature; it must not travel as one flat horizontal sweep. "
            f"{premium_finish}{temporal_sequence} "
            "Use a locked-off camera: absolutely no zoom, dolly, pan, tilt, orbit, crop animation, camera shake, cuts or transitions. "
            "No flat mask wipe, one-pass scan line, global brightness pulse, neon outline around the whole appliance, sticker overlay or generic lens flare. "
            "Do not redraw interface icons or text, change product geometry, open parts, add people, hands, rooms, extra products, words or logos. Keep the mood bright, clean, confident and futuristic. "
        )
        if custom:
            prompt += f"Creator instruction: {custom}."
        return prompt.strip()[:3000]
    if preset == "component":
        category_hint = _category_hint(category)
        if category_hint == "air fryer":
            component_action = "one clearly visible cooking basket or front drawer slides outward slightly, then settles; the other basket and outer cabinet remain fixed"
        elif category_hint in {"oven", "microwave", "dishwasher"}:
            component_action = "the clearly visible appliance door opens slightly on its real hinge, then settles; the outer cabinet remains fixed"
        elif category_hint == "washing machine":
            component_action = "the visible drum rotates smoothly inside the fixed outer cabinet"
        elif category_hint == "refrigerator":
            component_action = "one clearly visible door opens slightly on its real hinge; the cabinet and any other door remain fixed"
        else:
            component_action = "exactly one clearly visible movable product component performs a small mechanically plausible slide, rotation or hinge motion while the outer cabinet remains fixed"
        prompt = (
            "Create one continuous five-second premium e-commerce product demonstration based strictly on the supplied source image. "
            f"Product lock: exactly one Hisense {category}, model {model or 'as shown in the source'}, and no other appliance. "
            "The supplied image is the absolute truth for product structure, proportions, materials, control layout, text and brand placement. "
            f"Required articulated motion: {component_action}. Selling point: {feature}. Direction note: {direction}. "
            "The component motion must be obvious but restrained and mechanically connected to the product; do not move, translate, scale, bend or morph the whole appliance. "
            f"{premium_finish}{temporal_sequence} "
            "Use a locked-off camera: absolutely no zoom, dolly, pan, tilt, orbit, crop animation, camera shake, cuts or transitions. "
            "Keep the background, outer shell, control panel, labels and logo stationary and unchanged. Do not add hands, people, rooms, extra products, text or logos. "
            "Lighting stays bright, clean, positive and consistent. "
        )
        if custom:
            prompt += f"Creator instruction: {custom}."
        return prompt.strip()[:3000]
    prompt = (
        f"Create one continuous five-second premium e-commerce motion shot based strictly on the supplied source image. "
        f"Product lock: exactly one Hisense {category}, model {model or 'as shown in the source'}, and no other appliance. "
        "The source image is the absolute truth for product category, structure, proportions, materials, control layout and brand placement. "
        "Do not add, remove, replace, duplicate, redesign or morph the appliance. Do not invent people, hands, rooms or unrelated objects. "
        f"Selling point: {feature}. Animate only the visible {focus} using a {preset} effect, {direction}, at {intensity} intensity. "
        f"{premium_finish}{temporal_sequence} "
        "Use a single shot with a stable or very gentle push-in, coherent cause-to-effect progression, no cuts, no transition and no camera shake. "
        "Keep the mood bright, positive, clean, confident and uplifting from the first frame to the last; never show failure, frustration, darkness, anxiety or a negative-to-positive reversal. "
        "No new text, subtitles, price labels, watermarks, competitor brands, extra logos or garbled lettering. "
    )
    if custom:
        prompt += f"Creator instruction: {custom}."
    return prompt.strip()[:3000]


def validate_motion_plan(plan: dict[str, Any]) -> dict[str, Any]:
    """Normalize the small set of creator-facing motion controls."""
    preset = str(plan.get("preset") or "auto").strip().lower()
    intensity = str(plan.get("intensity") or "standard").strip().lower()
    text_policy = str(plan.get("text_policy") or "visual_only").strip().lower()
    focus = str(plan.get("focus") or "effect").strip().lower()
    ratio = str(plan.get("aspect_ratio") or "source").strip().lower()
    duration = max(1.0, min(5.0, float(plan.get("duration_seconds") or 5.0)))
    normalized_preset = preset if preset in ALLOWED_PRESETS else "auto"
    default_strategy = "hybrid_composite" if normalized_preset in {"flow", "steam", "liquid"} else "generative"
    generation_strategy = str(plan.get("generation_strategy") or default_strategy).strip().lower()
    return {
        **plan,
        "preset": normalized_preset,
        "intensity": intensity if intensity in ALLOWED_INTENSITIES else "standard",
        "text_policy": text_policy if text_policy in ALLOWED_TEXT_POLICIES else "visual_only",
        "focus": focus if focus in {"product", "effect", "background"} else "effect",
        "aspect_ratio": ratio if ratio in ALLOWED_RATIOS else "source",
        "duration_seconds": duration,
        "fps": 24,
        "quality": "1080p",
        "generation_strategy": generation_strategy if generation_strategy in {"generative", "hybrid_composite"} else default_strategy,
        "camera_motion": "locked" if normalized_preset in {"component", "flow", "steam", "liquid", "glow"} else "gentle_push_in",
    }


def _warm_effect_mask(image):
    """Locate baked orange/yellow airflow graphics without redrawing the source."""
    from PIL import ImageChops, ImageFilter

    hue, saturation, value = image.convert("HSV").split()
    warm_hue = hue.point([255 if item <= 34 or item >= 248 else 0 for item in range(256)])
    saturated = saturation.point([255 if item >= 92 else 0 for item in range(256)])
    bright = value.point([255 if item >= 118 else 0 for item in range(256)])
    mask = ImageChops.multiply(ImageChops.multiply(warm_hue, saturated), bright)
    return mask.filter(ImageFilter.MaxFilter(7)).filter(ImageFilter.GaussianBlur(1.6))


def _motion_overlay(
    size: tuple[int, int],
    frame: int,
    frame_count: int,
    preset: str,
    intensity: str,
    warm_mask=None,
    effect_region: dict[str, Any] | None = None,
):
    from PIL import Image, ImageChops, ImageDraw, ImageFilter

    width, height = size
    layer = Image.new("RGBA", size, (0, 0, 0, 0))
    alpha = {"subtle": 48, "standard": 76, "strong": 104}.get(intensity, 76)
    phase = frame / max(1, frame_count)
    region = effect_region or {"x": 0.08, "y": 0.16, "width": 0.84, "height": 0.68}
    left = max(0, min(width - 1, int(float(region.get("x", 0.08)) * width)))
    top = max(0, min(height - 1, int(float(region.get("y", 0.16)) * height)))
    right = max(left + 1, min(width, int((float(region.get("x", 0.08)) + float(region.get("width", 0.84))) * width)))
    bottom = max(top + 1, min(height, int((float(region.get("y", 0.16)) + float(region.get("height", 0.68))) * height)))
    region_width = right - left
    region_height = bottom - top
    clip = Image.new("L", size, 0)
    ImageDraw.Draw(clip).rectangle((left, top, right, bottom), fill=255)

    if preset == "steam":
        broad_mask = Image.new("L", size, 0)
        detail_mask = Image.new("L", size, 0)
        broad_draw = ImageDraw.Draw(broad_mask)
        detail_draw = ImageDraw.Draw(detail_mask)
        centers = (0.32, 0.68)
        for index in range(28):
            life = (phase * 1.18 + index * 0.089) % 1.0
            center = centers[index % len(centers)]
            turbulence = math.sin(life * math.tau * 1.7 + index * 1.91) + 0.45 * math.sin(life * math.tau * 3.2 + index)
            x = left + int(region_width * (center + turbulence * (0.018 + life * 0.028)))
            y = bottom - int(region_height * (0.10 + life * 0.86))
            fade = max(0.0, math.sin(math.pi * life)) ** 0.78
            rx = max(5, int(region_width * (0.024 + life * 0.050)))
            ry = max(8, int(region_height * (0.032 + life * 0.085)))
            opacity = min(245, int(alpha * 3.30 * fade * (0.58 + 0.34 * ((index * 37) % 11) / 10.0)))
            broad_draw.ellipse((x - rx, y - ry, x + rx, y + ry), fill=max(0, min(255, opacity)))
            detail_draw.ellipse((x - max(3, rx // 3), y - ry, x + max(3, rx // 3), y + ry), fill=max(0, min(255, int(opacity * 0.88))))
        broad_mask = broad_mask.filter(ImageFilter.GaussianBlur(max(6, width // 90)))
        detail_mask = detail_mask.filter(ImageFilter.GaussianBlur(max(2, width // 220)))
        steam_alpha = ImageChops.multiply(ImageChops.lighter(broad_mask, detail_mask), clip)
        steam = Image.new("RGBA", size, (247, 249, 246, 0))
        steam.putalpha(steam_alpha)
        layer = Image.alpha_composite(layer, steam)
    elif preset == "flow":
        flow_mask = Image.new("L", size, 0)
        flow_draw = ImageDraw.Draw(flow_mask)
        centers = (0.31, 0.69)
        for index in range(12):
            orbit = (phase * 0.95 + index * 0.137) % 1.0
            angle = orbit * math.tau + (index % 2) * math.pi
            center_x = left + region_width * centers[index % len(centers)]
            center_y = top + region_height * 0.58
            x = int(center_x + math.cos(angle) * region_width * 0.115)
            y = int(center_y + math.sin(angle) * region_height * 0.22)
            radius_x = max(5, int(region_width * (0.034 + 0.022 * (0.5 + 0.5 * math.sin(angle)))))
            radius_y = max(5, int(region_height * 0.058))
            opacity = min(235, int(alpha * 2.70 * (0.55 + 0.45 * math.sin(math.pi * orbit))))
            flow_draw.ellipse((x - radius_x, y - radius_y, x + radius_x, y + radius_y), fill=max(0, min(255, opacity)))
        flow_mask = flow_mask.filter(ImageFilter.GaussianBlur(max(2, width // 220)))
        if warm_mask is not None and warm_mask.getbbox():
            warm_glow = warm_mask.filter(ImageFilter.GaussianBlur(max(5, width // 120))).point(lambda value: int(value * 0.12))
            flow_mask = ImageChops.lighter(flow_mask, warm_glow)
        flow_mask = ImageChops.multiply(flow_mask, clip)
        heat = Image.new("RGBA", size, (255, 142, 28, 0))
        heat.putalpha(flow_mask)
        layer = Image.alpha_composite(layer, heat)
    elif preset == "liquid":
        liquid_mask = Image.new("L", size, 0)
        liquid_draw = ImageDraw.Draw(liquid_mask)
        for index in range(24):
            life = (phase * 1.25 + index * 0.073) % 1.0
            x = left + int(region_width * (0.18 + ((index * 29) % 64) / 100.0))
            y = top + int(region_height * life)
            radius = max(3, int(region_width * (0.006 + 0.008 * (1.0 - life))))
            opacity = int(alpha * math.sin(math.pi * life))
            liquid_draw.ellipse((x - radius, y - radius * 2, x + radius, y + radius * 2), fill=max(0, min(255, opacity)))
        liquid_mask = ImageChops.multiply(liquid_mask.filter(ImageFilter.GaussianBlur(max(2, width // 280))), clip)
        liquid = Image.new("RGBA", size, (112, 205, 255, 0))
        liquid.putalpha(liquid_mask)
        layer = Image.alpha_composite(layer, liquid)
    elif preset in {"glow", "component"}:
        draw = ImageDraw.Draw(layer)
        pulse = 0.55 + 0.45 * math.sin(phase * math.pi)
        margin = int(min(width, height) * (0.18 - 0.03 * pulse))
        color = (75, 225, 255, int(alpha * pulse))
        draw.rounded_rectangle((margin, margin, width - margin, height - margin), radius=max(12, margin // 4), outline=color, width=max(6, width // 120))
    return layer

def render_stable_motion_video(
    prepared_image_bytes: bytes,
    *,
    preset: str = "camera",
    intensity: str = "standard",
    text_policy: str = "visual_only",
    duration_seconds: float = 5.0,
    fps: int = 24,
    metadata: dict[str, Any] | None = None,
    protected_regions: list[dict[str, Any]] | None = None,
    effect_region: dict[str, Any] | None = None,
) -> bytes:
    """Render a deterministic fallback MP4 using original pixels plus overlays."""
    from PIL import Image, ImageOps

    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise RuntimeError("FFmpeg 未安装，无法生成高保真稳定动效。")
    base = ImageOps.exif_transpose(Image.open(io.BytesIO(prepared_image_bytes))).convert("RGB")
    frame_count = max(1, int(round(duration_seconds * fps)))
    chosen_preset = preset if preset in ALLOWED_PRESETS else "camera"
    warm_mask = _warm_effect_mask(base) if chosen_preset == "flow" else None
    chosen_intensity = intensity if intensity in ALLOWED_INTENSITIES else "standard"
    with tempfile.TemporaryDirectory() as tmpdir:
        for frame_index in range(frame_count):
            progress = frame_index / max(1, frame_count - 1)
            max_zoom = 1.0 if chosen_preset in {"flow", "steam", "liquid"} else 1.02 if text_policy == "preserve_title_logo" else {"subtle": 1.04, "standard": 1.075, "strong": 1.11}.get(chosen_intensity, 1.075)
            zoom = 1.0 + (max_zoom - 1.0) * progress
            if max_zoom > 1.0201:
                resized = base.resize((int(base.width * zoom), int(base.height * zoom)), Image.Resampling.LANCZOS)
                left = max(0, (resized.width - base.width) // 2)
                top = max(0, (resized.height - base.height) // 2)
                frame_image = resized.crop((left, top, left + base.width, top + base.height)).convert("RGBA")
            else:
                frame_image = base.convert("RGBA")
            if chosen_preset != "camera":
                frame_image = Image.alpha_composite(
                    frame_image,
                    _motion_overlay(base.size, frame_index, frame_count, chosen_preset, chosen_intensity, warm_mask, effect_region),
                )
            if text_policy == "preserve_title_logo":
                for region in protected_regions or []:
                    try:
                        left = max(0, min(base.width - 1, int(float(region.get("x", 0)) * base.width)))
                        top = max(0, min(base.height - 1, int(float(region.get("y", 0)) * base.height)))
                        right = max(left + 1, min(base.width, int((float(region.get("x", 0)) + float(region.get("width", 0))) * base.width)))
                        bottom = max(top + 1, min(base.height, int((float(region.get("y", 0)) + float(region.get("height", 0))) * base.height)))
                        frame_image.paste(base.crop((left, top, right, bottom)), (left, top))
                    except (TypeError, ValueError):
                        continue
            frame_image.convert("RGB").save(os.path.join(tmpdir, f"frame_{frame_index:04d}.jpg"), format="JPEG", quality=93, optimize=True)
        output_path = os.path.join(tmpdir, "motion.mp4")
        provenance = json.dumps({"Label": "1", "Workflow": "selling_point_image_motion", **(metadata or {})}, ensure_ascii=True, separators=(",", ":"))
        command = [
            ffmpeg, "-y", "-framerate", str(fps), "-i", os.path.join(tmpdir, "frame_%04d.jpg"),
            "-t", f"{duration_seconds:.3f}", "-an", "-c:v", "libx264", "-preset", "fast", "-crf", "19",
            "-pix_fmt", "yuv420p", "-movflags", "+faststart+use_metadata_tags", "-metadata", f"AIGC={provenance}", output_path,
        ]
        completed = subprocess.run(command, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, timeout=240, check=False)
        if completed.returncode != 0 or not os.path.exists(output_path):
            detail = completed.stderr.decode("utf-8", errors="ignore")[-500:]
            raise RuntimeError(f"稳定动效编码失败：{detail}")
        with open(output_path, "rb") as handle:
            return handle.read()


def normalize_generated_video(video_bytes: bytes, *, duration_seconds: float = 5.0, metadata: dict[str, Any] | None = None, output_size: tuple[int, int] | None = None) -> bytes:
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        return video_bytes
    with tempfile.TemporaryDirectory() as tmpdir:
        input_path = os.path.join(tmpdir, "source.mp4")
        output_path = os.path.join(tmpdir, "normalized.mp4")
        with open(input_path, "wb") as handle:
            handle.write(video_bytes)
        provenance = json.dumps({"Label": "1", "Workflow": "selling_point_image_motion", **(metadata or {})}, ensure_ascii=True, separators=(",", ":"))
        video_filter = []
        if output_size:
            width = max(2, int(output_size[0]) // 2 * 2)
            height = max(2, int(output_size[1]) // 2 * 2)
            video_filter = [
                "-vf", f"scale={width}:{height}:force_original_aspect_ratio=decrease,pad={width}:{height}:(ow-iw)/2:(oh-ih)/2",
            ]
        command = [
            ffmpeg, "-y", "-i", input_path, "-t", f"{duration_seconds:.3f}", "-map", "0:v:0", "-map", "0:a?",
            "-c:v", "libx264", "-preset", "fast", "-crf", "19", "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "128k",
            *video_filter,
            "-movflags", "+faststart+use_metadata_tags", "-metadata", f"AIGC={provenance}", output_path,
        ]
        completed = subprocess.run(command, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, timeout=180, check=False)
        if completed.returncode != 0 or not os.path.exists(output_path):
            return video_bytes
        with open(output_path, "rb") as handle:
            return handle.read()


def assess_video_fidelity(
    source_image_bytes: bytes,
    video_bytes: bytes,
    *,
    allowed_motion_region: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Check global similarity and pixel stability outside the allowed effect region."""
    from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageOps, ImageStat

    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        return {"status": "unavailable", "score": None, "message": "FFmpeg 不可用，无法执行抽帧保真检查。"}

    def average_hash(image) -> int:
        values = list(image.convert("L").resize((8, 8)).getdata())
        average = sum(values) / max(1, len(values))
        bits = 0
        for value in values:
            bits = (bits << 1) | (1 if value >= average else 0)
        return bits

    def similarity(left: int, right: int) -> float:
        return 1.0 - int(left ^ right).bit_count() / 64.0

    def masked_mean(image, mask) -> float:
        if mask.getbbox() is None:
            return 0.0
        values = ImageStat.Stat(image, mask=mask).mean
        return float(values[0]) if values else 0.0

    reference = ImageOps.exif_transpose(Image.open(io.BytesIO(source_image_bytes))).convert("RGB").resize((320, 180), Image.Resampling.LANCZOS)
    reference_hash = average_hash(reference)
    region = allowed_motion_region or {"x": 0.0, "y": 0.0, "width": 1.0, "height": 1.0}
    left = max(0, min(reference.width - 1, int(float(region.get("x", 0.0)) * reference.width)))
    top = max(0, min(reference.height - 1, int(float(region.get("y", 0.0)) * reference.height)))
    right = max(left + 1, min(reference.width, int((float(region.get("x", 0.0)) + float(region.get("width", 1.0))) * reference.width)))
    bottom = max(top + 1, min(reference.height, int((float(region.get("y", 0.0)) + float(region.get("height", 1.0))) * reference.height)))
    outside_mask = Image.new("L", reference.size, 255)
    ImageDraw.Draw(outside_mask).rectangle((left, top, right, bottom), fill=0)
    inside_mask = Image.new("L", reference.size, 0)
    ImageDraw.Draw(inside_mask).rectangle((left, top, right, bottom), fill=255)
    reference_edges = reference.convert("L").filter(ImageFilter.FIND_EDGES)

    with tempfile.TemporaryDirectory() as tmpdir:
        video_path = os.path.join(tmpdir, "input.mp4")
        with open(video_path, "wb") as handle:
            handle.write(video_bytes)
        perceptual_scores = []
        outside_differences = []
        inside_edge_differences = []
        for index, seek in enumerate(("0.10", "2.50")):
            frame_path = os.path.join(tmpdir, f"qa_{index}.png")
            command = [ffmpeg, "-y", "-ss", seek, "-i", video_path, "-frames:v", "1", frame_path]
            completed = subprocess.run(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=30, check=False)
            if completed.returncode != 0 or not os.path.exists(frame_path):
                continue
            frame = ImageOps.exif_transpose(Image.open(frame_path)).convert("RGB").resize(reference.size, Image.Resampling.LANCZOS)
            perceptual_scores.append(similarity(reference_hash, average_hash(frame)) * 100.0)
            difference = ImageChops.difference(reference.convert("L"), frame.convert("L"))
            outside_differences.append(masked_mean(difference, outside_mask))
            frame_edges = frame.convert("L").filter(ImageFilter.FIND_EDGES)
            edge_difference = ImageChops.difference(reference_edges, frame_edges)
            inside_edge_differences.append(masked_mean(edge_difference, inside_mask))
        if not perceptual_scores:
            return {"status": "unavailable", "score": None, "message": "未能从生成结果抽取质检帧。"}
        perceptual_score = round(min(perceptual_scores))
        outside_difference = round(max(outside_differences or [0.0]), 2)
        effect_edge_difference = round(max(inside_edge_differences or [0.0]), 2)
        outside_score = max(0.0, 100.0 - outside_difference * 5.0)
        score = round(min(perceptual_score, outside_score))
        passed = perceptual_score >= 55 and outside_difference <= 8.0
        return {
            "status": "passed" if passed else "failed",
            "score": score,
            "perceptual_similarity": perceptual_score,
            "outside_effect_difference": outside_difference,
            "effect_region_edge_difference": effect_edge_difference,
            "message": (
                f"产品与背景保护区稳定，整帧相似度约 {perceptual_score}%，保护区差异 {outside_difference}。"
                if passed
                else f"产品或背景发生重绘：整帧相似度约 {perceptual_score}%，保护区差异 {outside_difference}。"
            ),
        }

def assess_component_motion(
    video_bytes: bytes,
    target_region: dict[str, Any] | None = None,
    motion_name: str = "\u90e8\u4ef6",
    minimum_motion_coverage: float = 0.0,
) -> dict[str, Any]:
    """Reject static/global-camera results when articulated local motion was requested."""
    from PIL import Image, ImageChops, ImageOps, ImageStat

    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        return {"status": "unavailable", "score": None, "message": "FFmpeg \u4e0d\u53ef\u7528\uff0c\u65e0\u6cd5\u6267\u884c\u90e8\u4ef6\u8fd0\u52a8\u68c0\u67e5\u3002"}

    region = target_region or {"x": 0.15, "y": 0.28, "width": 0.70, "height": 0.62}

    def region_box(width: int, height: int) -> tuple[int, int, int, int]:
        left = max(0, min(width - 1, int(float(region.get("x", 0.15)) * width)))
        top = max(0, min(height - 1, int(float(region.get("y", 0.28)) * height)))
        right = max(left + 1, min(width, int((float(region.get("x", 0.15)) + float(region.get("width", 0.70))) * width)))
        bottom = max(top + 1, min(height, int((float(region.get("y", 0.28)) + float(region.get("height", 0.62))) * height)))
        return left, top, right, bottom

    def mean_difference(image) -> float:
        return float(ImageStat.Stat(image).mean[0])

    def zoom_alignment(reference, frame) -> tuple[float, float, float]:
        width, height = reference.size
        identity_error = mean_difference(ImageChops.difference(reference, frame))
        best_scale = 1.0
        best_error = identity_error
        for step in range(1, 13):
            scale = 1.0 + step * 0.01
            resized = reference.resize((int(round(width * scale)), int(round(height * scale))), Image.Resampling.LANCZOS)
            left = max(0, (resized.width - width) // 2)
            top = max(0, (resized.height - height) // 2)
            aligned = resized.crop((left, top, left + width, top + height))
            error = mean_difference(ImageChops.difference(aligned, frame))
            if error < best_error:
                best_scale, best_error = scale, error
        return best_scale, best_error, identity_error

    with tempfile.TemporaryDirectory() as tmpdir:
        video_path = os.path.join(tmpdir, "input.mp4")
        with open(video_path, "wb") as handle:
            handle.write(video_bytes)
        frames = []
        for index, seek in enumerate(("0.10", "2.50", "4.70")):
            frame_path = os.path.join(tmpdir, f"motion_{index}.png")
            command = [ffmpeg, "-y", "-ss", seek, "-i", video_path, "-frames:v", "1", frame_path]
            completed = subprocess.run(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=30, check=False)
            if completed.returncode == 0 and os.path.exists(frame_path):
                frames.append(ImageOps.exif_transpose(Image.open(frame_path)).convert("L").resize((160, 90), Image.Resampling.LANCZOS))
        if len(frames) < 2:
            return {"status": "unavailable", "score": None, "message": "\u672a\u80fd\u62bd\u53d6\u8db3\u591f\u5e27\u6267\u884c\u90e8\u4ef6\u8fd0\u52a8\u68c0\u67e5\u3002"}

        left, top, right, bottom = region_box(*frames[0].size)
        candidates = []
        for frame in frames[1:]:
            difference = ImageChops.difference(frames[0], frame)
            target_crop = difference.crop((left, top, right, bottom))
            target_difference = mean_difference(target_crop)
            target_values = list(target_crop.getdata())
            motion_coverage = sum(value >= 8 for value in target_values) / max(1, len(target_values))
            background_parts = [
                difference.crop((0, 0, difference.width, top)),
                difference.crop((0, bottom, difference.width, difference.height)),
                difference.crop((0, top, left, bottom)),
                difference.crop((right, top, difference.width, bottom)),
            ]
            weighted_total = 0.0
            weighted_pixels = 0
            for part in background_parts:
                if part.width and part.height:
                    pixels = part.width * part.height
                    weighted_total += mean_difference(part) * pixels
                    weighted_pixels += pixels
            background_difference = weighted_total / max(1, weighted_pixels)
            local_difference = max(0.0, target_difference - background_difference)
            ratio = target_difference / max(1.0, background_difference)
            best_scale, best_zoom_error, identity_error = zoom_alignment(frames[0], frame)
            zoom_explains_motion = best_scale >= 1.02 and best_zoom_error <= identity_error * 0.82
            candidates.append((local_difference, target_difference, background_difference, ratio, best_scale, zoom_explains_motion, motion_coverage))

        local_difference, target_difference, background_difference, ratio, best_scale, zoom_explains_motion, motion_coverage = max(candidates, key=lambda item: item[0])
        passed = (
            target_difference >= 4.5
            and local_difference >= 1.5
            and ratio >= 1.18
            and background_difference <= 16.0
            and not zoom_explains_motion
            and motion_coverage >= max(0.0, float(minimum_motion_coverage))
        )
        score = round(max(0.0, min(100.0, local_difference * 12.0 + (ratio - 1.0) * 40.0 + motion_coverage * 80.0)))
        if not passed:
            score = min(score, 49)
        return {
            "status": "passed" if passed else "failed",
            "score": score,
            "target_difference": round(target_difference, 2),
            "background_difference": round(background_difference, 2),
            "local_difference": round(local_difference, 2),
            "local_to_background_ratio": round(ratio, 2),
            "best_global_zoom_scale": round(best_scale, 3),
            "global_zoom_explains_motion": zoom_explains_motion,
            "message": (
                f"\u68c0\u6d4b\u5230\u76ee\u6807\u533a\u57df\u5b58\u5728\u72ec\u7acb{motion_name}\u8fd0\u52a8\uff0c\u4e14\u955c\u5934\u6574\u4f53\u8fd0\u52a8\u53d7\u63a7\u3002"
                if passed
                else f"\u672a\u68c0\u6d4b\u5230\u8db3\u591f\u7684\u72ec\u7acb{motion_name}\u8fd0\u52a8\uff0c\u6216\u7ed3\u679c\u4e3b\u8981\u662f\u5168\u5c40\u63a8\u955c/\u753b\u9762\u6f02\u79fb\u3002"
            ),
            "motion_coverage": round(motion_coverage, 3),
        }

def mute_video(video_bytes: bytes) -> bytes:
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        return video_bytes
    with tempfile.TemporaryDirectory() as tmpdir:
        source_path = os.path.join(tmpdir, "source.mp4")
        output_path = os.path.join(tmpdir, "muted.mp4")
        with open(source_path, "wb") as handle:
            handle.write(video_bytes)
        completed = subprocess.run(
            [
                ffmpeg, "-y", "-i", source_path, "-map", "0:v:0", "-map_metadata", "0",
                "-c:v", "copy", "-an", "-movflags", "+faststart+use_metadata_tags", output_path,
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=90,
            check=False,
        )
        if completed.returncode != 0 or not os.path.exists(output_path):
            return video_bytes
        with open(output_path, "rb") as handle:
            return handle.read()

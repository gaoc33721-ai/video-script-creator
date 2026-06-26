import base64
import concurrent.futures
import copy
import datetime as dt
import hashlib
import hmac
import io
import json
import os
import random
import re
import threading
import time
import urllib.parse
import urllib.request
import uuid
import xml.etree.ElementTree as ET

import boto3
import pandas as pd
from fastapi import FastAPI, Depends, File, Form, Header, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from product_feature_store import ProductFeatureStore, filter_product_features
from fridge_assistant import register_fridge_routes
from rainforest_competitor import (
    RainforestApiError,
    amazon_domain_for_market,
    clean_asin,
    discover_asins as rainforest_discover_asins,
    fetch_product as rainforest_fetch_product,
    normalize_product_response,
)
from social_competitor import (
    SocialApiError,
    classify_social_url,
    discover_youtube_videos,
    ensure_social_asset_fallbacks,
    extract_youtube_video_id,
    fetch_youtube_videos,
    normalize_social_url,
    normalize_youtube_video_item,
    refresh_social_thumbnail,
)
from storage_adapters import RuntimeStorage
from liblibai_provider import LiblibAIClient, LiblibAIConfig, LiblibAIError


APP_DATA_DIR = os.getenv("APP_DATA_DIR", ".")
os.makedirs(APP_DATA_DIR, exist_ok=True)
APP_BASE_PATH = "/" + os.getenv("APP_BASE_PATH", "").strip().strip("/")
if APP_BASE_PATH == "/":
    APP_BASE_PATH = ""
ADMIN_BASE_PATH = "/admin"

STORAGE = RuntimeStorage()
PRODUCT_FEATURE_STORE = ProductFeatureStore(STORAGE)

CACHE_META_KEY = "cache_meta.json"
HTTP_JOBS_KEY = "http_script_jobs.json"
NOVA_REEL_JOBS_KEY = "nova_reel_poc_jobs.json"
NOVA_CANVAS_JOBS_KEY = "nova_canvas_storyboard_jobs.json"
PRODUCT_IMAGE_ASSETS_KEY = "product_image_assets.json"
STORYBOARD_VIDEO_JOBS_KEY = "storyboard_video_jobs.json"
COMPETITOR_ASSETS_KEY = "competitor_assets.json"
SEED_COMPETITOR_ASSETS_KEY = "seed_competitor_assets.json"
COMPETITOR_RESEARCH_JOBS_KEY = "competitor_research_jobs.json"
HOTSPOTS_KEY = "hotspots.json"
HOTSPOT_SOURCES_KEY = "hotspot_sources.json"
COMPETITOR_CONFIGS_KEY = "competitor_configs.json"
COMPETITOR_COLLECTION_RUNS_KEY = "competitor_collection_runs.json"
SOCIAL_COLLECTION_SOURCES_KEY = "social_collection_sources.json"
COMPETITOR_ANALYSIS_RUNS_KEY = "competitor_analysis_runs.json"
ADMIN_JSON_CACHE_TTL = max(0, int(os.getenv("ADMIN_JSON_CACHE_TTL", "45")))
ADMIN_JSON_CACHE_KEYS = {
    COMPETITOR_ASSETS_KEY,
    HOTSPOTS_KEY,
    HOTSPOT_SOURCES_KEY,
    COMPETITOR_CONFIGS_KEY,
    COMPETITOR_COLLECTION_RUNS_KEY,
    SOCIAL_COLLECTION_SOURCES_KEY,
}
_json_cache: dict[str, dict] = {}
_json_cache_lock = threading.Lock()
TABLE_COLUMNS = [
    "镜头分段",
    "功能卖点（英文）",
    "画面示意&表现手法",
    "旁白（英文）",
    "字幕-显示卖点名及描述（英文）",
    "镜头运动&运动轨迹",
    "时长",
]
TABLE_HEADER_LINE = "| " + " | ".join(TABLE_COLUMNS) + " |"
TABLE_SEPARATOR_LINE = "| " + " | ".join([":---"] * len(TABLE_COLUMNS)) + " |"
MAX_SCRIPT_SEGMENTS = 6
SYSTEM_PROMPT = f"""##角色
你是“海外爆款内容引擎”的资深海外短视频创意导演、社媒内容策划和可落地分镜编剧。你的任务不是平铺产品参数，而是把海信产品卖点转化为以产品和被处理物品为主角、细节突出、动作清晰、适合轻量拍摄的海外电商短视频脚本，并保持可拍摄、可执行、可导出为 Excel 的 Markdown 表格。

##创意质量原则
1. 先在内部完成创意策略，不要输出思考过程：明确目标观众、产品主视觉、核心卖点的可视化证据、开场 hook、结尾品牌记忆点。
2. 默认采用“产品/物品展示优先”：产品本体、局部细节、操作面板、内部空间、配件、食材/衣物/餐具等被处理对象和使用前后效果必须是画面中心。除非用户明确要求人物剧情，不要写专业模特、正脸表演、复杂家庭成员关系或多人出镜。
3. 人物不是必需元素；如需出现，只写手部、手臂、背影、越肩视角或生活痕迹。人物动作必须服务于产品操作，如按键、开门、取放、倒入、擦拭、摆放，不让人物抢主视觉。
4. 每个核心卖点都要变成“可看见的产品动作或物品状态变化”：例如食物状态变化、操作面板反馈、内部空间利用、收纳前后、清洁前后、传统方式 vs 本品方式、成品质感特写。
5. 微波炉、烤箱、空气炸锅等厨房电器必须优先生成“产品 + 食材/餐具/台面/蒸汽/成品状态”的可拍摄场景；如果用户没有给出重点，请自动选择 1-2 个最适合的物品场景，如忙碌早餐食材入炉、电影夜爆米花膨胀、剩饭复热冒气、冷冻食品解冻、热饮加热、快速出餐摆盘。
6. 画面示意&表现手法必须具体到镜头动作和画面内容：不要写“展示产品功能”这种空话，要写“披萨放入腔体，手指按下触控面板，屏幕数字跳动，切到拉丝芝士和产品门体反光特写”这类产品清晰露出的可拍画面。
7. 每套方案必须明显不同：开场 hook、产品视角、物品状态、道具环境、镜头组织至少两处不同。避免三套都只是“产品特写 + 功能展示 + 品牌收尾”。
8. 创意可以丰富，但不得捏造产品卖点、参数、传感器、AI、变频、容量、菜单数量等事实；未出现在卖点库或用户输入中的功能不得写成确定功能。
9. 镜头分段必须像可拍摄分镜，而不是粗略目录：不允许只用“开场 / 产品切入 / 功能展示1 / 功能展示2 / 收尾”这种 5 段模板。需要按期望时长拆成足够多的短镜头，并按外部机构样例写成“时间段 + 生活场景任务 + 括号内阶段/卖点证据”，例如“0-4s 面板预约制冰（家中开场）”。
10. 参考行业优秀短视频经验：前 2 秒必须有反差、结果预告、痛点瞬间或动作钩子；中段用“动作触发转场 + 可见证据”推进，不要只按卖点列表顺序介绍；结尾用结果画面或品牌记忆收束。
11. 每套脚本至少包含 1 个有设计感的转场或镜头连接，例如动作匹配、遮挡转场、物体擦镜、声桥、前后对比切、推近接特写、俯拍切内腔；不得全程都是普通固定镜头。
12. 画面要有生活质感和感官细节：声音、蒸汽、水汽、纹理、光影、屏幕反馈、食物/衣物/地面状态变化等任选其一写进画面示意，避免“干净棚拍参数展示”的同质化。

##格式与语言硬约束
1. 第一输出必须是标准 Markdown 表格，绝对不要包裹在 ```markdown 或 ``` 代码块中。
2. 表格必须统一使用以下 7 列，并逐字使用该表头：
{TABLE_HEADER_LINE}
{TABLE_SEPARATOR_LINE}
3. 表格最后一行必须是“总时长”统计；“镜头分段”列写“总时长”，“时长”列写总秒数。
4. “旁白（英文）”和“字幕-显示卖点名及描述（英文）”两列必须是纯英文句子，不得带字段名/标签/括号前缀。
5. 其余列必须以中文为主，便于国内制作团队执行；允许少量 UI/LED/4K 等缩写。
6. 品牌收尾必须是产品静置全景特写 + Hisense Designed to Ease, Crafted to Cheer.
7. 表格单元格内不要使用英文竖线“|”；镜头分段和镜头轨迹里的分隔符统一使用中文全角“｜”或中文箭头“→”，避免 Markdown 表格断列。

##外部机构口令稿参考结构
你需要输出的表格要像专业 AI 视频机构的分镜口令，而不是普通脚本文案：
- “镜头分段”参考样例里的“镜头”字段：必须连续写出时间段、具体动作和阶段说明，例如“0-4s 面板预约制冰（家中开场）”“20-26s 回家开门 & 冰箱美学（Pureflat）”，不得只写“镜头01/功能展示/痛点开场”。
- “画面示意&表现手法”要合并画面示意与执行方式：写清前景/中景/背景、产品比例、产品位置、道具、被处理物品、手部动作、光影和构图；不要写“见图”“参考图”。
- “镜头运动&运动轨迹”参考样例里的“镜头运动/运镜轨迹”字段：必须像机构稿一样具体，例如“近景特写（CU）｜推进至面板｜手指按键特写”“快速切换（快剪）｜家门→换装→车内｜节奏上场”“俯视滑动 + 特写｜抽屉滑动轨迹｜取冰盒特写”；不要只写“特写/俯拍”。
- “功能卖点（英文）”必须优先使用卖点库 Feature Name 原文；没有直接卖点的开场/收尾镜头该列留空，或写真实产品/场景短语，不得写 Pain-point opening、Brand closing、Opening、Closing 等结构标签。
- 任何表格单元格都不得出现 Pain-point opening、Brand closing、Opening、Closing、Hook、Intro、Outro 等结构标签；这些只能作为内部创作思路，不能出现在字幕、旁白或卖点文案里。
- 每个卖点证明镜头都必须能被画面看见，不能只靠旁白说服。

##输出边界
只输出分镜脚本表格，不再输出整段 AI 视频生成 Prompt、Negative Prompt 或 Recommended Settings。
"""

BEDROCK_AWS_REGION = (
    os.getenv("BEDROCK_AWS_REGION")
    or os.getenv("AWS_REGION")
    or os.getenv("AWS_DEFAULT_REGION")
    or "us-east-1"
)
BEDROCK_MODEL_ID = os.getenv("BEDROCK_MODEL_ID", "qwen.qwen3-235b-a22b-2507-v1:0")
BEDROCK_MODEL_FALLBACK_IDS = [
    model_id.strip()
    for model_id in os.getenv(
        "BEDROCK_MODEL_FALLBACK_IDS",
        "openai.gpt-oss-120b-1:0,nvidia.nemotron-super-3-120b,mistral.devstral-2-123b,eu.amazon.nova-pro-v1:0",
    ).split(",")
    if model_id.strip() and model_id.strip() != BEDROCK_MODEL_ID
]
BEDROCK_MAX_TOKENS = int(os.getenv("BEDROCK_MAX_TOKENS", "16384"))
BEDROCK_FALLBACK_MAX_TOKENS = int(os.getenv("BEDROCK_FALLBACK_MAX_TOKENS", "8192"))
NOVA_REEL_AWS_REGION = os.getenv("NOVA_REEL_AWS_REGION", "us-east-1")
NOVA_REEL_MODEL_ID = os.getenv("NOVA_REEL_MODEL_ID", "amazon.nova-reel-v1:1")
NOVA_REEL_OUTPUT_S3_URI = os.getenv("NOVA_REEL_OUTPUT_S3_URI", "").rstrip("/")
NOVA_REEL_ESTIMATED_USD_PER_SECOND = float(os.getenv("NOVA_REEL_ESTIMATED_USD_PER_SECOND", "0.08"))
NOVA_CANVAS_AWS_REGION = os.getenv("NOVA_CANVAS_AWS_REGION", "us-west-2")
NOVA_CANVAS_MODEL_ID = os.getenv("NOVA_CANVAS_MODEL_ID", "stability.sd3-5-large-v1:0")
NOVA_CANVAS_ESTIMATED_USD_PER_IMAGE = float(os.getenv("NOVA_CANVAS_ESTIMATED_USD_PER_IMAGE", "0.08"))
NOVA_CANVAS_REFERENCE_STRENGTH = max(
    0.0,
    min(1.0, float(os.getenv("NOVA_CANVAS_REFERENCE_STRENGTH", "0.9"))),
)
MEDIA_IMAGE_PROVIDER = os.getenv("MEDIA_IMAGE_PROVIDER", os.getenv("IMAGE_GENERATION_PROVIDER", "nova_canvas")).strip().lower()
LIBLIBAI_BASE_URL = os.getenv("LIBLIBAI_BASE_URL", "https://openapi.liblibai.cloud")
LIBLIBAI_TEMPLATE_UUID = os.getenv("LIBLIBAI_TEMPLATE_UUID", "5d7e67009b344550bc1aa6ccbfa1d7f4")
LIBLIBAI_IMG2IMG_TEMPLATE_UUID = os.getenv("LIBLIBAI_IMG2IMG_TEMPLATE_UUID", "07e00af4fc464c7ab55ff906f8acf1b7")
LIBLIBAI_IMAGE_MODEL_LABEL = os.getenv("LIBLIBAI_IMAGE_MODEL_LABEL", "liblibai:star-3-alpha")
LIBLIBAI_IMAGE_ASPECT_RATIO = os.getenv("LIBLIBAI_IMAGE_ASPECT_RATIO", "landscape")
LIBLIBAI_IMAGE_WIDTH = int(os.getenv("LIBLIBAI_IMAGE_WIDTH", "1280"))
LIBLIBAI_IMAGE_HEIGHT = int(os.getenv("LIBLIBAI_IMAGE_HEIGHT", "720"))
LIBLIBAI_IMAGE_SIZE_ENABLED = os.getenv("LIBLIBAI_IMAGE_SIZE_ENABLED", "").strip().lower() in {"1", "true", "yes", "on"}
LIBLIBAI_IMAGE_STEPS = int(os.getenv("LIBLIBAI_IMAGE_STEPS", "20"))
LIBLIBAI_IMAGE_COUNT = int(os.getenv("LIBLIBAI_IMAGE_COUNT", "1"))
LIBLIBAI_REQUEST_TIMEOUT = int(os.getenv("LIBLIBAI_REQUEST_TIMEOUT", "90"))
LIBLIBAI_POLL_TIMEOUT = int(os.getenv("LIBLIBAI_POLL_TIMEOUT", "240"))
LIBLIBAI_POLL_INTERVAL = float(os.getenv("LIBLIBAI_POLL_INTERVAL", "3"))
LIBLIBAI_MAX_PROMPT_LENGTH = int(os.getenv("LIBLIBAI_MAX_PROMPT_LENGTH", "1800"))
LIBLIBAI_REFERENCE_CONTROL_TYPE = os.getenv("LIBLIBAI_REFERENCE_CONTROL_TYPE", "depth")
LIBLIBAI_REFERENCE_MODE = os.getenv("LIBLIBAI_REFERENCE_MODE", "controlnet")
LIBLIBAI_FALLBACK_TO_CONTROLNET = os.getenv("LIBLIBAI_FALLBACK_TO_CONTROLNET", "true").strip().lower() in {"1", "true", "yes", "on"}
STORYBOARD_IMAGE_WORKERS = max(1, int(os.getenv("STORYBOARD_IMAGE_WORKERS", "1")))
STORYBOARD_IMAGE_RETRY_COUNT = max(0, int(os.getenv("STORYBOARD_IMAGE_RETRY_COUNT", "2")))
STORYBOARD_IMAGE_RETRY_BACKOFF_SECONDS = max(1.0, float(os.getenv("STORYBOARD_IMAGE_RETRY_BACKOFF_SECONDS", "15")))
STORYBOARD_IMAGE_BRAND_STAMP_ENABLED = os.getenv(
    "STORYBOARD_IMAGE_BRAND_STAMP_ENABLED", "true"
).strip().lower() in {"1", "true", "yes", "on"}
_LIBLIBAI_ACCESS_KEY = os.getenv("LIBLIBAI_ACCESS_KEY", "")
_LIBLIBAI_ACCESS_KEY_SECRET_ID = (
    os.getenv("LIBLIBAI_ACCESS_KEY_SECRET_ID")
    or os.getenv("LIBLIBAI_ACCESS_KEY_SECRET_ARN")
    or os.getenv("LIBLIBAI_ACCESS_KEY_SECRET_NAME")
)
_LIBLIBAI_SECRET_KEY = os.getenv("LIBLIBAI_SECRET_KEY", "")
_LIBLIBAI_SECRET_KEY_SECRET_ID = (
    os.getenv("LIBLIBAI_SECRET_KEY_SECRET_ID")
    or os.getenv("LIBLIBAI_SECRET_KEY_SECRET_ARN")
    or os.getenv("LIBLIBAI_SECRET_KEY_SECRET_NAME")
)
_liblibai_access_key_cache = {"value": _LIBLIBAI_ACCESS_KEY, "expires_at": 0.0}
_liblibai_secret_key_cache = {"value": _LIBLIBAI_SECRET_KEY, "expires_at": 0.0}
RAINFOREST_DEFAULT_AMAZON_DOMAIN = os.getenv("RAINFOREST_DEFAULT_AMAZON_DOMAIN", "amazon.com")
RAINFOREST_SEARCH_TOP_N = int(os.getenv("RAINFOREST_SEARCH_TOP_N", "8"))
RAINFOREST_DISCOVERY_REQUEST_LIMIT = int(os.getenv("RAINFOREST_DISCOVERY_REQUEST_LIMIT", "6"))
RAINFOREST_MAX_PRODUCTS_PER_REFRESH = int(os.getenv("RAINFOREST_MAX_PRODUCTS_PER_REFRESH", "30"))
RAINFOREST_REQUEST_TIMEOUT = int(os.getenv("RAINFOREST_REQUEST_TIMEOUT", "30"))
ENABLE_SEED_COMPETITOR_ASSETS = os.getenv("ENABLE_SEED_COMPETITOR_ASSETS", "").strip().lower() in {"1", "true", "yes", "on"}
_RAINFOREST_API_KEY = os.getenv("RAINFOREST_API_KEY", "")
_RAINFOREST_API_KEY_SECRET_ID = (
    os.getenv("RAINFOREST_API_KEY_SECRET_ID")
    or os.getenv("RAINFOREST_API_KEY_SECRET_ARN")
    or os.getenv("RAINFOREST_API_KEY_SECRET_NAME")
)
_rainforest_api_key_cache = {"value": _RAINFOREST_API_KEY, "expires_at": 0.0}
YOUTUBE_DISCOVERY_TOP_N = int(os.getenv("YOUTUBE_DISCOVERY_TOP_N", "8"))
YOUTUBE_DISCOVERY_REQUEST_LIMIT = int(os.getenv("YOUTUBE_DISCOVERY_REQUEST_LIMIT", "4"))
SOCIAL_REQUEST_TIMEOUT = int(os.getenv("SOCIAL_REQUEST_TIMEOUT", "15"))
_YOUTUBE_API_KEY = os.getenv("YOUTUBE_API_KEY", "")
_YOUTUBE_API_KEY_SECRET_ID = (
    os.getenv("YOUTUBE_API_KEY_SECRET_ID")
    or os.getenv("YOUTUBE_API_KEY_SECRET_ARN")
    or os.getenv("YOUTUBE_API_KEY_SECRET_NAME")
)
_youtube_api_key_cache = {"value": _YOUTUBE_API_KEY, "expires_at": 0.0}
_SOCIAL_OEMBED_ACCESS_TOKEN = os.getenv("SOCIAL_OEMBED_ACCESS_TOKEN", "")
_SOCIAL_OEMBED_ACCESS_TOKEN_SECRET_ID = (
    os.getenv("SOCIAL_OEMBED_ACCESS_TOKEN_SECRET_ID")
    or os.getenv("SOCIAL_OEMBED_ACCESS_TOKEN_SECRET_ARN")
    or os.getenv("SOCIAL_OEMBED_ACCESS_TOKEN_SECRET_NAME")
)
_social_oembed_token_cache = {"value": _SOCIAL_OEMBED_ACCESS_TOKEN, "expires_at": 0.0}


def _bedrock_max_tokens_for_model(model_id: str, requested: int) -> int:
    if model_id == BEDROCK_MODEL_ID:
        return requested
    return min(requested, BEDROCK_FALLBACK_MAX_TOKENS)

app = FastAPI(title="海外爆款内容引擎 API")


@app.middleware("http")
async def _app_base_path_middleware(request: Request, call_next):
    path = request.scope.get("path") or ""
    if path == f"{ADMIN_BASE_PATH}/healthz" or path == f"{ADMIN_BASE_PATH}/api" or path.startswith(f"{ADMIN_BASE_PATH}/api/"):
        request.scope["path"] = path[len(ADMIN_BASE_PATH):] or "/"
    elif APP_BASE_PATH and APP_BASE_PATH != ADMIN_BASE_PATH:
        if path == APP_BASE_PATH:
            request.scope["path"] = "/"
        elif path.startswith(f"{APP_BASE_PATH}/"):
            request.scope["path"] = path[len(APP_BASE_PATH):] or "/"
    return await call_next(request)

# --- Security: CORS ---
_allowed_origins = [o.strip() for o in os.getenv("ALLOWED_ORIGINS", "").split(",") if o.strip()]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_allowed_origins or ["*"],
    allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Content-Type", "Authorization"],
)

# --- Security: simple password auth backed by env or Secrets Manager ---
_TRUTHY = {"1", "true", "yes", "on"}
_FALSY = {"0", "false", "no", "off"}
_API_ACCESS_PASSWORD = os.getenv("APP_ACCESS_PASSWORD", "")
_API_ACCESS_PASSWORD_SECRET_ID = (
    os.getenv("APP_ACCESS_PASSWORD_SECRET_ID")
    or os.getenv("APP_ACCESS_PASSWORD_SECRET_ARN")
    or os.getenv("APP_ACCESS_PASSWORD_SECRET_NAME")
)
_API_ACCESS_PASSWORD_CACHE_TTL = int(os.getenv("APP_ACCESS_PASSWORD_CACHE_TTL", "300"))
_API_ACCESS_CONTROL_SETTING = os.getenv("APP_ACCESS_CONTROL_ENABLED", "auto").strip().lower()
_API_ACCESS_COOKIE_NAME = os.getenv("APP_ACCESS_COOKIE_NAME", "video_script_access")
_access_password_cache = {"value": _API_ACCESS_PASSWORD, "expires_at": 0.0}

def _secret_region():
    return os.getenv("APP_ACCESS_PASSWORD_SECRET_REGION") or os.getenv("AWS_REGION") or os.getenv("AWS_DEFAULT_REGION") or BEDROCK_AWS_REGION


def _extract_secret_password(secret_value: str) -> str:
    text = str(secret_value or "").strip()
    if not text:
        return ""
    try:
        payload = json.loads(text)
        if isinstance(payload, dict):
            for key in (
                "password",
                "APP_ACCESS_PASSWORD",
                "app_access_password",
                "RAINFOREST_API_KEY",
                "YOUTUBE_API_KEY",
                "SOCIAL_OEMBED_ACCESS_TOKEN",
                "api_key",
                "access_token",
                "access_key",
                "secret_key",
                "AccessKey",
                "SecretKey",
                "LIBLIBAI_ACCESS_KEY",
                "LIBLIBAI_SECRET_KEY",
                "token",
                "value",
            ):
                if payload.get(key):
                    return str(payload[key]).strip()
    except Exception:
        pass
    return text


def _current_access_password() -> str:
    if not _API_ACCESS_PASSWORD_SECRET_ID:
        return _API_ACCESS_PASSWORD
    now = time.monotonic()
    cached_value = str(_access_password_cache.get("value") or "")
    if cached_value and now < float(_access_password_cache.get("expires_at") or 0):
        return cached_value
    try:
        client = boto3.client("secretsmanager", region_name=_secret_region())
        response = client.get_secret_value(SecretId=_API_ACCESS_PASSWORD_SECRET_ID)
        value = _extract_secret_password(response.get("SecretString", ""))
        if value:
            _access_password_cache["value"] = value
            _access_password_cache["expires_at"] = now + max(_API_ACCESS_PASSWORD_CACHE_TTL, 5)
            return value
    except Exception:
        pass
    return cached_value or _API_ACCESS_PASSWORD


def _current_rainforest_api_key() -> str:
    if not _RAINFOREST_API_KEY_SECRET_ID:
        return _RAINFOREST_API_KEY
    now = time.monotonic()
    cached_value = str(_rainforest_api_key_cache.get("value") or "")
    if cached_value and now < float(_rainforest_api_key_cache.get("expires_at") or 0):
        return cached_value
    try:
        client = boto3.client("secretsmanager", region_name=_secret_region())
        response = client.get_secret_value(SecretId=_RAINFOREST_API_KEY_SECRET_ID)
        value = _extract_secret_password(response.get("SecretString", ""))
        if value:
            _rainforest_api_key_cache["value"] = value
            _rainforest_api_key_cache["expires_at"] = now + max(_API_ACCESS_PASSWORD_CACHE_TTL, 5)
            return value
    except Exception:
        pass
    return cached_value or _RAINFOREST_API_KEY


def _current_external_secret(initial_value: str, secret_id: str, cache: dict) -> str:
    if not secret_id:
        return initial_value
    now = time.monotonic()
    cached_value = str(cache.get("value") or "")
    if cached_value and now < float(cache.get("expires_at") or 0):
        return cached_value
    try:
        client = boto3.client("secretsmanager", region_name=_secret_region())
        response = client.get_secret_value(SecretId=secret_id)
        value = _extract_secret_password(response.get("SecretString", ""))
        if value:
            cache["value"] = value
            cache["expires_at"] = now + max(_API_ACCESS_PASSWORD_CACHE_TTL, 5)
            return value
    except Exception:
        pass
    return cached_value or initial_value


def _current_youtube_api_key() -> str:
    return _current_external_secret(_YOUTUBE_API_KEY, _YOUTUBE_API_KEY_SECRET_ID, _youtube_api_key_cache)


def _current_social_oembed_token() -> str:
    return _current_external_secret(
        _SOCIAL_OEMBED_ACCESS_TOKEN,
        _SOCIAL_OEMBED_ACCESS_TOKEN_SECRET_ID,
        _social_oembed_token_cache,
    )


def _current_liblibai_access_key() -> str:
    return _current_external_secret(
        _LIBLIBAI_ACCESS_KEY,
        _LIBLIBAI_ACCESS_KEY_SECRET_ID,
        _liblibai_access_key_cache,
    )


def _current_liblibai_secret_key() -> str:
    return _current_external_secret(
        _LIBLIBAI_SECRET_KEY,
        _LIBLIBAI_SECRET_KEY_SECRET_ID,
        _liblibai_secret_key_cache,
    )


def _access_control_active() -> bool:
    configured = bool(_API_ACCESS_PASSWORD or _API_ACCESS_PASSWORD_SECRET_ID)
    if _API_ACCESS_CONTROL_SETTING in _TRUTHY:
        return True
    if _API_ACCESS_CONTROL_SETTING in _FALSY:
        return False
    return configured


def _clean_access_token(raw_value: str) -> str:
    token = str(raw_value or "").strip()
    if token.lower().startswith("bearer "):
        token = token[7:].strip()
    return token


def _is_valid_access_token(raw_value: str) -> bool:
    expected = _current_access_password()
    token = _clean_access_token(raw_value)
    if not expected:
        return False
    return hmac.compare_digest(token.encode("utf-8"), expected.encode("utf-8"))


async def _verify_access(request: Request, authorization: str = Header(default="")):
    """Dependency that gates platform APIs behind the shared access password."""
    if not _access_control_active():
        return
    token = authorization
    if not _is_valid_access_token(token):
        raise HTTPException(status_code=401, detail="访问密码不正确或已过期。")
# --- Security: upload size limit ---
MAX_UPLOAD_BYTES = 20 * 1024 * 1024  # 20 MB
MAX_PRODUCT_IMAGE_BYTES = 12 * 1024 * 1024  # 12 MB

job_lock = threading.Lock()
competitor_lock = threading.Lock()
storyboard_image_executor = concurrent.futures.ThreadPoolExecutor(
    max_workers=STORYBOARD_IMAGE_WORKERS,
    thread_name_prefix="storyboard-image",
)

static_dir = os.path.join(os.path.dirname(__file__), "web_frontend")
if os.path.isdir(static_dir):
    admin_static_dir = os.path.join(static_dir, "admin")
    if os.path.isdir(admin_static_dir):
        app.mount(f"{ADMIN_BASE_PATH}/static", StaticFiles(directory=admin_static_dir), name="admin_static")
    app.mount("/static", StaticFiles(directory=static_dir), name="static")

register_fridge_routes(app, STORAGE, _current_access_password, _clean_access_token, _access_control_active)


class GenerateRequest(BaseModel):
    platform: str = "TikTok / Reels / Shorts"
    target_market: str = "北美 (US/CA)"
    variant_count: int = Field(default=2, ge=1, le=5)
    category: str
    model: str
    selected_features: list[str] = Field(default_factory=list)
    video_usage: str = "站外种草"
    video_type: list[str] = Field(default_factory=list)
    expected_duration: int = Field(default=30, ge=6, le=90)
    project_type: str = "常规上新"
    target_audience: str = ""
    pain_points: str = ""
    custom_requirements: str = ""
    use_competitor_context: bool = True
    use_hotspot_context: bool = True
    competitor_asset_ids: list[str] = Field(default_factory=list, max_length=20)
    hotspot_ids: list[str] = Field(default_factory=list, max_length=20)


class AuthLoginRequest(BaseModel):
    password: str = Field(default="", max_length=256)


class NovaReelSubmitRequest(BaseModel):
    script_job_id: str
    variant_index: int = Field(default=0, ge=0)


class NovaCanvasSubmitRequest(BaseModel):
    script_job_id: str
    variant_index: int = Field(default=0, ge=0)
    shot_index: int = Field(default=0, ge=0)
    prompt: str = Field(min_length=10, max_length=6000)
    product_image_id: str = ""


class StoryboardVideoSubmitRequest(BaseModel):
    script_job_id: str
    variant_index: int = Field(default=0, ge=0)
    product_image_id: str = ""
    shot_index: int = Field(default=-1, ge=-1)


class RainforestDiscoverRequest(BaseModel):
    category: str = ""
    target_market: str = "北美 (US/CA)"
    amazon_domain: str = ""
    brands: list[str] = Field(default_factory=list)
    keywords: list[str] = Field(default_factory=list)
    max_results: int = Field(default=RAINFOREST_SEARCH_TOP_N, ge=1, le=50)
    request_limit: int = Field(default=RAINFOREST_DISCOVERY_REQUEST_LIMIT, ge=1, le=20)


class RainforestRefreshRequest(BaseModel):
    category: str = ""
    target_market: str = "北美 (US/CA)"
    amazon_domain: str = ""
    brands: list[str] = Field(default_factory=list)
    keywords: list[str] = Field(default_factory=list)
    asins: list[str] = Field(default_factory=list)
    use_discovery: bool = True
    max_search_results: int = Field(default=RAINFOREST_SEARCH_TOP_N, ge=1, le=50)
    request_limit: int = Field(default=RAINFOREST_DISCOVERY_REQUEST_LIMIT, ge=1, le=20)
    max_products: int = Field(default=RAINFOREST_MAX_PRODUCTS_PER_REFRESH, ge=1, le=200)


class CompetitorResearchRequest(BaseModel):
    question: str = Field(min_length=4, max_length=2000)
    category: str = ""
    target_market: str = ""
    platform: str = "Amazon"
    source: str = "rainforest"
    top_k: int = Field(default=8, ge=1, le=20)


class SocialUrlImportRequest(BaseModel):
    urls: list[str] = Field(default_factory=list, max_length=50)
    category: str = ""
    target_market: str = ""
    brands: list[str] = Field(default_factory=list)
    fetch_oembed: bool = True


class YouTubeDiscoverRequest(BaseModel):
    category: str = ""
    target_market: str = "北美 (US/CA)"
    region_code: str = ""
    brands: list[str] = Field(default_factory=list)
    keywords: list[str] = Field(default_factory=list)
    max_results: int = Field(default=YOUTUBE_DISCOVERY_TOP_N, ge=1, le=25)
    request_limit: int = Field(default=YOUTUBE_DISCOVERY_REQUEST_LIMIT, ge=1, le=10)


class YouTubeRefreshRequest(BaseModel):
    category: str = ""
    target_market: str = "北美 (US/CA)"
    region_code: str = ""
    brands: list[str] = Field(default_factory=list)
    keywords: list[str] = Field(default_factory=list)
    video_ids: list[str] = Field(default_factory=list, max_length=200)
    use_discovery: bool = True
    max_results: int = Field(default=YOUTUBE_DISCOVERY_TOP_N, ge=1, le=25)
    request_limit: int = Field(default=YOUTUBE_DISCOVERY_REQUEST_LIMIT, ge=1, le=10)
    max_videos: int = Field(default=30, ge=1, le=200)


class SocialThumbnailRefreshRequest(BaseModel):
    q: str = ""
    category: str = ""
    platform: str = ""
    source: str = ""
    media_type: str = ""
    asset_ids: list[str] = Field(default_factory=list, max_length=200)
    limit: int = Field(default=20, ge=1, le=200)


class CompetitorDeepAnalysisRequest(BaseModel):
    asset_ids: list[str] = Field(default_factory=list, max_length=50)
    category: str = ""
    target_market: str = ""
    platform: str = ""
    source: str = ""
    q: str = ""
    media_type: str = ""
    review_status: str = ""
    max_assets: int = Field(default=12, ge=1, le=50)
    concurrency: int = Field(default=4, ge=1, le=8)
    force: bool = False


class CompetitorAssetPatchRequest(BaseModel):
    review_status: str | None = None
    rights_status: str | None = None
    ai_tags: list[str] | None = None
    ai_analysis: str | None = None
    category: str | None = None
    brand: str | None = None
    quality_score: int | None = Field(default=None, ge=0, le=100)


class CompetitorBulkReviewRequest(BaseModel):
    asset_ids: list[str] = Field(default_factory=list, min_length=1, max_length=200)
    review_status: str = Field(pattern="^(auto_collected|approved|featured|rejected|needs_review)$")


class HotspotRequest(BaseModel):
    title: str = Field(min_length=1, max_length=240)
    source_type: str = "manual"
    source_name: str = "人工录入"
    source_url: str = ""
    category: str = ""
    target_market: str = ""
    platform: str = ""
    heat_score: int = Field(default=50, ge=0, le=100)
    valid_from: str = ""
    valid_to: str = ""
    status: str = "active"
    tags: list[str] = Field(default_factory=list, max_length=20)
    notes: str = ""


class HotspotPatchRequest(BaseModel):
    title: str | None = None
    source_type: str | None = None
    source_name: str | None = None
    source_url: str | None = None
    category: str | None = None
    target_market: str | None = None
    platform: str | None = None
    heat_score: int | None = Field(default=None, ge=0, le=100)
    valid_from: str | None = None
    valid_to: str | None = None
    status: str | None = None
    tags: list[str] | None = None
    notes: str | None = None


class HotspotRefreshRequest(BaseModel):
    category: str = ""
    target_market: str = "北美 (US/CA)"
    platform: str = ""
    source_ids: list[str] = Field(default_factory=list, max_length=20)


class HotspotSourceRequest(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    source_type: str = Field(pattern="^(google_trends_rss|ecommerce_calendar|rss|manual)$")
    url: str = ""
    target_market: str = ""
    category: str = ""
    platform: str = ""
    enabled: bool = True
    keywords: list[str] = Field(default_factory=list, max_length=40)


class HotspotSourcePatchRequest(BaseModel):
    name: str | None = None
    source_type: str | None = None
    url: str | None = None
    target_market: str | None = None
    category: str | None = None
    platform: str | None = None
    enabled: bool | None = None
    keywords: list[str] | None = None


class CompetitorConfigRequest(BaseModel):
    category: str = Field(min_length=1, max_length=120)
    brands: list[str] = Field(default_factory=list, max_length=80)
    keywords: list[str] = Field(default_factory=list, max_length=80)
    platforms: list[str] = Field(default_factory=list, max_length=20)
    target_market: str = ""
    refresh_frequency: str = "manual"
    notes: str = ""


class CompetitorConfigPatchRequest(BaseModel):
    brands: list[str] | None = None
    keywords: list[str] | None = None
    platforms: list[str] | None = None
    target_market: str | None = None
    refresh_frequency: str | None = None
    notes: str | None = None


class CompetitorCollectionRunRequest(BaseModel):
    category: str = ""
    target_market: str = ""
    platform: str = ""
    source: str = ""
    brands: list[str] = Field(default_factory=list, max_length=80)
    keywords: list[str] = Field(default_factory=list, max_length=80)
    status: str = "queued"
    error_message: str = ""


def _clone_json(value):
    try:
        return copy.deepcopy(value)
    except Exception:
        return value


def _read_json(key, default_value):
    ttl = ADMIN_JSON_CACHE_TTL if key in ADMIN_JSON_CACHE_KEYS else 0
    if ttl:
        now = time.monotonic()
        with _json_cache_lock:
            cached = _json_cache.get(key)
            if cached and cached.get("expires_at", 0) > now:
                return _clone_json(cached.get("value"))
    value = STORAGE.read_json(key, default_value)
    if ttl:
        with _json_cache_lock:
            _json_cache[key] = {"value": _clone_json(value), "expires_at": time.monotonic() + ttl}
    return value


def _write_json(key, payload):
    ok = STORAGE.write_json(key, payload)
    if ok:
        with _json_cache_lock:
            _json_cache.pop(key, None)
    return ok


def _load_products() -> pd.DataFrame:
    return PRODUCT_FEATURE_STORE.load()


def _load_jobs():
    data = _read_json(HTTP_JOBS_KEY, [])
    return data if isinstance(data, list) else []


def _save_jobs(jobs):
    return _write_json(HTTP_JOBS_KEY, jobs[:100])


def _public_job_summary(job: dict) -> dict:
    variants = job.get("variants") or []
    request = dict(job.get("request") or {})
    return {
        "id": job.get("id", ""),
        "created_at": job.get("created_at", ""),
        "updated_at": job.get("updated_at", ""),
        "completed_at": job.get("completed_at", ""),
        "status": job.get("status", ""),
        "progress": job.get("progress", 0),
        "current_step": job.get("current_step", ""),
        "request": request,
        "error_message": job.get("error_message", ""),
        "variant_count": len(variants),
        "has_variants": bool(variants),
    }


def _utc_now() -> str:
    return dt.datetime.utcnow().isoformat(timespec="seconds") + "Z"


def _stable_id(prefix: str, *parts: str) -> str:
    raw = "|".join(str(part or "").strip().lower() for part in parts)
    return f"{prefix}:{hashlib.sha1(raw.encode('utf-8')).hexdigest()[:16]}"


def _parse_date(value: str | None) -> dt.date | None:
    text = str(value or "").strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        return dt.datetime.fromisoformat(text).date()
    except Exception:
        pass
    try:
        return dt.date.fromisoformat(text[:10])
    except Exception:
        return None


def _parse_datetime(value: str | None) -> dt.datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = dt.datetime.fromisoformat(text)
        if parsed.tzinfo:
            parsed = parsed.astimezone(dt.timezone.utc).replace(tzinfo=None)
        return parsed
    except Exception:
        try:
            day = dt.date.fromisoformat(text[:10])
            return dt.datetime.combine(day, dt.time.min)
        except Exception:
            return None


def _clean_list(values, limit=50):
    result = []
    for value in values or []:
        clean = str(value or "").strip()
        if clean and clean not in result:
            result.append(clean)
        if len(result) >= limit:
            break
    return result


GENERIC_COMPETITOR_CATEGORIES = {
    "未选择",
    "全部",
    "全部品类",
    "通用",
    "其他小家电",
    "小家电",
    "all",
    "all categories",
    "other small appliances",
    "small appliances",
}


def _competitor_category_filter_value(category: str) -> str:
    text = str(category or "").strip()
    if not text:
        return ""
    return "" if text.lower() in {item.lower() for item in GENERIC_COMPETITOR_CATEGORIES} else text.lower()


def _amazon_product_from_url(url: str) -> dict:
    text = str(url or "").strip()
    if not text:
        return {}
    parsed = urllib.parse.urlparse(text)
    host = parsed.netloc.lower()
    if host.startswith("www."):
        host = host[4:]
    if host.startswith("m."):
        host = host[2:]
    if host.startswith("smile."):
        host = host[6:]
    if not (host == "amazon.com" or host.startswith("amazon.") or ".amazon." in host):
        return {}

    asin = ""
    decoded_path = urllib.parse.unquote(parsed.path or "")
    patterns = [
        r"/(?:[^/]+/)?dp/([A-Za-z0-9]{10})(?:[/?]|$)",
        r"/gp/product/([A-Za-z0-9]{10})(?:[/?]|$)",
        r"/product/([A-Za-z0-9]{10})(?:[/?]|$)",
    ]
    for pattern in patterns:
        match = re.search(pattern, decoded_path)
        if match:
            asin = clean_asin(match.group(1))
            break
    if not asin:
        query = urllib.parse.parse_qs(parsed.query)
        for key in ("asin", "ASIN"):
            if query.get(key):
                asin = clean_asin(query[key][0])
                break
    if not asin:
        return {}
    return {"asin": asin, "amazon_domain": host, "url": text}


def _load_seed_competitor_assets():
    if not ENABLE_SEED_COMPETITOR_ASSETS:
        return []
    path = os.path.join(os.path.dirname(__file__), SEED_COMPETITOR_ASSETS_KEY)
    if not os.path.exists(path):
        return []
    try:
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
        return data if isinstance(data, list) else []
    except Exception:
        return []


def _load_competitor_assets():
    data = _read_json(COMPETITOR_ASSETS_KEY, [])
    stored_assets = data if isinstance(data, list) else []
    by_id = {}
    for item in _load_seed_competitor_assets():
        if isinstance(item, dict) and item.get("id"):
            by_id[str(item["id"])] = item
    for item in stored_assets:
        if isinstance(item, dict) and item.get("id"):
            by_id[str(item["id"])] = item
    return list(by_id.values())


def _asset_media_types(asset: dict) -> set[str]:
    media_types = {
        str(item.get("media_type") or "").strip().lower()
        for item in (asset.get("media") or [])
        if isinstance(item, dict) and item.get("media_type")
    }
    source_url = str(asset.get("source_url") or asset.get("canonical_url") or "").lower()
    image_url = str(asset.get("image_url") or "").lower()
    if source_url.endswith(".gif") or image_url.endswith(".gif"):
        media_types.add("gif")
    return {item for item in media_types if item}


def _ensure_asset_admin_defaults(asset: dict) -> dict:
    item = dict(asset or {})
    now = _utc_now()
    item["rights_status"] = item.get("rights_status") or "link_only_no_raw_video"
    item["review_status"] = item.get("review_status") or "auto_collected"
    item["quality_score"] = int(item.get("quality_score") or 0)
    item["created_at"] = item.get("created_at") or now
    item["updated_at"] = item.get("updated_at") or now
    item["collected_at"] = item.get("collected_at") or now
    item["ai_tags"] = _clean_list(item.get("ai_tags") or [], limit=30)
    item = ensure_social_asset_fallbacks(item)
    item["ai_tags"] = _clean_list(item.get("ai_tags") or [], limit=30)

    source_url = str(item.get("source_url") or item.get("canonical_url") or "").lower()
    if source_url.endswith(".gif") and "动图素材" not in item["ai_tags"]:
        item["ai_tags"].append("动图素材")
    media = []
    for media_item in item.get("media", []) or []:
        if not isinstance(media_item, dict):
            continue
        next_item = dict(media_item)
        media_url = str(next_item.get("media_url") or next_item.get("thumbnail_url") or "").lower()
        if media_url.endswith(".gif"):
            next_item["media_type"] = "gif"
        next_item["rights_status"] = next_item.get("rights_status") or item["rights_status"]
        media.append(next_item)
    item["media"] = media
    return item


def _save_competitor_assets(assets):
    sorted_assets = sorted(
        [_ensure_asset_admin_defaults(item) for item in assets if isinstance(item, dict)],
        key=lambda item: str(item.get("collected_at") or item.get("updated_at") or ""),
        reverse=True,
    )
    return _write_json(COMPETITOR_ASSETS_KEY, sorted_assets[:5000])


def _is_social_profile_asset(asset: dict) -> bool:
    item = asset or {}
    platform = str(item.get("platform") or "").strip()
    metadata = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
    content_id = str(metadata.get("platform_content_id") or "").strip()
    source_type = str(item.get("source_type") or "").strip()
    title = str(item.get("title") or "").strip().lower()
    image_url = str(item.get("image_url") or "").strip()

    if (
        platform == "Instagram"
        and source_type == "instagram_manual"
        and re.fullmatch(r"[0-9a-f]{16}", content_id or "")
        and not image_url
        and ("instagram social asset" in title or metadata.get("collected_method") == "manual_url")
    ):
        return True

    source_url = str(item.get("source_url") or item.get("canonical_url") or "").strip()
    if platform not in {"Instagram", "TikTok", "Pinterest", "Facebook", "YouTube"} or not source_url:
        return False
    try:
        return classify_social_url(source_url).get("kind") != "asset"
    except Exception:
        return False


def _visible_competitor_assets(assets: list[dict]) -> list[dict]:
    return [item for item in assets if not _is_social_profile_asset(item)]


def _asset_review_status(asset: dict) -> str:
    return str((asset or {}).get("review_status") or "auto_collected").strip().lower()


def _business_usable_competitor_assets(assets: list[dict]) -> list[dict]:
    return [item for item in assets if _asset_review_status(item) != "rejected"]


def _unique_competitor_assets(assets: list[dict], limit: int) -> list[dict]:
    seen = set()
    unique = []
    for asset in assets:
        asset_id = str((asset or {}).get("id") or "")
        if not asset_id or asset_id in seen:
            continue
        seen.add(asset_id)
        unique.append(asset)
        if len(unique) >= limit:
            break
    return unique


def _upsert_competitor_assets(new_assets):
    now = dt.datetime.utcnow().isoformat(timespec="seconds") + "Z"
    with competitor_lock:
        assets = _load_competitor_assets()
        by_id = {str(item.get("id") or ""): dict(item) for item in assets if item.get("id")}
        inserted = 0
        updated = 0
        for asset in new_assets:
            asset_id = str((asset or {}).get("id") or "")
            if not asset_id:
                continue
            existing = by_id.get(asset_id, {})
            merged = _ensure_asset_admin_defaults({**existing, **asset})
            merged["updated_at"] = now
            if "created_at" not in merged:
                merged["created_at"] = existing.get("created_at") or now
            by_id[asset_id] = merged
            if existing:
                updated += 1
            else:
                inserted += 1
        _save_competitor_assets(list(by_id.values()))
    return {"inserted": inserted, "updated": updated, "total": inserted + updated}


def _load_competitor_research_jobs():
    data = _read_json(COMPETITOR_RESEARCH_JOBS_KEY, [])
    return data if isinstance(data, list) else []


def _save_competitor_research_jobs(jobs):
    return _write_json(COMPETITOR_RESEARCH_JOBS_KEY, jobs[:200])


def _load_competitor_analysis_runs():
    data = _read_json(COMPETITOR_ANALYSIS_RUNS_KEY, [])
    return data if isinstance(data, list) else []


def _save_competitor_analysis_runs(runs):
    return _write_json(COMPETITOR_ANALYSIS_RUNS_KEY, runs[:200])


def _update_competitor_analysis_run(run_id: str, **fields):
    with competitor_lock:
        runs = _load_competitor_analysis_runs()
        for run in runs:
            if run.get("id") == run_id:
                run.update(fields)
                now = _utc_now()
                run["updated_at"] = now
                if fields.get("status") in {"succeeded", "failed", "partial"}:
                    run["completed_at"] = now
                break
        _save_competitor_analysis_runs(runs)


def _load_social_collection_sources():
    data = _read_json(SOCIAL_COLLECTION_SOURCES_KEY, [])
    return data if isinstance(data, list) else []


def _save_social_collection_sources(sources):
    now = _utc_now()
    cleaned = []
    for source in sources:
        if not isinstance(source, dict):
            continue
        item = dict(source)
        item["id"] = str(item.get("id") or _stable_id("social-source", item.get("platform", ""), item.get("source_url", "")))
        item["source_url"] = str(item.get("source_url") or "").strip()
        item["platform"] = str(item.get("platform") or "Social").strip()
        item["handle"] = str(item.get("handle") or "").strip()
        item["category"] = str(item.get("category") or "").strip()
        item["target_market"] = str(item.get("target_market") or "").strip()
        item["brands"] = _clean_list(item.get("brands") or [], limit=80)
        item["status"] = str(item.get("status") or "source_only").strip()
        item["message"] = str(item.get("message") or "").strip()
        item["created_at"] = item.get("created_at") or now
        item["updated_at"] = item.get("updated_at") or now
        cleaned.append(item)
    cleaned.sort(key=lambda item: str(item.get("updated_at") or item.get("created_at") or ""), reverse=True)
    return _write_json(SOCIAL_COLLECTION_SOURCES_KEY, cleaned[:1000])


def _upsert_social_collection_sources(sources):
    now = _utc_now()
    existing = _load_social_collection_sources()
    by_id = {str(item.get("id") or ""): dict(item) for item in existing if item.get("id")}
    inserted = 0
    updated = 0
    for source in sources:
        if not isinstance(source, dict):
            continue
        source_id = str(source.get("id") or _stable_id("social-source", source.get("platform", ""), source.get("source_url", "")))
        previous = by_id.get(source_id, {})
        merged = {**previous, **source, "id": source_id, "updated_at": now}
        if not previous:
            merged["created_at"] = source.get("created_at") or now
            inserted += 1
        else:
            updated += 1
        by_id[source_id] = merged
    _save_social_collection_sources(list(by_id.values()))
    return {"inserted": inserted, "updated": updated, "total": inserted + updated}


def _load_hotspots():
    data = _read_json(HOTSPOTS_KEY, [])
    return data if isinstance(data, list) else []


def _save_hotspots(hotspots):
    normalized = [_normalize_hotspot(item) for item in hotspots if isinstance(item, dict)]
    normalized.sort(
        key=lambda item: (
            int(item.get("heat_score") or 0),
            str(item.get("updated_at") or item.get("created_at") or ""),
        ),
        reverse=True,
    )
    return _write_json(HOTSPOTS_KEY, normalized[:3000])


def _default_hotspot_sources():
    return [
        {
            "id": "google-trends-us",
            "name": "Google Trends 北美",
            "source_type": "google_trends_rss",
            "url": "",
            "target_market": "北美 (US/CA)",
            "category": "",
            "platform": "",
            "enabled": True,
            "keywords": [],
            "created_at": _utc_now(),
            "updated_at": _utc_now(),
        },
        {
            "id": "ecommerce-calendar",
            "name": "电商节日节点",
            "source_type": "ecommerce_calendar",
            "url": "",
            "target_market": "",
            "category": "",
            "platform": "",
            "enabled": True,
            "keywords": [],
            "created_at": _utc_now(),
            "updated_at": _utc_now(),
        },
    ]


def _load_hotspot_sources():
    data = _read_json(HOTSPOT_SOURCES_KEY, [])
    if not isinstance(data, list):
        return _default_hotspot_sources()
    if not data and not STORAGE.exists(HOTSPOT_SOURCES_KEY):
        return _default_hotspot_sources()
    return data


def _save_hotspot_sources(sources):
    cleaned = []
    now = _utc_now()
    for source in sources:
        if not isinstance(source, dict):
            continue
        item = dict(source)
        item["id"] = str(item.get("id") or _stable_id("hotspot-source", item.get("source_type", ""), item.get("name", ""), item.get("url", "")))
        item["name"] = str(item.get("name") or item.get("source_type") or "热点源").strip()
        item["source_type"] = str(item.get("source_type") or "manual").strip()
        item["enabled"] = bool(item.get("enabled", True))
        item["keywords"] = _clean_list(item.get("keywords") or [], limit=40)
        item["created_at"] = item.get("created_at") or now
        item["updated_at"] = item.get("updated_at") or now
        cleaned.append(item)
    return _write_json(HOTSPOT_SOURCES_KEY, cleaned[:200])


def _load_competitor_configs():
    data = _read_json(COMPETITOR_CONFIGS_KEY, [])
    return data if isinstance(data, list) else []


def _save_competitor_configs(configs):
    now = _utc_now()
    cleaned = []
    for config in configs:
        if not isinstance(config, dict):
            continue
        item = dict(config)
        item["id"] = str(item.get("id") or _stable_id("competitor-config", item.get("category", "")))
        item["brands"] = _clean_list(item.get("brands") or [], limit=80)
        item["keywords"] = _clean_list(item.get("keywords") or [], limit=80)
        item["platforms"] = _clean_list(item.get("platforms") or [], limit=20)
        item["updated_at"] = item.get("updated_at") or now
        cleaned.append(item)
    return _write_json(COMPETITOR_CONFIGS_KEY, cleaned[:500])


def _load_collection_runs():
    data = _read_json(COMPETITOR_COLLECTION_RUNS_KEY, [])
    return data if isinstance(data, list) else []


def _save_collection_runs(runs):
    cleaned = [dict(item) for item in runs if isinstance(item, dict)]
    cleaned.sort(key=lambda item: str(item.get("created_at") or ""), reverse=True)
    return _write_json(COMPETITOR_COLLECTION_RUNS_KEY, cleaned[:500])


def _public_collection_run(run: dict) -> dict:
    item = dict(run or {})
    status = str(item.get("status") or "").lower()
    if status == "queued" and "progress" not in item and not item.get("current_step"):
        item["status"] = "recorded"
        item["progress"] = 0
        item["current_step"] = "历史采集计划记录；请重新提交以执行后台采集。"
    return item


def _update_collection_run(run_id: str, **fields):
    with competitor_lock:
        runs = _load_collection_runs()
        for run in runs:
            if str(run.get("id") or "") == str(run_id):
                run.update(fields)
                run["updated_at"] = _utc_now()
                if fields.get("status") in {"succeeded", "failed", "partial"}:
                    run["completed_at"] = run["updated_at"]
                break
        _save_collection_runs(runs)


def _collection_config_for_category(category: str) -> dict:
    wanted = str(category or "").strip().lower()
    if not wanted:
        return {}
    for config in _load_competitor_configs():
        if str(config.get("category") or "").strip().lower() == wanted:
            return config
    return {}


def _resolve_collection_request(request: dict) -> tuple[dict, dict]:
    resolved = dict(request or {})
    config = _collection_config_for_category(resolved.get("category", ""))
    if config:
        if not str(resolved.get("target_market") or "").strip():
            resolved["target_market"] = config.get("target_market") or "北美 (US/CA)"
        if not _clean_list(resolved.get("brands") or [], limit=80):
            resolved["brands"] = _clean_list(config.get("brands") or [], limit=80)
        if not _clean_list(resolved.get("keywords") or [], limit=80):
            resolved["keywords"] = _clean_list(config.get("keywords") or [], limit=80)
        if not str(resolved.get("platform") or "").strip() and config.get("platforms"):
            resolved["platform"] = str((config.get("platforms") or [""])[0] or "")
    resolved["target_market"] = str(resolved.get("target_market") or "北美 (US/CA)").strip() or "北美 (US/CA)"
    resolved["brands"] = _clean_list(resolved.get("brands") or [], limit=80)
    resolved["keywords"] = _clean_list(resolved.get("keywords") or [], limit=80)
    return resolved, config


def _collection_result_summary(result: dict) -> dict:
    discovery = result.get("discovery") or {}
    discovered_items = discovery.get("asins") or discovery.get("video_ids") or discovery.get("assets") or []
    return {
        "requested_count": result.get("requested_asin_count") or result.get("requested_video_count") or 0,
        "fetched_count": result.get("fetched_count") or 0,
        "discovered_count": len(discovered_items),
        "upsert": result.get("upsert") or {},
        "errors": (result.get("errors") or [])[:10],
        "note": result.get("note") or "",
    }


def _run_competitor_collection_job(run_id: str):
    run = next((item for item in _load_collection_runs() if str(item.get("id") or "") == str(run_id)), None)
    if not run:
        return
    request, config = _resolve_collection_request(run.get("request") or {})
    platform_text = str(request.get("platform") or request.get("source") or "").strip()
    platform_key = platform_text.lower()
    if platform_key == "amazon":
        platform_key = "rainforest"
    _update_collection_run(
        run_id,
        status="running",
        progress=10,
        current_step="正在准备采集参数",
        resolved_request=request,
        config_applied=bool(config),
    )
    try:
        if platform_key in {"rainforest", "amazon"}:
            _update_collection_run(run_id, progress=25, current_step="正在通过 Rainforest 搜索并刷新 Amazon 素材")
            result = rainforest_refresh(
                RainforestRefreshRequest(
                    category=request.get("category") or "",
                    target_market=request.get("target_market") or "北美 (US/CA)",
                    amazon_domain=request.get("amazon_domain") or "",
                    brands=request.get("brands") or [],
                    keywords=request.get("keywords") or [],
                    asins=request.get("asins") or [],
                    use_discovery=True,
                )
            )
        elif platform_key == "youtube":
            _update_collection_run(run_id, progress=25, current_step="正在通过 YouTube API 发现并刷新素材")
            result = youtube_refresh(
                YouTubeRefreshRequest(
                    category=request.get("category") or "",
                    target_market=request.get("target_market") or "北美 (US/CA)",
                    brands=request.get("brands") or [],
                    keywords=request.get("keywords") or [],
                    video_ids=request.get("video_ids") or [],
                    use_discovery=True,
                )
            )
        else:
            raise ValueError("采集任务当前仅支持 Amazon/Rainforest 和 YouTube；TikTok、Instagram 等平台请在竞品素材库导入具体 URL。")
        summary = _collection_result_summary(result)
        status = "partial" if summary.get("errors") else "succeeded"
        _update_collection_run(
            run_id,
            status=status,
            progress=100,
            current_step="采集完成，已写入竞品素材库" if status == "succeeded" else "部分素材已入库，存在失败项",
            result_summary=summary,
            error_message="；".join(str(item.get("error") or item) for item in summary.get("errors", [])[:3]) if status == "partial" else "",
        )
    except HTTPException as exc:
        _update_collection_run(
            run_id,
            status="failed",
            progress=100,
            current_step="采集失败",
            error_message=str(exc.detail or exc),
        )
    except Exception as exc:
        _update_collection_run(
            run_id,
            status="failed",
            progress=100,
            current_step="采集失败",
            error_message=str(exc),
        )


def _update_competitor_research_job(job_id, **fields):
    with competitor_lock:
        jobs = _load_competitor_research_jobs()
        for job in jobs:
            if job.get("id") == job_id:
                job.update(fields)
                now = dt.datetime.utcnow().isoformat(timespec="seconds") + "Z"
                job["updated_at"] = now
                if fields.get("status") in {"succeeded", "failed"}:
                    job["completed_at"] = now
                break
        _save_competitor_research_jobs(jobs)


def _truncate_public_text(value, limit=260):
    text = str(value or "").strip()
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 1)].rstrip() + "…"


def _asset_first_image_url(asset: dict) -> str:
    media = asset.get("media") or []
    return (
        str(asset.get("image_url") or "")
        or str(next((item.get("thumbnail_url") or item.get("media_url") for item in media if isinstance(item, dict) and (item.get("thumbnail_url") or item.get("media_url"))), "") or "")
        or str(next((item.get("media_url") for item in media if isinstance(item, dict) and item.get("media_type") == "image"), "") or "")
    )


def _public_competitor_asset(asset, include_source_payload=False, compact=False):
    if compact:
        media = [item for item in (asset.get("media") or []) if isinstance(item, dict)]
        media_types = _asset_media_types(asset)
        metadata = asset.get("metadata") if isinstance(asset.get("metadata"), dict) else {}
        return {
            "id": asset.get("id") or "",
            "asin": asset.get("asin") or "",
            "platform": asset.get("platform") or "",
            "source_type": asset.get("source_type") or "",
            "source_url": asset.get("source_url") or "",
            "embed_url": asset.get("embed_url") or "",
            "image_url": _asset_first_image_url(asset),
            "brand": asset.get("brand") or "",
            "category": asset.get("category") or "",
            "title": asset.get("title") or "",
            "ai_analysis": _truncate_public_text(asset.get("ai_analysis") or "", 220),
            "ai_tags": _clean_list(asset.get("ai_tags") or [], limit=5),
            "quality_score": int(asset.get("quality_score") or 0),
            "review_status": asset.get("review_status") or "auto_collected",
            "rights_status": asset.get("rights_status") or "link_only_no_raw_video",
            "created_at": asset.get("created_at") or "",
            "updated_at": asset.get("updated_at") or "",
            "collected_at": asset.get("collected_at") or "",
            "media_count": len(media),
            "video_count": sum(1 for item in media if str(item.get("media_type") or "").lower() == "video"),
            "gif_count": sum(1 for item in media if str(item.get("media_type") or "").lower() == "gif") or (1 if "gif" in media_types else 0),
            "metadata": {
                "platform_content_id": metadata.get("platform_content_id") or "",
                "youtube_video_id": metadata.get("youtube_video_id") or "",
                "published_at": metadata.get("published_at") or "",
            },
        }
    public = dict(asset or {})
    if not include_source_payload:
        public.pop("source_payload", None)
    media = []
    for item in public.get("media", []) or []:
        public_media = dict(item or {})
        if not include_source_payload:
            public_media.pop("source_payload", None)
        media.append(public_media)
    public["media"] = media
    return public


def _competitor_asset_search_text(asset):
    media_text = " ".join(
        f"{item.get('title', '')} {item.get('media_type', '')}"
        for item in (asset.get("media") or [])
        if isinstance(item, dict)
    )
    metadata = asset.get("metadata") or {}
    return " ".join(
        [
            str(asset.get("id") or ""),
            str(asset.get("asin") or ""),
            str(asset.get("platform") or ""),
            str(asset.get("source_type") or ""),
            str(asset.get("channel") or ""),
            str(asset.get("brand") or ""),
            str(asset.get("category") or ""),
            str(asset.get("title") or ""),
            str(asset.get("original_copy") or "")[:800],
            str(asset.get("ai_analysis") or ""),
            " ".join(asset.get("ai_tags") or []),
            str(metadata.get("source_query") or ""),
            str(metadata.get("platform_content_id") or ""),
            str(metadata.get("account_name") or ""),
            str(metadata.get("youtube_video_id") or ""),
            media_text,
        ]
    ).lower()


def _search_competitor_assets(
    *,
    q: str = "",
    category: str = "",
    brand: str = "",
    platform: str = "",
    source: str = "",
    media_type: str = "",
    review_status: str = "",
    rights_status: str = "",
    date_from: str = "",
    date_to: str = "",
    min_quality: int = 0,
    sort: str = "quality_desc",
    offset: int = 0,
    limit: int = 20,
):
    q_lower = str(q or "").strip().lower()
    category_lower = _competitor_category_filter_value(category)
    brand_lower = str(brand or "").strip().lower()
    platform_lower = str(platform or "").strip().lower()
    source_lower = str(source or "").strip().lower()
    media_type_lower = str(media_type or "").strip().lower()
    review_lower = str(review_status or "").strip().lower()
    rights_lower = str(rights_status or "").strip().lower()
    date_from_value = _parse_datetime(date_from)
    date_to_value = _parse_datetime(date_to)
    min_quality_value = max(0, int(min_quality or 0))
    matched = []
    for asset in _load_competitor_assets():
        if _is_social_profile_asset(asset) and review_lower != "rejected":
            continue
        asset = _ensure_asset_admin_defaults(asset)
        if not review_lower and _asset_review_status(asset) == "rejected":
            continue
        search_text = ""
        if q_lower:
            search_text = _competitor_asset_search_text(asset)
        if q_lower and q_lower not in search_text:
            continue
        if category_lower:
            asset_category = str(asset.get("category") or "").lower()
            if category_lower not in asset_category:
                if not search_text:
                    search_text = _competitor_asset_search_text(asset)
                if category_lower not in search_text:
                    continue
        if brand_lower and brand_lower not in str(asset.get("brand") or "").lower():
            continue
        if platform_lower and platform_lower not in str(asset.get("platform") or "").lower():
            continue
        if source_lower and source_lower not in str(asset.get("source_type") or "").lower():
            continue
        if media_type_lower:
            if media_type_lower not in _asset_media_types(asset):
                continue
        if review_lower and review_lower != str(asset.get("review_status") or "").lower():
            continue
        if rights_lower and rights_lower != str(asset.get("rights_status") or "").lower():
            continue
        if min_quality_value and int(asset.get("quality_score") or 0) < min_quality_value:
            continue
        asset_time = _parse_datetime(asset.get("collected_at") or asset.get("updated_at") or asset.get("created_at"))
        if date_from_value and asset_time and asset_time < date_from_value:
            continue
        if date_to_value and asset_time and asset_time > date_to_value + dt.timedelta(days=1):
            continue
        matched.append(asset)
    if sort == "updated_asc":
        matched.sort(key=lambda item: str(item.get("updated_at") or item.get("collected_at") or ""))
    elif sort == "quality_asc":
        matched.sort(key=lambda item: int(item.get("quality_score") or 0))
    elif sort == "created_desc":
        matched.sort(key=lambda item: str(item.get("created_at") or item.get("collected_at") or ""), reverse=True)
    elif sort in {"newest", "updated_desc"}:
        matched.sort(
            key=lambda item: str(item.get("updated_at") or item.get("collected_at") or item.get("created_at") or ""),
            reverse=True,
        )
    else:
        matched.sort(
            key=lambda item: (
                str(item.get("updated_at") or item.get("collected_at") or item.get("created_at") or ""),
                int(item.get("quality_score") or 0),
            ),
            reverse=True,
        )
    total = len(matched)
    start = max(0, int(offset or 0))
    end = start + max(1, int(limit or 20))
    return matched[start:end], total


def _normalize_hotspot(payload: dict) -> dict:
    now = _utc_now()
    item = dict(payload or {})
    title = str(item.get("title") or "").strip()
    source_type = str(item.get("source_type") or "manual").strip()
    source_name = str(item.get("source_name") or item.get("source") or source_type).strip()
    item["id"] = str(
        item.get("id")
        or _stable_id(
            "hotspot",
            source_type,
            source_name,
            title,
            item.get("target_market", ""),
            item.get("category", ""),
            item.get("platform", ""),
        )
    )
    item["title"] = title
    item["source_type"] = source_type
    item["source_name"] = source_name
    item["source_url"] = str(item.get("source_url") or "").strip()
    item["category"] = str(item.get("category") or "").strip()
    item["target_market"] = str(item.get("target_market") or "").strip()
    item["platform"] = str(item.get("platform") or "").strip()
    item["heat_score"] = max(0, min(100, int(item.get("heat_score") or 0)))
    item["valid_from"] = str(item.get("valid_from") or dt.date.today().isoformat())[:10]
    item["valid_to"] = str(item.get("valid_to") or (dt.date.today() + dt.timedelta(days=45)).isoformat())[:10]
    item["status"] = str(item.get("status") or "active").strip()
    item["tags"] = _clean_list(item.get("tags") or [], limit=20)
    item["notes"] = str(item.get("notes") or "").strip()
    item["created_at"] = item.get("created_at") or now
    item["updated_at"] = item.get("updated_at") or now
    return item


def _hotspot_matches(item: dict, *, q: str = "", category: str = "", target_market: str = "", platform: str = "", status: str = "", active_only: bool = False) -> bool:
    text = " ".join(
        [
            str(item.get("title") or ""),
            str(item.get("source_name") or ""),
            str(item.get("source_type") or ""),
            str(item.get("notes") or ""),
            " ".join(item.get("tags") or []),
        ]
    ).lower()
    q_lower = str(q or "").strip().lower()
    if q_lower and q_lower not in text:
        return False
    if category:
        category_lower = category.lower()
        if category_lower not in str(item.get("category") or "").lower() and category_lower not in text:
            return False
    if target_market:
        market_lower = target_market.lower()
        item_market = str(item.get("target_market") or "").lower()
        if item_market and market_lower not in item_market and item_market not in market_lower:
            return False
    if platform:
        platform_lower = platform.lower()
        item_platform = str(item.get("platform") or "").lower()
        if item_platform and platform_lower not in item_platform and item_platform not in platform_lower:
            return False
    if status and str(item.get("status") or "").lower() != status.lower():
        return False
    if active_only:
        today = dt.date.today()
        valid_from = _parse_date(item.get("valid_from")) or today
        valid_to = _parse_date(item.get("valid_to")) or today
        if str(item.get("status") or "") != "active" or valid_from > today or valid_to < today:
            return False
    return True


def _search_hotspots(
    *,
    q: str = "",
    category: str = "",
    target_market: str = "",
    platform: str = "",
    source_type: str = "",
    status: str = "",
    active_only: bool = False,
    offset: int = 0,
    limit: int = 50,
):
    matched = []
    source_lower = str(source_type or "").strip().lower()
    for item in _load_hotspots():
        hotspot = _normalize_hotspot(item)
        if source_lower and source_lower != str(hotspot.get("source_type") or "").lower():
            continue
        if not _hotspot_matches(
            hotspot,
            q=q,
            category=category,
            target_market=target_market,
            platform=platform,
            status=status,
            active_only=active_only,
        ):
            continue
        matched.append(hotspot)
    matched.sort(
        key=lambda item: (
            str(item.get("status") or "") == "active",
            int(item.get("heat_score") or 0),
            str(item.get("updated_at") or ""),
        ),
        reverse=True,
    )
    total = len(matched)
    start = max(0, int(offset or 0))
    end = start + max(1, int(limit or 50))
    return matched[start:end], total


def _market_key(target_market: str) -> str:
    text = str(target_market or "").lower()
    if "北美" in text or "us" in text or "ca" in text:
        return "NA"
    if "欧洲" in text or "uk" in text or "de" in text or "fr" in text:
        return "EU"
    if "东南亚" in text or "sea" in text or "sg" in text:
        return "SEA"
    return "OTHER"


def _google_trends_geo(target_market: str) -> str:
    key = _market_key(target_market)
    if key == "EU":
        return "GB"
    if key == "SEA":
        return "SG"
    return "US"


def _nth_weekday_of_month(year: int, month: int, weekday: int, n: int) -> dt.date:
    first = dt.date(year, month, 1)
    shift = (weekday - first.weekday()) % 7
    return dt.date(year, month, 1 + shift + (n - 1) * 7)


def _calendar_nodes(target_market: str, from_date: dt.date | None = None, limit: int = 12):
    current = from_date or dt.date.today()
    year = current.year
    market = _market_key(target_market)
    candidates = [
        ("New Year", dt.date(year, 1, 1), ["节日节点", "全年营销"]),
        ("Valentine's Day", dt.date(year, 2, 14), ["节日节点", "礼赠场景"]),
        ("Back to School", dt.date(year, 8, 15), ["开学季", "家庭场景"]),
        ("Halloween", dt.date(year, 10, 31), ["节日节点", "派对场景"]),
        ("Singles' Day (11.11)", dt.date(year, 11, 11), ["大促节点", "电商节日"]),
        ("Double 12 (12.12)", dt.date(year, 12, 12), ["大促节点", "电商节日"]),
        ("Christmas", dt.date(year, 12, 25), ["节日节点", "家庭聚会"]),
    ]
    if market in {"NA", "EU"}:
        black_friday = _nth_weekday_of_month(year, 11, 3, 4)
        candidates.extend(
            [
                ("Black Friday", black_friday, ["大促节点", "折扣季"]),
                ("Cyber Monday", black_friday + dt.timedelta(days=3), ["大促节点", "线上购物"]),
            ]
        )
    if market == "NA":
        candidates.extend(
            [
                ("Mother's Day (US)", _nth_weekday_of_month(year, 5, 6, 2), ["节日节点", "礼赠场景"]),
                ("Father's Day (US)", _nth_weekday_of_month(year, 6, 6, 3), ["节日节点", "家庭场景"]),
            ]
        )
    if market == "EU":
        candidates.append(("Boxing Day (UK)", dt.date(year, 12, 26), ["大促节点", "英国市场"]))
    future = [(name, day, tags) for name, day, tags in candidates if day >= current]
    if not future:
        return _calendar_nodes(target_market, dt.date(year + 1, 1, 1), limit=limit)
    future.sort(key=lambda item: item[1])
    return future[:limit]


def _rss_items(url: str, timeout: int = 10):
    request = urllib.request.Request(
        url,
        headers={"Accept": "application/rss+xml, application/xml, text/xml", "User-Agent": "video-script-hotspot-admin/1.0"},
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        data = response.read()
    parser = ET.XMLParser()
    parser.entity = {}
    root = ET.fromstring(data, parser=parser)
    return root.findall(".//item")


def _fetch_google_trends_hotspots(source: dict, *, category: str = "", target_market: str = "", platform: str = ""):
    market = target_market or source.get("target_market") or "北美 (US/CA)"
    geo = _google_trends_geo(market)
    url = f"https://trends.google.com/trends/trendingsearches/daily/rss?geo={geo}"
    hotspots = []
    try:
        for item in _rss_items(url):
            title = (item.findtext("title") or "").strip()
            if not title:
                continue
            traffic = (item.findtext("{http://trends.google.com/trends/trendingsearches}approx_traffic") or "").strip()
            tags = ["Google Trends"]
            if traffic:
                tags.append(traffic)
            hotspots.append(
                _normalize_hotspot(
                    {
                        "title": title,
                        "source_type": "google_trends_rss",
                        "source_name": source.get("name") or "Google Trends",
                        "source_url": url,
                        "category": category or source.get("category", ""),
                        "target_market": market,
                        "platform": platform or source.get("platform", ""),
                        "heat_score": 78 if traffic else 68,
                        "valid_from": dt.date.today().isoformat(),
                        "valid_to": (dt.date.today() + dt.timedelta(days=7)).isoformat(),
                        "status": "active",
                        "tags": tags,
                        "notes": f"公开趋势 RSS 抓取；热度标记：{traffic}" if traffic else "公开趋势 RSS 抓取。",
                    }
                )
            )
    except Exception:
        fallback_titles = ["Before vs After", "Quick tutorial", "Time-saving hack", "Meal prep", "Family gathering"]
        hotspots.extend(
            _normalize_hotspot(
                {
                    "title": title,
                    "source_type": "google_trends_rss",
                    "source_name": source.get("name") or "Google Trends fallback",
                    "category": category or source.get("category", ""),
                    "target_market": market,
                    "platform": platform or source.get("platform", ""),
                    "heat_score": 45,
                    "valid_from": dt.date.today().isoformat(),
                    "valid_to": (dt.date.today() + dt.timedelta(days=14)).isoformat(),
                    "status": "active",
                    "tags": ["兜底候选"],
                    "notes": "Google Trends 抓取失败时的通用短视频热点候选。",
                }
            )
            for title in fallback_titles
        )
    return hotspots[:30]


def _fetch_rss_hotspots(source: dict, *, category: str = "", target_market: str = "", platform: str = ""):
    url = str(source.get("url") or "").strip()
    if not url:
        return []
    hotspots = []
    try:
        for item in _rss_items(url):
            title = (item.findtext("title") or "").strip()
            link = (item.findtext("link") or "").strip()
            if not title:
                continue
            hotspots.append(
                _normalize_hotspot(
                    {
                        "title": title,
                        "source_type": "rss",
                        "source_name": source.get("name") or "RSS",
                        "source_url": link or url,
                        "category": category or source.get("category", ""),
                        "target_market": target_market or source.get("target_market", ""),
                        "platform": platform or source.get("platform", ""),
                        "heat_score": 62,
                        "valid_from": dt.date.today().isoformat(),
                        "valid_to": (dt.date.today() + dt.timedelta(days=21)).isoformat(),
                        "status": "active",
                        "tags": ["RSS"],
                    }
                )
            )
    except Exception:
        return []
    return hotspots[:30]


def _fetch_calendar_hotspots(source: dict, *, category: str = "", target_market: str = "", platform: str = ""):
    market = target_market or source.get("target_market") or "北美 (US/CA)"
    result = []
    for name, day, tags in _calendar_nodes(market, limit=12):
        result.append(
            _normalize_hotspot(
                {
                    "title": name,
                    "source_type": "ecommerce_calendar",
                    "source_name": source.get("name") or "电商节日节点",
                    "category": category or source.get("category", ""),
                    "target_market": market,
                    "platform": platform or source.get("platform", ""),
                    "heat_score": 85 if "Black Friday" in name or "Cyber Monday" in name else 72,
                    "valid_from": max(dt.date.today(), day - dt.timedelta(days=45)).isoformat(),
                    "valid_to": (day + dt.timedelta(days=3)).isoformat(),
                    "status": "active",
                    "tags": tags,
                    "notes": f"建议提前围绕 {day.isoformat()} 节点规划内容。",
                }
            )
        )
    return result


def _upsert_hotspots(new_hotspots):
    now = _utc_now()
    with competitor_lock:
        existing = {_normalize_hotspot(item)["id"]: _normalize_hotspot(item) for item in _load_hotspots()}
        inserted = 0
        updated = 0
        for raw in new_hotspots:
            item = _normalize_hotspot(raw)
            found = existing.get(item["id"])
            if found:
                merged = {**found, **item, "created_at": found.get("created_at") or item.get("created_at"), "updated_at": now}
                existing[item["id"]] = _normalize_hotspot(merged)
                updated += 1
            else:
                item["created_at"] = item.get("created_at") or now
                item["updated_at"] = now
                existing[item["id"]] = item
                inserted += 1
        _save_hotspots(list(existing.values()))
    return {"inserted": inserted, "updated": updated, "total": inserted + updated}


def _refresh_hotspots(req: HotspotRefreshRequest):
    selected_ids = {str(item) for item in req.source_ids or []}
    sources = [
        source
        for source in _load_hotspot_sources()
        if source.get("enabled", True) and (not selected_ids or str(source.get("id")) in selected_ids)
    ]
    generated = []
    errors = []
    for source in sources:
        source_type = str(source.get("source_type") or "manual")
        try:
            if source_type == "google_trends_rss":
                generated.extend(_fetch_google_trends_hotspots(source, category=req.category, target_market=req.target_market, platform=req.platform))
            elif source_type == "ecommerce_calendar":
                generated.extend(_fetch_calendar_hotspots(source, category=req.category, target_market=req.target_market, platform=req.platform))
            elif source_type == "rss":
                generated.extend(_fetch_rss_hotspots(source, category=req.category, target_market=req.target_market, platform=req.platform))
        except Exception as exc:
            errors.append({"source_id": source.get("id"), "error": str(exc)})
    upsert = _upsert_hotspots(generated) if generated else {"inserted": 0, "updated": 0, "total": 0}
    return {"sources": sources, "hotspots": generated, "upsert": upsert, "errors": errors[:20]}


def _update_job(job_id, **fields):
    with job_lock:
        jobs = _load_jobs()
        for job in jobs:
            if job.get("id") == job_id:
                job.update(fields)
                now = dt.datetime.utcnow().isoformat(timespec="seconds") + "Z"
                job["updated_at"] = now
                if fields.get("status") in {"succeeded", "failed"}:
                    job["completed_at"] = now
                break
        _save_jobs(jobs)


def _feature_rows(df: pd.DataFrame, model: str, selected_features: list[str]) -> list[dict]:
    rows = df[df["model"].astype(str) == str(model)] if not df.empty else pd.DataFrame()
    if selected_features:
        selected = {str(x).strip() for x in selected_features if str(x).strip()}
        rows = rows[rows["Feature Name"].astype(str).isin(selected)]
    if rows.empty:
        rows = df[df["model"].astype(str) == str(model)].head(5)
    payload = []
    for _, row in rows.head(10).iterrows():
        payload.append(
            {
                "name": str(row.get("Feature Name", "") or ""),
                "tagline": str(row.get("Tagline", "") or ""),
                "description": str(row.get("Feature Description", "") or ""),
            }
        )
    return payload


def _clean_prompt_value(value) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip())


def _feature_catalog_lines(features: list[dict]) -> str:
    lines = []
    for index, item in enumerate(features[:10], start=1):
        name = _clean_prompt_value(item.get("name"))
        tagline = _clean_prompt_value(item.get("tagline"))
        description = _clean_prompt_value(item.get("description"))
        lines.append(
            "\n".join(
                [
                    f"{index}. Feature Name（功能卖点（英文）列必须逐字使用）：{name}",
                    f"   Tagline（字幕列优先逐字引用）：{tagline or '无'}",
                    f"   Feature Description（旁白/画面示意&表现手法/字幕必须吸收其专业表述）：{description or '无'}",
                ]
            )
        )
    return "\n".join(lines)


def _request_text_blob(req: GenerateRequest, features: list[dict]) -> str:
    parts = [
        req.category,
        req.model,
        req.custom_requirements,
        req.pain_points,
        req.video_usage,
        req.project_type,
    ]
    for item in features or []:
        parts.extend([item.get("name"), item.get("tagline"), item.get("description")])
    return " ".join(str(part or "") for part in parts)


def _is_microwave_request(req: GenerateRequest, features: list[dict]) -> bool:
    raw = _request_text_blob(req, features).lower()
    return any(token in raw for token in ("microwave", "微波", "flatbed", "wave stirrer", "preset menus"))


def _is_cooking_appliance_request(req: GenerateRequest, features: list[dict]) -> bool:
    raw = _request_text_blob(req, features).lower()
    return any(
        token in raw
        for token in (
            "air fryer",
            "airfryer",
            "空气炸",
            "microwave",
            "微波",
            "oven",
            "烤箱",
            "grill",
            "preset menus",
            "menu",
            "cooking",
            "cook",
            "加热",
            "烹饪",
            "预设菜单",
        )
    )


def _segment_examples_for_request(req: GenerateRequest | None, features: list[dict] | None = None) -> tuple[str, str]:
    raw = _request_text_blob(req, features or []).lower() if req else ""
    if any(token in raw for token in ("microwave", "微波", "flatbed", "wave stirrer", "preset menus")):
        return (
            "“冰箱取出冷饭盒｜复热痛点开场”“整盘餐食直接放上平板腔体｜Flatbed 空间证据”“长方形餐盒不用转盘也能放平｜Flatbed 尺寸证据”“取出冒热气意面/汤碗｜均匀加热结果验证”",
            "冷饭盒、汤碗、咖啡杯、爆米花袋、早餐燕麦杯、长方形便当盒、整盘晚餐、微波炉门体/腔体/控制面板/蒸汽/热气",
        )
    if any(token in raw for token in ("dishwasher", "洗碗", "dish washer")):
        return (
            "“晚餐后水槽堆满｜痛点开场”“半篮餐具入仓｜Half Load 证据”“开门取出干燥杯碗｜结果验证”",
            "晚餐盘、咖啡杯、玻璃杯、餐具篮、锅具、水渍、干燥杯碗、洗碗机门体/内腔/喷淋臂",
        )
    if any(token in raw for token in ("laundry", "washer", "washing", "洗衣", "洗烘")):
        return (
            "“运动衣物堆在篮中｜换洗痛点开场”“把衬衫和毛巾放入滚筒｜容量/程序证据”“取出蓬松衣物｜洁净结果验证”",
            "运动衣、毛巾、衬衫、洗衣篮、滚筒内筒、控制面板、衣物纹理、洗后蓬松状态",
        )
    if any(token in raw for token in ("refrigerator", "fridge", "freezer", "冰箱", "冷藏", "冷冻")):
        return (
            "“周末采购袋放上台面｜囤货痛点开场”“不同食材分区入仓｜空间/保鲜证据”“开门看到整齐食材｜结果验证”",
            "蔬果盒、牛奶、饮料瓶、保鲜盒、冷冻抽屉、门架、层板、冰箱内灯、食材新鲜状态",
        )
    return (
        "“生活任务出现｜痛点开场”“产品介入处理物品｜核心卖点证据”“前后状态对比｜结果验证”",
        "台面道具、被处理物品、产品局部、操作面板、使用前后状态、成品质感特写",
    )


def _percentage_range_from_text(text: str) -> tuple[float, float] | None:
    match = re.search(r"(\d{1,3})\s*(?:-|~|～|—|–|至|到)\s*(\d{1,3})\s*%", str(text or ""))
    if not match:
        return None
    low = max(0, min(100, int(match.group(1)))) / 100
    high = max(0, min(100, int(match.group(2)))) / 100
    if low > high:
        low, high = high, low
    return low, high


def _feature_focus_targets(req: GenerateRequest, features: list[dict]) -> list[dict]:
    custom = str(req.custom_requirements or "")
    custom_lower = custom.lower()
    percent = _percentage_range_from_text(custom)
    targets = []
    for item in features or []:
        name = _clean_prompt_value(item.get("name"))
        tagline = _clean_prompt_value(item.get("tagline"))
        description = _clean_prompt_value(item.get("description"))
        haystack = f"{name} {tagline} {description}".lower()
        is_flatbed = "flatbed" in haystack or "平板" in haystack
        mentioned = bool(name and name.lower() in custom_lower) or (is_flatbed and ("flatbed" in custom_lower or "平板" in custom_lower))
        if not mentioned:
            continue
        low, high = percent or (0.35, 0.50)
        aliases = [name, tagline]
        if is_flatbed:
            aliases.extend(["Flatbed", "平板", "无转盘", "turntable-free", "extra-large cooking space"])
        targets.append(
            {
                "name": name,
                "tagline": tagline,
                "description": description,
                "low": low,
                "high": high,
                "aliases": [alias for alias in aliases if alias],
            }
        )
    return targets


def _script_quality_guidance(req: GenerateRequest, features: list[dict]) -> str:
    lines = [
        "产品与卖点质量硬要求：",
        "- 功能卖点（英文）列必须优先逐字使用下方 Feature Name，不要改写成泛泛的“痛点开场/功能展示”。没有直接功能卖点的开场/收尾行，该列留空或写真实产品/场景短语，严禁写 Pain-point opening、Brand closing、Opening、Closing 等结构标签。",
        "- 字幕-显示卖点名及描述（英文）列必须使用“Feature Name: Tagline”或从 Feature Description 摘取原文专业短句；不得写成 generic benefit。",
        "- 旁白和字幕是成片文案，严禁出现 Pain-point opening、Brand closing、Opening、Closing、Hook、Intro、Outro 等制作结构标签，也不要写成“开头：/结尾：/字幕：/卖点：”这种字段标签。",
        "- 旁白（英文）可以更口语，但必须围绕卖点库里的专业词：Feature Name、Tagline、Feature Description 至少命中其一。",
        "- 画面示意&表现手法必须把 Feature Description 转成可拍动作，不允许只写“展示平板设计/展示均匀加热效果”这种抽象描述。",
        "- 镜头运动&运动轨迹必须写清运动方式和方向/主体关系，例如近景特写（CU）｜推进至面板、横移跟拍（左→右）、俯拍切到腔体、缓慢拉远；不能只写“俯拍/特写/全景”。",
        "- 每套必须至少出现 1 个有设计感的转场或镜头连接：动作匹配、遮挡转场、物体擦镜、声桥、前后对比切、推近接特写、俯拍切细节等任选。",
        "- 每套必须至少出现 1 个感官/状态细节：蒸汽、水汽、声音、纹理、屏幕数字反馈、食物质地、地面污渍变化、衣物/餐具状态变化等任选。",
        "- 不要使用模板化分段名堆叠，例如“产品切入/功能展示/功能展示2/清洁效果展示1/清洁效果展示2”；要写成具体生活任务和画面动作。",
    ]
    if _is_microwave_request(req, features):
        lines.extend(
            [
                "- 当前产品按微波炉处理：开头必须是微波炉使用任务，例如冷饭/汤碗/便当盒/爆米花/早餐燕麦/咖啡加热；严禁使用洗碗机式的“水槽堆满餐具、洗碗、dirty dishes、dishwasher”开场。",
                "- 微波炉镜头必须围绕微波炉门体、腔体、平板底部、控制面板、食物/餐盒入炉、蒸汽和加热后状态；餐具只能作为承载食物的盘碗，不能变成待清洗对象。",
            ]
        )
    if _is_cooking_appliance_request(req, features):
        lines.extend(
            [
                "- 厨房烹饪类脚本必须遵守真实使用时序：准备食材/放入产品 → 选择程序或预设菜单/启动 → 烹饪或加热过程 → 取出成品/口感结果 → 品牌收尾。",
                "- 严禁出现“食物已经做熟/成品展示/结果验证”之后才去按预设菜单、选择程序、启动机器或展示控制面板的倒置逻辑；菜单/按键必须发生在烹饪结果之前。",
                "- 如果展示 Preset Menus、Air Fry、Dual Heating 等操作型卖点，必须让该卖点在食物入炉后、成品出炉前承担因果作用，而不是成品之后再补拍面板。",
            ]
        )
    for target in _feature_focus_targets(req, features):
        lines.append(
            f"- 用户点名强调 {target['name']}：必须用总时长的 {round(target['low'] * 100)}%-{round(target['high'] * 100)}% 展开该卖点；"
            f"至少 3 个正文镜头或至少 {round(int(req.expected_duration) * target['low'])} 秒直接围绕它，且镜头分段/功能卖点（英文）/字幕中反复出现其专业表达。"
        )
        if "flatbed" in " ".join(target.get("aliases", [])).lower() or "平板" in " ".join(target.get("aliases", [])):
            lines.append(
                "- Flatbed 证明镜头必须具体：整盘晚餐/长方形餐盒/宽碗直接放在平板腔体上、无需转盘避让、腔体空间俯拍、门体打开后取出热气食物。"
            )
    return "\n".join(lines)


def _strip_code_fences(text: str) -> str:
    cleaned = str(text or "").strip()
    cleaned = re.sub(r"^\s*```(?:markdown|md)?\s*", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\s*```\s*$", "", cleaned)
    return cleaned.strip()


def _strip_overall_video_prompt_sections(text: str) -> str:
    return re.sub(
        r"\n*\s*(?:整体AI视频生成Prompt|整体 AI 视频生成 Prompt|Overall AI Video Generation Prompt)\s*(?:（English）|\(English\))?\s*[:：][\s\S]*$",
        "",
        str(text or ""),
        flags=re.IGNORECASE,
    ).strip()


def _has_expected_table(content: str) -> bool:
    text = str(content or "")
    return TABLE_HEADER_LINE in text and TABLE_SEPARATOR_LINE in text and "总时长" in text


def _duration_structure_profile(expected_duration) -> tuple[int, int, int]:
    try:
        expected = int(float(expected_duration))
    except (TypeError, ValueError):
        expected = 30
    expected = max(6, min(90, expected))
    if expected <= 12:
        min_segments = 3
        max_segment_seconds = 6
    elif expected <= 20:
        min_segments = 4
        max_segment_seconds = 7
    elif expected <= 30:
        min_segments = 5
        max_segment_seconds = 8
    elif expected <= 45:
        min_segments = 5
        max_segment_seconds = 10
    else:
        min_segments = 6
        max_segment_seconds = 15
    return expected, min_segments, max_segment_seconds


def _duration_structure_guidance(expected_duration, req: GenerateRequest | None = None, features: list[dict] | None = None) -> str:
    expected, min_segments, max_segment_seconds = _duration_structure_profile(expected_duration)
    min_lifestyle_details = max(3, min_segments - 3)
    segment_examples, detail_examples = _segment_examples_for_request(req, features or [])
    return f"""分段密度硬要求：
- 正文镜头行（不含表头、分隔行和“总时长”行）必须控制在 {min_segments}-{MAX_SCRIPT_SEGMENTS} 行，绝对不得超过 {MAX_SCRIPT_SEGMENTS} 行；所有行时长相加必须精确等于 {expected} 秒，“总时长”行也必须写 {expected}秒。
- 单行时长以 4-8 秒为主，最长不超过 {max_segment_seconds} 秒；除 12 秒以内的短视频外，不要生成 1-2 秒碎片段。
- “镜头分段”必须从 0 秒开始连续推进，例如“0-3s 冷饭盒拿出（复热痛点开场）”“3-6s 餐盒放入平板腔体（Flatbed 证据）”；每行时间段必须与“时长”列一致，不能重叠、跳秒或只写镜头编号。
- “镜头分段”不能只写“功能展示1/功能展示2/产品切入/收尾”，必须写成“时间段 + 生活场景任务 + 括号内阶段/卖点证据”的具体名称。适合当前产品的示例：{segment_examples}。
- 结构顺序用最多 {MAX_SCRIPT_SEGMENTS} 段合并覆盖：生活化痛点开场、环境/物品状态铺垫、产品切入、核心功能操作、卖点证据特写、结果验证、品牌收尾；不要为了覆盖环节拆成很多 2 秒短段。
- 每套至少出现 {min_lifestyle_details} 个生活化物品/场景细节（优先从这些当前品类细节中选择：{detail_examples}），人物只允许手部、手臂、背影、越肩视角或生活痕迹。"""


def _duration_seconds(cell) -> int:
    match = re.search(r"\d{1,3}", str(cell or ""))
    return int(match.group(0)) if match else 0


def _script_body_rows(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty or "镜头分段" not in df.columns:
        return df.iloc[0:0]
    mask = ~df.apply(lambda row: any("总时长" in str(value) or "total" in str(value).lower() for value in row), axis=1)
    return df[mask]


def _has_rich_duration_structure(content: str, expected_duration) -> bool:
    table_lines, _ = _extract_first_md_table(content)
    df = _parse_md_table_to_df(table_lines)
    if df.empty or "时长" not in df.columns:
        return False
    expected, min_segments, max_segment_seconds = _duration_structure_profile(expected_duration)
    body = _script_body_rows(df)
    if len(body) < min_segments:
        return False
    if len(body) > MAX_SCRIPT_SEGMENTS:
        return False
    durations = [_duration_seconds(value) for value in body["时长"].tolist()]
    if any(value <= 0 for value in durations):
        return False
    if expected > 12 and sum(1 for value in durations if value <= 2) > 0:
        return False
    if any(value > max_segment_seconds for value in durations):
        return False
    body_total = sum(durations)
    total_rows = df.drop(body.index, errors="ignore")
    total_seconds = 0
    if not total_rows.empty and "时长" in total_rows.columns:
        total_seconds = _duration_seconds(total_rows.iloc[-1].get("时长", ""))
    return body_total == expected and (total_seconds == expected or total_seconds == 0)


def _content_contains_any(text: str, terms: list[str]) -> bool:
    lower = str(text or "").lower()
    return any(str(term or "").lower() in lower for term in terms if str(term or "").strip())


def _row_text(row) -> str:
    if hasattr(row, "to_dict"):
        values = row.to_dict().values()
    else:
        values = row
    return " ".join(str(value or "") for value in values)


def _script_quality_issues(content: str, req: GenerateRequest, features: list[dict], script_direction: str = "") -> list[str]:
    issues = []
    table_lines, _ = _extract_first_md_table(content)
    df = _parse_md_table_to_df(table_lines)
    if df.empty:
        return ["缺少可解析的 Markdown 表格，无法检查品类与卖点质量。"]
    body = _script_body_rows(df)
    full_text = "\n".join(table_lines)
    expected, _, _ = _duration_structure_profile(req.expected_duration)

    agency_columns = [
        "镜头分段",
        "功能卖点（英文）",
        "画面示意&表现手法",
        "旁白（英文）",
        "字幕-显示卖点名及描述（英文）",
        "镜头运动&运动轨迹",
    ]
    if not body.empty:
        banned_label_pattern = re.compile(
            r"\b(?:pain[-\s]?point\s+opening|brand\s+closing)\b|^(?:opening|closing|hook|intro|outro)\b\s*[:：-]?|^(?:开头|结尾|字幕|卖点)\s*[:：]",
            flags=re.IGNORECASE,
        )
        label_leaks = 0
        for _, row in body.iterrows():
            if any(banned_label_pattern.search(str(value or "").strip()) for value in row.tolist()):
                label_leaks += 1
        if label_leaks:
            issues.append(
                f"有 {label_leaks} 行泄漏了制作结构标签（如 Pain-point opening/Brand closing/Opening/Closing），这些不能出现在字幕、旁白或卖点文案里。"
            )
        if len(body) > MAX_SCRIPT_SEGMENTS:
            issues.append(f"分段过碎：正文镜头共有 {len(body)} 行，必须合并为不超过 {MAX_SCRIPT_SEGMENTS} 段。")
        short_segments = sum(1 for value in body["时长"].tolist() if _duration_seconds(value) <= 2)
        if short_segments and expected > 12:
            issues.append(f"短碎片段过多：有 {short_segments} 行时长仅 1-2 秒，需合并为 4-8 秒左右的完整镜头段。")
        for column in agency_columns:
            blanks = sum(1 for value in body[column].tolist() if not str(value or "").strip())
            if blanks:
                issues.append(f"机构稿字段不完整：{column} 有 {blanks} 行为空，必须逐镜头填写。")
        generic_pattern = re.compile(r"^(展示|突出|呈现)?\s*(产品|功能|卖点|效果)?\s*(特写|展示|介绍)?$", flags=re.IGNORECASE)
        generic_terms = re.compile(r"展示产品功能|展示功能|突出卖点|产品特写|功能展示|卖点展示")
        generic_rows = 0
        for value in body["画面示意&表现手法"].tolist():
            text = str(value or "").strip()
            if not text or generic_pattern.search(text) or generic_terms.search(text):
                generic_rows += 1
        if generic_rows:
            issues.append(f"画面示意&表现手法仍偏泛泛描述，有 {generic_rows} 行需要改成具体可拍画面/故事推进。")
        body_text = "\n".join(_row_text(row) for _, row in body.iterrows())
        transition_pattern = re.compile(
            r"转场|匹配|遮挡|擦镜|声桥|对比切|快切|拉焦|推近接|切到|切细节|wipe|match\s*cut|sound\s*bridge|whip|rack\s*focus",
            flags=re.IGNORECASE,
        )
        sensory_pattern = re.compile(
            r"蒸汽|水汽|雾气|声音|咔哒|滴答|嗡鸣|纹理|质地|拉丝|冒气|屏幕|数字|灯光|反光|污渍|毛发|碎屑|状态变化|before|after|steam|texture|sizzle|beep|display",
            flags=re.IGNORECASE,
        )
        if not transition_pattern.search(body_text):
            issues.append("缺少有设计感的转场或镜头连接，需加入动作匹配、遮挡转场、声桥、前后对比切或推近接特写。")
        if not sensory_pattern.search(body_text):
            issues.append("缺少感官/状态细节，需加入蒸汽、水汽、声音、纹理、屏幕反馈、污渍/食材/物品状态变化等可见证据。")
        if _is_cooking_appliance_request(req, features):
            result_row_index = None
            operation_after_result = False
            result_pattern = re.compile(
                r"成品|做完|完成|出炉|取出|上桌|摆盘|结果|熟透|焦脆|金黄|酥脆|完美加热|cooked|finished|ready|serve|served|plated|crispy|golden|result",
                flags=re.IGNORECASE,
            )
            operation_pattern = re.compile(
                r"预设|菜单|程序|模式|启动|开始|按下|选择|控制面板|屏幕|数字跳动|preset|menu|programme|program|mode|start|press|select|control\s*panel|display",
                flags=re.IGNORECASE,
            )
            for row_index, row in body.iterrows():
                text = _row_text(row)
                if result_row_index is None and result_pattern.search(text):
                    result_row_index = row_index
                    continue
                if result_row_index is not None and operation_pattern.search(text):
                    operation_after_result = True
                    break
            if operation_after_result:
                issues.append(
                    "烹饪时序倒置：已经出现成品/做熟/取出结果之后又去选择预设菜单、启动或展示控制面板；必须改为食材入炉后先操作菜单/启动，再展示烹饪过程和成品结果。"
                )
        weak_motion = 0
        for value in body["镜头运动&运动轨迹"].tolist():
            text = str(value or "")
            if not re.search(
                r"推|拉|横移|跟拍|固定|俯拍|摇|移|切|靠近|远离|左|右|上|下|zoom|pan|tilt|track|dolly|fixed|push|pull|slide|move|cut",
                text,
                flags=re.IGNORECASE,
            ):
                weak_motion += 1
        if weak_motion:
            issues.append(f"镜头运动&运动轨迹有 {weak_motion} 行缺少运动路径或方向，需按机构稿写成可执行轨迹。")
        first_timecode = str(body.iloc[0].get("镜头分段", "") or "")
        if not re.search(r"0\s*[-~—–至到]\s*\d+|0\s*s|0\s*秒", first_timecode, flags=re.IGNORECASE):
            issues.append("镜头分段未从 0 秒开始连续标注，例如“0-3s 冷饭盒拿出（复热痛点开场）”。")

    for item in (features or [])[:5]:
        name = _clean_prompt_value(item.get("name"))
        tagline = _clean_prompt_value(item.get("tagline"))
        description = _clean_prompt_value(item.get("description"))
        if name and name.lower() not in full_text.lower():
            issues.append(f"未逐字使用卖点库 Feature Name：{name}")
        if tagline and tagline.lower() not in full_text.lower():
            issues.append(f"未复用卖点库 Tagline：{name}: {tagline}")
        elif not tagline and description:
            words = [word for word in re.split(r"[^A-Za-z0-9-]+", description) if len(word) >= 5][:4]
            if words and not any(word.lower() in full_text.lower() for word in words):
                issues.append(f"未吸收卖点库 Feature Description 的专业表达：{name}")

    if _is_microwave_request(req, features) and not body.empty:
        first_rows_text = " ".join(_row_text(row) for _, row in body.head(2).iterrows())
        bad_opening = re.search(
            r"水槽|洗碗|碗碟堆|餐具堆|dirty\s+dish|dirty\s+dishes|sink|dishwasher|dish\s+rack|detergent",
            first_rows_text,
            flags=re.IGNORECASE,
        )
        if bad_opening:
            issues.append("微波炉脚本开头误用了洗碗机/水槽/脏餐具场景。")
        microwave_opening_terms = [
            "微波",
            "加热",
            "复热",
            "冷饭",
            "剩饭",
            "食材",
            "食物",
            "餐盒",
            "便当",
            "汤",
            "咖啡",
            "爆米花",
            "燕麦",
            "flatbed",
            "microwave",
            "leftover",
            "reheat",
            "frozen",
            "oatmeal",
            "popcorn",
            "mug",
        ]
        if not _content_contains_any(first_rows_text, microwave_opening_terms):
            issues.append("微波炉前两镜没有建立微波炉使用任务或食物加热场景。")

    direction = script_direction or (_selected_script_directions(req)[0] if len(_selected_script_directions(req)) == 1 else "")
    if direction == "开箱体验型" and not body.empty:
        openbox_terms = [
            "开箱",
            "拆箱",
            "包装",
            "外箱",
            "纸箱",
            "封条",
            "胶带",
            "取出",
            "揭膜",
            "保护膜",
            "泡沫",
            "配件",
            "说明书",
            "摆放",
            "安装",
            "首次",
            "第一次",
            "unbox",
            "unboxing",
            "package",
            "packaging",
            "first use",
            "setup",
        ]
        first_rows_text = " ".join(_row_text(row) for _, row in body.head(3).iterrows())
        if not _content_contains_any(full_text, openbox_terms):
            issues.append("已选择“开箱体验型”，但脚本缺少包装、拆箱、取出、揭膜、摆放安装或首次上手等开箱主线。")
        elif not _content_contains_any(first_rows_text, openbox_terms[:14]):
            issues.append("已选择“开箱体验型”，但前几镜没有建立包装、拆箱或取出产品的开箱开场。")

    for target in _feature_focus_targets(req, features):
        aliases = target.get("aliases") or [target.get("name", "")]
        focus_seconds = 0
        focus_rows = 0
        for _, row in body.iterrows():
            text = _row_text(row)
            if _content_contains_any(text, aliases):
                focus_rows += 1
                focus_seconds += _duration_seconds(row.get("时长", ""))
        min_seconds = max(1, round(expected * float(target.get("low") or 0)))
        min_rows = 3 if expected >= 24 else 2
        if focus_seconds < min_seconds or focus_rows < min_rows:
            issues.append(
                f"{target.get('name')} 篇幅不足：当前约 {focus_seconds} 秒/{focus_rows} 行，需至少 {min_seconds} 秒且 {min_rows} 行。"
            )

    return issues[:8]


def _extract_first_md_table(text: str):
    if not text:
        return [], ""
    lines = str(text).splitlines()
    table = []
    started = False
    end_idx = None
    for idx, line in enumerate(lines):
        stripped = line.strip()
        if not started:
            if stripped.startswith("|") and stripped.count("|") >= 2:
                started = True
                table.append(line)
            continue
        if stripped.startswith("|") and stripped.count("|") >= 2:
            table.append(line)
        else:
            end_idx = idx
            break
    remainder = "\n".join(lines[end_idx:]).strip() if end_idx is not None else ""
    return table, remainder


def _parse_md_table_to_df(table_lines):
    if not table_lines or len(table_lines) < 2:
        return pd.DataFrame(columns=TABLE_COLUMNS)
    rows = []
    for line in table_lines:
        stripped = line.strip().strip("|")
        cells = [cell.strip() for cell in stripped.split("|")]
        rows.append(cells)
    header = rows[0]
    body = rows[2:] if len(rows) >= 3 else []
    normalized = []
    for row in body:
        if len(row) < len(header):
            row = row + [""] * (len(header) - len(row))
        if len(row) > len(header):
            row = row[: len(header)]
        normalized.append(row)
    df = pd.DataFrame(normalized, columns=header)
    row_count = len(df)

    def _series(column: str) -> pd.Series:
        if column in df.columns:
            return df[column].fillna("").astype(str)
        return pd.Series([""] * row_count, index=df.index, dtype=str)

    def _combine_columns(*columns: str) -> pd.Series:
        source = [_series(column) for column in columns]
        values = []
        for parts in zip(*source):
            values.append("；".join(part.strip() for part in parts if part and part.strip()))
        return pd.Series(values, index=df.index, dtype=str)

    if "镜头分段" not in df.columns:
        if "镜号/时间码" in df.columns or "结构分段" in df.columns:
            timecodes = _series("镜号/时间码")
            segments = _series("结构分段")
            values = []
            for index, (timecode, segment) in enumerate(zip(timecodes.tolist(), segments.tolist())):
                raw = f"{timecode} {segment}".strip()
                if "总时长" in raw or "total" in raw.lower():
                    values.append("总时长")
                    continue
                time_part = re.sub(r"^镜头\s*\d+\s*[｜|:：-]*\s*", "", timecode or "").strip()
                if not time_part:
                    time_part = f"镜头{index + 1:02d}"
                values.append(f"{time_part} {segment}".strip())
            df["镜头分段"] = values
        elif "结构分段" in df.columns:
            df["镜头分段"] = df["结构分段"]

    if "功能卖点（英文）" not in df.columns:
        df["功能卖点（英文）"] = _combine_columns("功能点", "对应卖点").str.strip("；")

    if "画面示意&表现手法" not in df.columns:
        if any(column in df.columns for column in ["分镜画面（AI示意图口令）", "画面说明", "故事情节解说"]):
            df["画面示意&表现手法"] = _combine_columns("分镜画面（AI示意图口令）", "画面说明", "故事情节解说")
        elif "表现手法" in df.columns:
            df["画面示意&表现手法"] = df["表现手法"]

    if "镜头运动&运动轨迹" not in df.columns:
        if "镜头运动轨迹" in df.columns:
            df["镜头运动&运动轨迹"] = df["镜头运动轨迹"]
        else:
            df["镜头运动&运动轨迹"] = _combine_columns("拍摄角度", "运镜方式").str.strip("；")

    for column in TABLE_COLUMNS:
        if column not in df.columns:
            df[column] = ""
    return df[TABLE_COLUMNS]


SCRIPT_DIRECTIONS = (
    "问题解决/痛点挖掘型",
    "产品展示/功能介绍型",
    "开箱体验型",
    "场景化/生活方式型",
    "测评/对比型",
)

SCRIPT_DIRECTION_ALIASES = {
    "偏痛点转化": "问题解决/痛点挖掘型",
    "痛点转化": "问题解决/痛点挖掘型",
    "痛点挖掘": "问题解决/痛点挖掘型",
    "功能展示": "产品展示/功能介绍型",
    "产品介绍": "产品展示/功能介绍型",
    "开箱": "开箱体验型",
    "开箱体验": "开箱体验型",
    "场景化种草": "场景化/生活方式型",
    "生活方式": "场景化/生活方式型",
    "测评对比": "测评/对比型",
}


def _normalize_script_direction(value: str) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    if raw in SCRIPT_DIRECTIONS:
        return raw
    compact = re.sub(r"[\s/｜|、（）()_-]+", "", raw)
    for direction in SCRIPT_DIRECTIONS:
        if compact == re.sub(r"[\s/｜|、（）()_-]+", "", direction):
            return direction
    return SCRIPT_DIRECTION_ALIASES.get(raw) or SCRIPT_DIRECTION_ALIASES.get(compact, "")


def _selected_script_directions(req: GenerateRequest) -> list[str]:
    directions = []
    seen = set()
    for item in req.video_type or []:
        direction = _normalize_script_direction(item)
        if direction and direction not in seen:
            directions.append(direction)
            seen.add(direction)
    return directions or ["场景化/生活方式型"]


def _script_direction_for_variant(req: GenerateRequest, variant_index: int) -> str:
    directions = _selected_script_directions(req)
    return directions[variant_index % len(directions)]


def _direction_specific_guidance(direction: str) -> str:
    if direction == "开箱体验型":
        return """
脚本方向执行要求（开箱体验型）：
- 本方案必须围绕“开箱体验”展开：外包装/拆箱动作/配件或内胆初见/摆放安装/首次使用/初次效果反馈形成完整链路。
- 每个主要镜头都要有开箱或首次上手痕迹，例如包装封条、手部拆箱、取出产品、揭膜、检查细节、摆上台面、第一次放入食材或物品。
- 不得把整体结构写成痛点转化、场景化种草或单纯功能罗列；可以出现使用前期待和初次体验反馈，但必须服务开箱主线。
- 方案定位、方案标签、建议视频类型如需出现，只能逐字使用“开箱体验型”，不得改写为“偏痛点转化”“痛点转化”“场景化种草”“功能展示”等自造名称。
""".strip()
    return f"""
脚本方向执行要求：
- 本方案必须且只能执行“{direction}”，不得切换为其他脚本方向。
- 方案定位、方案标签、建议视频类型如需出现，只能逐字使用“{direction}”，不得改写为“偏痛点转化”“痛点转化”“场景化种草”“功能展示”等自造名称。
""".strip()


def _assets_by_ids(asset_ids: list[str]) -> list[dict]:
    wanted = [str(item) for item in asset_ids or [] if str(item or "").strip()]
    if not wanted:
        return []
    by_id = {str(item.get("id") or ""): _ensure_asset_admin_defaults(item) for item in _load_competitor_assets()}
    return [by_id[item] for item in wanted if item in by_id]


def _hotspots_by_ids(hotspot_ids: list[str]) -> list[dict]:
    wanted = [str(item) for item in hotspot_ids or [] if str(item or "").strip()]
    if not wanted:
        return []
    by_id = {str(_normalize_hotspot(item).get("id") or ""): _normalize_hotspot(item) for item in _load_hotspots()}
    return [by_id[item] for item in wanted if item in by_id]


def _script_context_snapshot(req: GenerateRequest) -> dict:
    if req.competitor_asset_ids:
        competitor_assets = _assets_by_ids(req.competitor_asset_ids)[:3]
    elif req.use_competitor_context:
        competitor_assets = []
        for status in ("featured", "approved"):
            rows, _ = _search_competitor_assets(
                category=req.category,
                review_status=status,
                rights_status="link_only_no_raw_video",
                limit=3,
            )
            competitor_assets.extend(rows)
            if len(competitor_assets) >= 3:
                break
        seen_assets = set()
        competitor_assets = [
            item
            for item in competitor_assets
            if not (item.get("id") in seen_assets or seen_assets.add(item.get("id")))
        ][:3]
    else:
        competitor_assets = []

    if req.hotspot_ids:
        hotspots = _hotspots_by_ids(req.hotspot_ids)[:5]
    elif req.use_hotspot_context:
        hotspots, _ = _search_hotspots(
            category=req.category,
            target_market=req.target_market,
            platform=req.platform,
            active_only=True,
            limit=5,
        )
    else:
        hotspots = []

    return {
        "competitor_assets": [_public_competitor_asset(item) for item in competitor_assets],
        "hotspots": hotspots[:5],
        "generated_at": _utc_now(),
    }


def _competitor_context_prompt(assets: list[dict]) -> str:
    if not assets:
        return "竞品素材上下文：当前没有已审核可引用的竞品素材；不要编造竞品信息。"
    lines = []
    for index, asset in enumerate(assets[:3], start=1):
        media_types = "、".join(sorted(_asset_media_types(asset))) or "未知媒体"
        tags = "、".join(asset.get("ai_tags") or [])
        lines.append(
            "\n".join(
                [
                    f"{index}. {asset.get('brand') or '竞品'} / {asset.get('platform') or asset.get('source_type') or '素材'}",
                    f"- 标题：{asset.get('title') or 'Untitled'}",
                    f"- 链接：{asset.get('source_url') or ''}",
                    f"- 媒体：{media_types}；质量分：{asset.get('quality_score') or 0}",
                    f"- 标签：{tags or '无'}",
                    f"- 分析：{asset.get('ai_analysis') or ''}",
                ]
            )
        )
    return (
        "竞品素材上下文（仅供内部参考创意节奏和画面组织，不要在表格中新增竞品字段；不得编造播放量、投放效果、竞品参数）：\n"
        + "\n".join(lines)
        + "\n可吸收其开场节奏、镜头组织或素材类型，但输出表格只保留固定 7 列。"
    )


def _hotspot_context_prompt(hotspots: list[dict]) -> str:
    if not hotspots:
        return "热点上下文：当前没有有效热点；如无必要不要强行加入热点。"
    lines = []
    for index, item in enumerate(hotspots[:5], start=1):
        tags = "、".join(item.get("tags") or [])
        lines.append(
            f"{index}. {item.get('title')}｜来源：{item.get('source_name')}｜市场：{item.get('target_market') or '通用'}｜有效期：{item.get('valid_from')} 至 {item.get('valid_to')}｜标签：{tags or '无'}"
        )
    return "行业热点上下文（仅选择与产品和目标市场自然相关的热点使用）：\n" + "\n".join(lines)


def _creative_playbook_for_variant(variant_index: int, req: GenerateRequest, script_direction: str = "") -> str:
    if script_direction == "开箱体验型":
        playbooks = [
            {
                "name": "开箱初见",
                "hook": "从包装到达、封条细节或手部拆箱动作切入，让观众先看到真实开箱过程。",
                "structure": "外包装到达 → 拆封取出 → 配件/内胆初见 → 摆放安装 → 首次使用 → 初次效果反馈",
                "camera": "俯拍拆箱、手部取出、推近产品细节、揭膜动作匹配转场；保持产品和包装痕迹同框。",
                "avoid": "避免写成纯痛点解决、生活种草或功能罗列，避免第一段跳过包装和取出动作。",
            },
            {
                "name": "细节检查",
                "hook": "用揭膜、检查质感、查看内胆/接口/配件等动作建立第一次上手的可信感。",
                "structure": "拆封细节 → 取出产品 → 关键部件检查 → 台面摆放 → 首次操作 → 效果确认",
                "camera": "近景特写、手部跟拍、局部拉焦、包装到产品的遮挡转场；突出新机状态和首次触摸。",
                "avoid": "避免把镜头主线改成家庭痛点、情绪反差或社媒挑战。",
            },
            {
                "name": "首次使用",
                "hook": "从刚开箱后的第一次放入食材/物品或第一次通电操作开始，呈现上手过程。",
                "structure": "拆箱完成 → 产品就位 → 首次放入物品 → 第一次操作 → 结果验证 → 品牌收束",
                "camera": "越肩手部视角、产品居中慢推、操作面板特写、结果前后动作匹配；保留包装/说明书作背景细节。",
                "avoid": "避免只写成参数演示或普通厨房日常，必须能看出这是第一次开箱上手。",
            },
        ]
    else:
        playbooks = [
            {
                "name": "同方向生活切入",
                "hook": "在所选脚本方向内，从一个具体生活任务或使用情境切入，让产品自然进入画面。",
                "structure": "情境建立 → 产品介入 → 核心动作证据 → 结果确认 → 品牌收束",
                "camera": "手持跟随、越肩视角、近景特写、动作匹配转场；镜头有生活现场感。",
                "avoid": "避免切换成未选择的脚本方向，避免第一段就念完整产品名。",
            },
            {
                "name": "同方向证据强化",
                "hook": "第一镜先给出与所选方向一致的结果或细节悬念，再展示产品如何做到。",
                "structure": "结果/细节预告 → 操作触发 → 卖点证明特写 → 二次验证 → 结果定格",
                "camera": "前后对比切、快速拉焦、遮挡转场、俯拍切细节；节奏更利落但不碎片化。",
                "avoid": "避免平铺每个按钮和参数，避免所有镜头都停留在同一景别。",
            },
            {
                "name": "同方向任务推进",
                "hook": "用三步任务、一个小测试或连续状态变化制造观看驱动力，但不改变所选方向。",
                "structure": "任务设定 → 连续动作推进 → 关键卖点放大 → 完成瞬间 → 品牌记忆",
                "camera": "俯拍任务台、快慢结合、物体擦镜、声桥或节奏点切换；强调动作完成感。",
                "avoid": "避免剧情过重或人物表演抢戏，避免只靠旁白解释卖点。",
            },
        ]
    item = playbooks[variant_index % len(playbooks)]
    return "\n".join(
        [
            f"本方案创意打法：{item['name']}",
            f"- 所属脚本方向：{script_direction or '按输入参数执行'}，不得改名或切换方向。",
            f"- Hook 方式：{item['hook']}",
            f"- 结构建议：{item['structure']}，最多 {MAX_SCRIPT_SEGMENTS} 段内完成。",
            f"- 镜头语言：{item['camera']}",
            f"- 本方案避雷：{item['avoid']}",
            "- 必须让本方案与其他方案在开场物品、转场方式、证据镜头和结尾画面上至少两处不同。",
        ]
    )


def _build_prompt(req: GenerateRequest, features: list[dict], variant_index: int, context_snapshot: dict | None = None) -> str:
    feature_lines = _feature_catalog_lines(features)
    direction = _script_direction_for_variant(req, variant_index)
    variant_no = variant_index + 1
    context_snapshot = context_snapshot or {"competitor_assets": [], "hotspots": []}
    competitor_context = _competitor_context_prompt(context_snapshot.get("competitor_assets") or [])
    hotspot_context = _hotspot_context_prompt(context_snapshot.get("hotspots") or [])
    creative_playbook = _creative_playbook_for_variant(variant_index, req, direction)
    duration_guidance = _duration_structure_guidance(req.expected_duration, req, features)
    quality_guidance = _script_quality_guidance(req, features)
    direction_guidance = _direction_specific_guidance(direction)
    return f"""
请生成【方案{variant_no}】海外电商短视频脚本（只输出这一套，不要输出其他方案标题）。
- 必须先输出一张符合系统要求的 Markdown 表格（7列，行内时长为秒，最后一行为总时长）。
- 表格必须包含并使用如下表头（逐字一致）：
{TABLE_HEADER_LINE}
- 表格单元格内禁止使用英文竖线“|”；镜头分段和运动轨迹分隔统一使用中文全角“｜”或中文箭头“→”。
- 表格后不要追加任何整段 AI 视频生成 Prompt、Negative Prompt、Recommended Settings 或解释文字。
- 与其他方案保持明显差异：开场 hook、产品视角、物品状态、画面口令、镜头运动、故事推进至少两处不同。
- 参考外部机构 AI 视频口令稿的结构：每一行都必须同时写清“镜头分段、可拍画面示意与表现手法、镜头运动&运动轨迹、功能卖点植入、英文旁白/字幕”。这些信息不能互相重复，也不能空泛。
- 镜头设计必须先服务故事推进，再服务卖点露出：开场负责吸引注意，中段用产品动作证明卖点，后段用结果/品牌记忆收束。
- 镜头分段必须参考上传样例的“镜头”字段：用“时间段 + 具体动作 + 括号内阶段/卖点证据”，例如“0-4s 冷饭盒拿出（复热痛点开场）”，不得只写镜头编号。
- 画面示意&表现手法必须像制作口令：包含主体比例、前景/中景/背景、产品位置、道具、被处理物品、手部动作和光影；严禁只写“产品特写”“功能展示”“见示意图”。
- 镜头运动&运动轨迹必须参考上传样例的“镜头运动/运镜轨迹”字段：写运动路径和方向，例如“近景特写（CU）｜推进至面板｜手指按键特写”“横移跟拍（左→右）｜手部动作入画”“俯拍切到腔体｜红点锁定食物中心”。不得只写“特写/俯拍/全景”。
- 功能卖点（英文）列必须优先逐字使用卖点库 Feature Name；没有直接卖点的开场/收尾行该列留空，或写真实产品/场景短语，严禁写 Pain-point opening、Brand closing、Opening、Closing 等结构标签。
- 先在内部确定本方案的创意策略，但不要输出策略过程；多方案只允许在所选脚本方向内改变开场 hook、产品切入角度、被处理物品状态和镜头组织，不得切换成未选择的脚本方向。
- 默认拍摄策略：产品/物品展示优先，淡化人物角色；除非补充要求明确需要人物剧情，不要设计专业模特、正脸表演或多人关系。
- 人物处理：可用手部/手臂/背影/越肩视角完成开门、按键、取放、摆放、擦拭等操作；不要让人物成为画面主角。
- 场景优先级：厨房电器优先写“产品 + 食材/餐具/台面/蒸汽/成品状态”的互动；冰箱/洗衣机/洗碗机等家电优先写“产品内部空间 + 被处理物品 + 使用前后结果”的可视化过程。
- 画面示意&表现手法必须落到产品可见动作和物品状态变化，不要只写“展示功能/突出卖点/产品特写”；每行至少包含一个产品本体或被处理物品的可拍动作、一个道具或环境细节、一个镜头处理。
- 语言强约束：除【旁白（英文）】与【字幕-显示卖点名及描述（英文）】两列外，其余列必须以中文为主。
- 英文列格式强约束：旁白和字幕两列不得带任何字段名/标签/括号前缀，直接输出纯英文句子。
- 标签泄漏强约束：任何表格单元格都不得出现 Pain-point opening、Brand closing、Opening、Closing、Hook、Intro、Outro、“开头：”“结尾：”“字幕：”“卖点：”等制作结构标签；这些只用于内部构思，不能进入成片文案。
- 卖点事实强约束：不得加入核心卖点中没有出现的功能概念或参数。

{direction_guidance}

{creative_playbook}

{duration_guidance}

{quality_guidance}

输入参数：
- 发布渠道：{req.platform}
- 目标市场：{req.target_market}
- 产品品类：{req.category}
- 产品型号：{req.model}
- 视频用途：{req.video_usage}
- 脚本方向：{direction}
- 期望时长：{req.expected_duration} 秒
- 项目类型：{req.project_type}
- 目标受众：{req.target_audience or "通用海外消费者"}
- 用户痛点：{req.pain_points or "结合产品卖点自行提炼"}
- 补充要求：{req.custom_requirements or "无"}
- 建议视频类型：{direction}

核心卖点：
{feature_lines or "- 请围绕产品核心功能和使用场景撰写。"}

{competitor_context}

{hotspot_context}
""".strip()


def _call_bedrock(prompt: str, temperature=0.7, top_p=0.9, system_prompt: str = SYSTEM_PROMPT, max_tokens: int | None = None) -> str:
    client = boto3.client("bedrock-runtime", region_name=BEDROCK_AWS_REGION)
    model_ids = [BEDROCK_MODEL_ID, *BEDROCK_MODEL_FALLBACK_IDS]
    last_error = None
    requested_tokens = int(max_tokens or BEDROCK_MAX_TOKENS)
    for model_id in model_ids:
        try:
            response = client.converse(
                modelId=model_id,
                system=[{"text": system_prompt}],
                messages=[{"role": "user", "content": [{"text": prompt}]}],
                inferenceConfig={
                    "maxTokens": _bedrock_max_tokens_for_model(model_id, requested_tokens),
                    "temperature": temperature,
                    "topP": top_p,
                },
            )
            content = response.get("output", {}).get("message", {}).get("content", [])
            text = "\n".join(
                str(item.get("text") or "") for item in content if isinstance(item, dict) and item.get("text")
            ).strip()
            if text:
                return text
            raise RuntimeError(f"Bedrock model {model_id} returned no text content.")
        except Exception as exc:
            last_error = exc
            continue
    raise RuntimeError(f"Bedrock API call failed for models {', '.join(model_ids)}: {last_error}")


def _load_nova_reel_jobs():
    data = _read_json(NOVA_REEL_JOBS_KEY, [])
    return data if isinstance(data, list) else []


def _save_nova_reel_jobs(jobs):
    return _write_json(NOVA_REEL_JOBS_KEY, jobs[:200])


def _load_nova_canvas_jobs():
    data = _read_json(NOVA_CANVAS_JOBS_KEY, [])
    return data if isinstance(data, list) else []


def _save_nova_canvas_jobs(jobs):
    return _write_json(NOVA_CANVAS_JOBS_KEY, jobs[:500])


def _utc_now_iso() -> str:
    return dt.datetime.utcnow().isoformat(timespec="seconds") + "Z"


def _update_nova_canvas_job(job_id: str, **changes):
    with job_lock:
        jobs = _load_nova_canvas_jobs()
        updated = None
        for index, item in enumerate(jobs):
            if item.get("id") == job_id:
                next_item = dict(item)
                next_item.update(changes)
                next_item["updated_at"] = changes.get("updated_at") or _utc_now_iso()
                jobs[index] = next_item
                updated = next_item
                break
        if updated:
            _save_nova_canvas_jobs(jobs)
        return updated


def _is_transient_storyboard_image_error(message: str) -> bool:
    raw = str(message or "").lower()
    return any(
        token in raw
        for token in (
            "429",
            "100054",
            "too many",
            "rate",
            "limit",
            "timeout",
            "timed out",
            "temporarily",
            "gateway",
            "read timed out",
            "request failed",
        )
    )


def _is_active_storyboard_image_status(status: str) -> bool:
    return str(status or "").lower() in {"pending", "queued", "running", "inprogress", "submitted"}


def _friendly_storyboard_image_error(message: str) -> str:
    raw = str(message or "")
    lowered = raw.lower()
    if "429" in lowered or "100054" in lowered or "rate" in lowered or "limit" in lowered:
        return "LibLibAI 生成服务繁忙或达到频率限制，系统已自动重试但仍未成功。请稍后重新生成该镜头。"
    if "timeout" in lowered or "timed out" in lowered or "gateway" in lowered:
        return "LibLibAI 生成耗时过长，系统已转入后台队列并重试后仍未成功。请稍后重新生成该镜头。"
    return raw or "参考图生成失败。"


def _load_product_image_assets():
    data = _read_json(PRODUCT_IMAGE_ASSETS_KEY, [])
    return data if isinstance(data, list) else []


def _save_product_image_assets(assets):
    return _write_json(PRODUCT_IMAGE_ASSETS_KEY, assets[:300])


def _load_storyboard_video_jobs():
    data = _read_json(STORYBOARD_VIDEO_JOBS_KEY, [])
    return data if isinstance(data, list) else []


def _save_storyboard_video_jobs(jobs):
    return _write_json(STORYBOARD_VIDEO_JOBS_KEY, jobs[:200])


def _nova_canvas_image_key(script_job_id, variant_index, shot_index):
    stamp = dt.datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
    return (
        "nova-canvas-storyboards/"
        f"{_safe_ascii_slug(script_job_id)}/"
        f"variant_{int(variant_index) + 1}_shot_{int(shot_index) + 1}_{stamp}_{uuid.uuid4().hex[:8]}.png"
    )


def _product_image_keys(script_job_id, variant_index, image_id, original_format):
    ext = "jpg" if str(original_format or "").upper() in {"JPG", "JPEG"} else "png"
    base = (
        "product-image-assets/"
        f"{_safe_ascii_slug(script_job_id)}/"
        f"variant_{int(variant_index) + 1}_{_safe_ascii_slug(image_id)}"
    )
    return f"{base}_original.{ext}", f"{base}_normalized.png"


def _product_image_by_id(image_id: str, script_job_id: str = ""):
    wanted = str(image_id or "").strip()
    if not wanted:
        return None
    for item in _load_product_image_assets():
        if str(item.get("id") or "") != wanted:
            continue
        if script_job_id and str(item.get("script_job_id") or "") != str(script_job_id):
            continue
        return item
    return None


def _public_product_image_asset(asset):
    public = dict(asset or {})
    if public.get("id"):
        public["preview_url"] = f"/api/product-images/{public['id']}"
    return public


def _normalize_product_image_bytes(data: bytes):
    try:
        from PIL import Image, ImageOps, UnidentifiedImageError
    except ModuleNotFoundError as exc:
        raise RuntimeError("Pillow 未安装，无法处理产品图。") from exc

    try:
        image = Image.open(io.BytesIO(data))
        image.load()
    except UnidentifiedImageError as exc:
        raise ValueError("请上传 PNG 或 JPEG 产品图。") from exc

    image_format = str(image.format or "").upper()
    if image_format == "JPG":
        image_format = "JPEG"
    if image_format not in {"PNG", "JPEG"}:
        raise ValueError("请上传 PNG 或 JPEG 产品图。")

    width, height = image.size
    if image.mode in {"RGBA", "LA"} or ("transparency" in image.info):
        rgba = image.convert("RGBA")
        background = Image.new("RGBA", rgba.size, (246, 248, 249, 255))
        background.alpha_composite(rgba)
        image = background.convert("RGB")
    else:
        image = image.convert("RGB")

    canvas = Image.new("RGB", (1280, 720), (246, 248, 249))
    contained = ImageOps.contain(image, (1280, 720), method=Image.Resampling.LANCZOS)
    x = (1280 - contained.width) // 2
    y = (720 - contained.height) // 2
    canvas.paste(contained, (x, y))

    output = io.BytesIO()
    canvas.save(output, format="PNG", optimize=True)
    return image_format, width, height, output.getvalue()


def _clamp_crop_box(box, width: int, height: int):
    left, top, right, bottom = box
    left = max(0, min(int(round(left)), width - 1))
    top = max(0, min(int(round(top)), height - 1))
    right = max(left + 1, min(int(round(right)), width))
    bottom = max(top + 1, min(int(round(bottom)), height))
    return left, top, right, bottom


def _front_load_appliance_crop_box(image):
    width, height = image.size
    if width < 80 or height < 80:
        return None
    analysis_width = min(360, max(120, width))
    analysis_height = max(1, int(round(height * analysis_width / width)))
    gray = image.convert("L").resize((analysis_width, analysis_height))
    pixels = gray.tobytes()
    mean_value = sum(pixels) / max(1, len(pixels))
    threshold = min(120, max(42, int(mean_value * 0.68)))
    mask = bytearray(1 if value <= threshold else 0 for value in pixels)
    visited = bytearray(len(mask))
    best = None

    for y in range(analysis_height):
        for x in range(analysis_width):
            index = y * analysis_width + x
            if visited[index] or not mask[index]:
                continue
            stack = [index]
            visited[index] = 1
            area = 0
            min_x = max_x = x
            min_y = max_y = y
            while stack:
                current = stack.pop()
                cx = current % analysis_width
                cy = current // analysis_width
                area += 1
                min_x = min(min_x, cx)
                max_x = max(max_x, cx)
                min_y = min(min_y, cy)
                max_y = max(max_y, cy)
                for nx, ny in ((cx - 1, cy), (cx + 1, cy), (cx, cy - 1), (cx, cy + 1)):
                    if nx < 0 or ny < 0 or nx >= analysis_width or ny >= analysis_height:
                        continue
                    neighbor = ny * analysis_width + nx
                    if visited[neighbor] or not mask[neighbor]:
                        continue
                    visited[neighbor] = 1
                    stack.append(neighbor)

            box_width = max_x - min_x + 1
            box_height = max_y - min_y + 1
            if area < 55 or box_width < 12 or box_height < 12:
                continue
            aspect = box_width / max(1, box_height)
            fill = area / max(1, box_width * box_height)
            center_x = (min_x + max_x) / 2 / analysis_width
            center_y = (min_y + max_y) / 2 / analysis_height
            if not (0.45 <= aspect <= 2.25 and 0.08 <= fill <= 0.92):
                continue
            if not (0.18 <= center_x <= 0.82 and 0.25 <= center_y <= 0.90):
                continue
            centrality = 1.0 - min(1.0, abs(center_x - 0.5) * 2.0)
            roundness = 1.0 - min(1.0, abs(1.0 - aspect) / 1.25)
            score = area * (0.35 + centrality) * (0.45 + roundness) * (0.65 + center_y)
            if best is None or score > best[0]:
                best = (score, min_x, min_y, max_x, max_y)

    if not best:
        return None
    _, min_x, min_y, max_x, max_y = best
    scale_x = width / analysis_width
    scale_y = height / analysis_height
    center_x = ((min_x + max_x) / 2) * scale_x
    center_y = ((min_y + max_y) / 2) * scale_y
    diameter = max((max_x - min_x + 1) * scale_x, (max_y - min_y + 1) * scale_y)
    crop_width = max(diameter * 3.05, width * 0.22)
    crop_height = max(diameter * 3.75, height * 0.40)
    return _clamp_crop_box(
        (
            center_x - crop_width * 0.50,
            center_y - crop_height * 0.58,
            center_x + crop_width * 0.50,
            center_y + crop_height * 0.42,
        ),
        width,
        height,
    )


def _center_product_crop_box(image):
    width, height = image.size
    ratio = width / max(1, height)
    if ratio >= 1.45:
        crop_width = int(width * 0.58)
        crop_height = int(height * 0.96)
    elif ratio <= 0.78:
        crop_width = int(width * 0.96)
        crop_height = int(height * 0.72)
    else:
        crop_width = int(width * 0.78)
        crop_height = int(height * 0.86)
    left = (width - crop_width) / 2
    top = (height - crop_height) / 2
    return _clamp_crop_box((left, top, left + crop_width, top + crop_height), width, height)


def _prepare_storyboard_reference_image_bytes(data: bytes, category="", model="", prompt=""):
    try:
        from PIL import Image, ImageEnhance, ImageOps, UnidentifiedImageError
    except ModuleNotFoundError:
        return data, {"reference_preprocess": "original"}

    try:
        image = Image.open(io.BytesIO(data))
        image.load()
    except (UnidentifiedImageError, OSError):
        return data, {"reference_preprocess": "original"}

    image = ImageOps.exif_transpose(image)
    if image.mode in {"RGBA", "LA"} or ("transparency" in image.info):
        rgba = image.convert("RGBA")
        background = Image.new("RGBA", rgba.size, (246, 248, 249, 255))
        background.alpha_composite(rgba)
        image = background.convert("RGB")
    else:
        image = image.convert("RGB")

    detection_text = f"{category} {model} {prompt}"
    crop_box = None
    reference_preprocess = "center-product-reference-card"
    if _is_laundry_storyboard(detection_text):
        crop_box = _front_load_appliance_crop_box(image)
        if crop_box:
            reference_preprocess = "front-load-product-reference-card"
    if not crop_box:
        crop_box = _center_product_crop_box(image)

    crop = image.crop(crop_box)
    crop = ImageEnhance.Sharpness(crop).enhance(1.18)
    card = Image.new("RGB", (1024, 1024), (246, 248, 249))
    contained = ImageOps.contain(crop, (900, 930), method=Image.Resampling.LANCZOS)
    x = (1024 - contained.width) // 2
    y = (1024 - contained.height) // 2
    card.paste(contained, (x, y))
    output = io.BytesIO()
    card.save(output, format="PNG", optimize=True)
    return output.getvalue(), {
        "reference_preprocess": reference_preprocess,
        "reference_crop_box": list(crop_box),
        "reference_card_size": [1024, 1024],
    }


def _liblibai_uses_source_image_mode() -> bool:
    return str(LIBLIBAI_REFERENCE_MODE or "").lower().strip() in LiblibAIClient.SOURCE_IMAGE_REFERENCE_MODES


def _is_laundry_storyboard(text):
    raw = str(text or "").lower()
    if "dishwasher" in raw or "\u6d17\u7897" in raw:
        return False
    return any(
        token in raw
        for token in (
            "washer",
            "dryer",
            "laundry",
            "washing machine",
            "\u6d17",
            "\u70d8",
            "\u8863",
        )
    )

def _has_any_storyboard_token(raw: str, tokens: tuple[str, ...]) -> bool:
    return any(token in raw for token in tokens)


_SINGLE_PRODUCT_RULE = (
    "Single-product rule: show exactly one physical Hisense appliance unit as the only product in the scene. "
    "Do not add a second unit, duplicate product, side-by-side appliance, showroom lineup, or background appliance "
    "of the same category."
)

_SINGLE_PRODUCT_NEGATIVE = (
    "multiple appliances, duplicate product, second product unit, side-by-side appliances, product lineup, "
    "showroom display, appliance showroom lineup, background appliance of the same category"
)

_HISENSE_BRAND_RULE = (
    "Brand text rule: any readable appliance logo or brand text must be exactly 'Hisense' with complete, sharp "
    "Latin letters. Do not invent, abbreviate, distort, or misspell the brand; if exact text cannot be rendered, "
    "leave the appliance badge blank rather than using fake letters."
)

_HISENSE_BRAND_NEGATIVE = (
    "misspelled Hisense, partial Hisense, distorted Hisense, garbled logo, unreadable logo, fake brand word, "
    "Hisnse, Hiense, Hisens, Hisensse, Hicense, Hissense, Hisonse"
)


def _storyboard_category_context(category, model, detection_text=""):
    raw = f"{category or ''} {model or ''} {detection_text or ''}".lower()
    if _is_laundry_storyboard(raw):
        return {
            "subject": "one Hisense front-loading washing machine or washer-dryer combo",
            "setting": "a modern laundry room or utility room, never a kitchen",
            "must": (
                "The frame must clearly show exactly one front-loading laundry appliance unit as the main subject. "
                "Use laundry-room cues such as folded towels, a laundry basket, hanging clothes, detergent, or a utility "
                "shelf as relevant to the shot, but do not show a second washer or dryer."
            ),
            "negative": (
                "kitchen, stove, stovetop, oven, microwave, refrigerator, kettle, cookware, food preparation, "
                "dining room, cooking appliance, kitchen island, second washer, second dryer, paired washer and dryer"
            ),
        }

    if _has_any_storyboard_token(
        raw,
        (
  "空气炸锅",
  "air fryer",
  "air fry",
  "fryer",
  "frying",
  "little oil",
  "frozen food",
  "6.3l",
  "6.3 l",
        ),
    ):
        return {
  "subject": f"Hisense {model or ''} countertop air fryer with a visible basket or drawer and control buttons".strip(),
  "setting": "a modern kitchen countertop or breakfast prep counter, never a living room",
  "must": (
      "The frame must clearly show a countertop air fryer as the main foreground subject. "
      "Include food being placed into or removed from the fryer basket, the drawer/basket edge, "
      "and the control button or panel when the shot mentions pressing a button."
  ),
  "negative": (
      "television, tv screen, living room, entertainment console, sofa, media wall, bedroom, "
      "wall-mounted display, refrigerator as main subject, microwave as main subject, oven as main subject, "
      "unrelated appliance, product absent, empty room"
  ),
        }
    if _has_any_storyboard_token(raw, ("微波", "microwave", "reheat", "defrost", "popcorn")):
        return {
  "subject": f"Hisense {model or ''} countertop microwave oven with visible door, cavity, and control panel".strip(),
  "setting": "a modern kitchen countertop, never a living room",
  "must": (
      "The frame must clearly show a countertop microwave oven as the main subject, with the door, "
      "control panel, plate, food container, steam, or reheated food visible according to the shot."
  ),
  "negative": (
      "television, tv screen, living room, sofa, entertainment console, air fryer as main subject, "
      "refrigerator as main subject, unrelated appliance, product absent"
  ),
        }
    if _has_any_storyboard_token(raw, ("烤箱", "oven", "bake", "roast", "pizza")):
        return {
  "subject": f"Hisense {model or ''} kitchen oven with visible door, cavity, tray, and control area".strip(),
  "setting": "a modern kitchen or kitchen countertop, never a living room",
  "must": (
      "The frame must clearly show the oven as the main subject, with food on a tray, the oven door, "
      "interior light, control area, or baked result visible according to the shot."
  ),
  "negative": (
      "television, tv screen, living room, sofa, entertainment console, refrigerator as main subject, "
      "unrelated appliance, product absent"
  ),
        }
    if _has_any_storyboard_token(raw, ("冰箱", "refrigerator", "fridge", "freezer", "freshness", "fresh food")):
        return {
  "subject": f"Hisense {model or ''} refrigerator with visible doors, shelves, drawers, and stored food".strip(),
  "setting": "a modern kitchen, never a living room TV wall",
  "must": (
      "The frame must clearly show a refrigerator as the main subject, with open doors, shelves, "
      "drawers, food containers, produce, or storage before-and-after cues according to the shot."
  ),
  "negative": (
      "television, tv screen, entertainment console, sofa, air fryer as main subject, microwave as main subject, "
      "unrelated appliance, product absent"
  ),
        }
    if _has_any_storyboard_token(raw, ("洗碗", "dishwasher", "dishes", "tableware", "餐具")):
        return {
  "subject": f"Hisense {model or ''} dishwasher with visible racks, door, dishes, and control panel".strip(),
  "setting": "a modern kitchen beside cabinets or a sink, never a living room",
  "must": (
      "The frame must clearly show a dishwasher as the main subject, with open racks, plates, bowls, "
      "cutlery, clean results, or a control panel visible according to the shot."
  ),
  "negative": (
      "television, tv screen, living room, sofa, entertainment console, laundry appliances as main subject, "
      "unrelated appliance, product absent"
  ),
        }
    return {
        "subject": f"Hisense {category or 'home appliance'} {model or ''}".strip(),
        "setting": "a realistic premium home environment that matches the product category",
        "must": "The frame must show the selected product category as the main subject and follow the shot action.",
        "negative": "wrong product category, unrelated room, unrelated appliance",
    }


def _storyboard_action_constraints(prompt):
    raw = str(prompt or "")
    lower = raw.lower()
    constraints = []
    focus = _storyboard_visual_focus(raw)
    if focus.get("constraint"):
        constraints.append(focus["constraint"])
    if _storyboard_requires_user(raw):
        constraints.append(
            "A visible adult user must be present in the foreground and actively interacting with the product or the laundry task."
        )
    if _storyboard_switching_laundry(raw):
        constraints.append(
            "Show the laundry pain point with one front-loading washer-dryer combo: the user holds a laundry basket or wet clothes near the open drum, implying avoided washer-to-dryer transfer without showing a second machine."
        )
    if "top-down" in lower or "overhead" in lower or "\u4fef\u62cd" in raw:
        constraints.append("Use a high-angle overhead or three-quarter top-down camera view, not a straight eye-level kitchen view.")
    if "fixed" in lower or "\u56fa\u5b9a" in raw:
        constraints.append("Use a stable locked-off composition with no motion blur.")
    return " ".join(constraints)


def _storyboard_requires_user(prompt):
    raw = str(prompt or "")
    lower = raw.lower()
    return any(
        token in lower
        for token in (
            "user",
            "person",
            "woman",
            "man",
            "hand",
            "hands",
            "holding",
            "take out",
            "taking out",
            "remove",
            "removing",
        )
    ) or any(token in raw for token in ("\u7528\u6237", "\u624b", "\u624b\u6301", "\u53d6\u51fa", "\u62ff\u51fa", "\u62ff\u8d77"))


def _storyboard_visual_focus(prompt, category="", model=""):
    raw = str(prompt or "")
    lower = raw.lower()
    is_laundry = _is_laundry_storyboard(f"{category} {model} {raw}")
    result_tokens = (
        "clean clothes",
        "clean laundry",
        "fresh laundry",
        "fluffy",
        "washed clothes",
        "dry clothes",
        "dried clothes",
        "folded clothes",
        "laundry basket",
        "result",
        "completion",
        "finished",
    )
    hand_tokens = (
        "hand",
        "hands",
        "holding",
        "take out",
        "taking out",
        "remove clothes",
        "removing clothes",
        "pulling out",
    )
    control_tokens = (
        "button",
        "buttons",
        "display",
        "led",
        "control panel",
        "panel",
        "knob",
        "program",
        "cycle",
    )
    chinese_result_tokens = (
        "\u5e72\u51c0",
        "\u6e05\u6d01",
        "\u6d01\u51c0",
        "\u84ec\u677e",
        "\u8863\u7269",
        "\u8863\u670d",
        "\u6bdb\u5dfe",
        "\u6d17\u6da4\u5b8c\u6210",
        "\u6d17\u5b8c",
        "\u5b8c\u6210",
        "\u7ed3\u679c",
        "\u9a8c\u8bc1",
    )
    chinese_hand_tokens = ("\u624b", "\u624b\u6301", "\u53d6\u51fa", "\u62ff\u51fa", "\u62ff\u8d77", "\u53d6\u8d70")
    chinese_control_tokens = ("\u6309\u94ae", "\u663e\u793a", "\u663e\u793a\u5c4f", "LED", "\u63a7\u5236", "\u65cb\u94ae", "\u7a0b\u5e8f")

    has_result = any(token in lower for token in result_tokens) or any(token in raw for token in chinese_result_tokens)
    has_hand = any(token in lower for token in hand_tokens) or any(token in raw for token in chinese_hand_tokens)
    has_control = any(token in lower for token in control_tokens) or any(token in raw for token in chinese_control_tokens)

    if is_laundry and has_hand:
        return {
            "kind": "laundry-hand-result",
            "primary": "hands holding or taking out clean, fluffy laundry from the washer opening",
            "composition": (
                "The hands and clean laundry must be the dominant foreground subject. Show the washer door or drum "
                "as supporting context, but do not make this a front-on appliance product shot."
            ),
            "constraint": (
                "Must show visible hands and clean/fluffy clothes being removed or held; an appliance-only image is incorrect."
            ),
            "negative": "closed washer with no hands, no clothes, appliance-only hero shot, empty drum",
            "reference_policy": "skip-action-result-reference",
        }
    if is_laundry and has_result and not has_control:
        return {
            "kind": "laundry-clean-result",
            "primary": "clean washed laundry, towels, or clothes presented as the washing result",
            "composition": (
                "The clean laundry result must be clearly visible and visually dominant. The washer may appear open, "
                "partial, or in the background as context."
            ),
            "constraint": (
                "Must show clean clothes/towels as the result of washing; an appliance-only image is incorrect."
            ),
            "negative": "closed washer with no clothes, appliance-only hero shot, empty laundry room",
            "reference_policy": "skip-action-result-reference",
        }
    if is_laundry and has_control:
        return {
            "kind": "laundry-control-detail",
            "primary": "the washer control-panel/display/button interaction requested by the storyboard",
            "composition": (
                "A close product-detail composition is acceptable, but the requested button, knob, display, or program "
                "state must be visible and sharp."
            ),
            "constraint": "Must clearly show the requested control-panel/display/button detail.",
            "negative": "generic washer front with unreadable controls, missing display, missing button interaction",
            "reference_policy": "use-product-reference",
        }
    return {
        "kind": "default",
        "primary": "the exact visual action or result stated in the storyboard row",
        "composition": "Follow the storyboard row before product reference composition.",
        "constraint": "",
        "negative": "image unrelated to the storyboard action",
        "reference_policy": "use-product-reference",
    }


def _storyboard_reference_policy(prompt, category="", model=""):
    return _storyboard_visual_focus(prompt, category=category, model=model).get("reference_policy", "use-product-reference")


def _storyboard_switching_laundry(prompt):
    raw = str(prompt or "")
    lower = raw.lower()
    has_washer = "washer" in lower or "\u6d17" in raw
    has_dryer = "dryer" in lower or "\u70d8" in raw
    if not (has_washer and has_dryer):
        return False

    english_patterns = (
        r"\bswitch(?:ing|es|ed)?\s+(?:back\s+and\s+forth\s+)?between\s+"
        r"(?:the\s+)?(?:washer|washing machine)\s+and\s+(?:the\s+)?dryer\b",
        r"\bbetween\s+(?:the\s+)?(?:washer|washing machine)\s+and\s+"
        r"(?:the\s+)?dryer\b.{0,80}\bswitch(?:ing|es|ed)?\b",
        r"\b(?:move|moving|transfer|transferring)\b.{0,80}\bfrom\s+"
        r"(?:the\s+)?(?:washer|washing machine)\s+to\s+(?:the\s+)?dryer\b",
    )
    if any(re.search(pattern, lower) for pattern in english_patterns):
        return True

    chinese_appliance_pair = (
        r"(?:\u6d17\u8863\u673a|\u6d17\u8863|\u6d17).{0,12}(?:\u70d8\u5e72\u673a|\u70d8\u5e72|\u70d8)"
        r"|(?:\u70d8\u5e72\u673a|\u70d8\u5e72|\u70d8).{0,12}(?:\u6d17\u8863\u673a|\u6d17\u8863|\u6d17)"
    )
    chinese_between = r"(?:\u4e4b\u95f4|\u4e24\u673a|\u4e2d\u95f4)"
    chinese_action = r"(?:\u5207\u6362|\u6765\u56de|\u8f6c\u79fb|\u642c|\u62ff|\u79fb)"
    if re.search(rf"(?:{chinese_appliance_pair}).{{0,16}}{chinese_between}.{{0,16}}{chinese_action}", raw):
        return True
    if re.search(rf"{chinese_between}.{{0,16}}{chinese_action}.{{0,16}}(?:{chinese_appliance_pair})", raw):
        return True
    if re.search(
        r"(?:\u4ece|\u628a).{0,16}(?:\u6d17\u8863\u673a|\u6d17\u8863|\u6d17).{0,16}"
        r"(?:\u8f6c\u79fb|\u642c|\u62ff|\u79fb).{0,16}(?:\u70d8\u5e72\u673a|\u70d8\u5e72|\u70d8)",
        raw,
    ):
        return True
    return False


def _enhance_storyboard_image_prompt(prompt, category="", model="", shot_index=0, reference_policy="use-product-reference"):
    raw_prompt = str(prompt or "").strip()[:900]
    context = _storyboard_category_context(category, model, detection_text=raw_prompt)
    focus = _storyboard_visual_focus(raw_prompt, category=category, model=model)
    action_constraints = _storyboard_action_constraints(raw_prompt)
    scene_instruction = focus["constraint"] if str(reference_policy or "").startswith("skip-") else context["must"]
    reference_text = (
        "preserve the supplied reference only for product identity, silhouette, color, finish, door outline, "
        "handle/buttons, logo placement, control-panel layout, cavity/drum shape, and proportions"
        if not str(reference_policy or "").startswith("skip-")
        else "no product reference image is used; obey the storyboard action/result instead of making a product packshot"
    )
    lines = [
        (
            "Subject: exactly one physical Hisense appliance as the core object; "
            f"{context['subject']}; model Hisense {model or 'from brief'}; {reference_text}; "
            "no duplicate product, no second unit, no side-by-side appliances, no showroom lineup, "
            "no same-category background appliance."
        ),
        (
            "Action/Pose: depict the exact storyboard row before any generic product pose; "
            f"shot {int(shot_index) + 1}; critical visual target: {focus['primary']}; "
            f"required composition: {focus['composition']}; primary scene: {scene_instruction}; "
            f"camera/action constraints: {action_constraints or 'follow the storyboard camera and action cues'}; "
            f"storyboard details: {raw_prompt}."
        ),
        (
            "Background: build a new real-life scene from the storyboard row; "
            f"setting: {context['setting']}; do not copy the reference image room, closet, cabinet layout, "
            "white background, catalog packshot angle, crop, sticker/badge, lighting, or camera angle."
        ),
        (
            "Lighting/Color: clean commercial soft daylight, realistic natural colors, readable product contours, "
            "subtle rim light, balanced exposure, no harsh glare, no UI mockups."
        ),
        (
            "Style/Rendering: premium 16:9 photorealistic e-commerce storyboard still, high detail, sharp and "
            "physically plausible handles, buttons, knobs, display area, door seams, drum/cavity shape, and panel geometry; "
            "readable logo text must be exactly 'Hisense' with complete sharp letters; no text overlay, no watermark, "
            "no discount badge, no round sticker, no competitor brands, no wrong product category; "
            f"negative: {context['negative']}; {focus['negative']}; {_SINGLE_PRODUCT_NEGATIVE}; {_HISENSE_BRAND_NEGATIVE}."
        ),
    ]
    return "\n".join(line for line in lines if line).strip()[:3000]


def _image_negative_prompt(prompt, category="", model=""):
    context = _storyboard_category_context(category, model, detection_text=prompt)
    terms = [
        "competitor brands",
        "distorted logo",
        _HISENSE_BRAND_NEGATIVE,
        "unreadable text",
        "text overlay",
        "watermark",
        "discount badge",
        "round sticker",
        "promo sticker",
        "catalog packshot",
        "isolated product on white background",
        "plain white seamless background",
        "low quality",
        "blurry",
        "cartoon",
        "cgi look",
        "deformed product",
        "extra products",
        _SINGLE_PRODUCT_NEGATIVE,
        "cluttered composition",
        context["negative"],
    ]
    if _is_laundry_storyboard(f"{category} {model} {prompt}"):
        terms.extend(
            [
                "kitchen cabinets as the main scene",
                "person cooking",
                "woman standing in a kitchen",
                "range hood",
                "cooktop",
            ]
        )
    if _storyboard_requires_user(prompt):
        terms.extend(["empty room", "empty appliance showroom", "appliance-only lineup with no user"])
    if _storyboard_switching_laundry(prompt):
        terms.extend(["second laundry machine", "separate dryer", "washer and dryer pair", "appliance showroom lineup"])
    return ", ".join(item for item in terms if item)


def _image_model_supports_reference_image(model_id: str) -> bool:
    normalized = str(model_id or "")
    return normalized.startswith("amazon.nova-canvas") or normalized.startswith("stability.sd3-5")


def _bedrock_image_request_body(prompt, seed, category="", model="", reference_image_bytes: bytes | None = None):
    model_id = str(NOVA_CANVAS_MODEL_ID or "")
    prompt_text = str(prompt or "premium e-commerce storyboard reference image")[:3000]
    negative_prompt = _image_negative_prompt(prompt_text, category=category, model=model)
    if reference_image_bytes:
        encoded_reference = base64.b64encode(reference_image_bytes).decode("utf-8")
        if model_id.startswith("stability.sd3-5"):
            reference_prompt = (
                "Use the uploaded product image only as a weak identity reference. Generate a new storyboard scene "
                "from the prompt, with realistic environment, props, hands/action, and camera angle. Do not recreate "
                "the uploaded catalog image or its white background/sticker/composition.\n\n"
                f"{prompt_text}"
            )
            return {
                "mode": "image-to-image",
                "prompt": reference_prompt[:3000],
                "negative_prompt": negative_prompt,
                "image": encoded_reference,
                "strength": NOVA_CANVAS_REFERENCE_STRENGTH,
                "output_format": "png",
                "seed": seed,
            }
        if not _image_model_supports_reference_image(model_id):
            raise RuntimeError(
                "产品图参考分镜图需要配置支持图片参考的 Bedrock 图像模型，"
                f"例如 stability.sd3-5-large-v1:0；当前 NOVA_CANVAS_MODEL_ID={model_id or '未配置'}。"
            )
        return {
            "taskType": "IMAGE_VARIATION",
            "imageVariationParams": {
                "text": prompt_text[:512],
                "negativeText": negative_prompt[:512],
                "images": [encoded_reference],
                "similarityStrength": 0.65,
            },
            "imageGenerationConfig": {
                "numberOfImages": 1,
                "height": 720,
                "width": 1280,
                "cfgScale": 7.0,
                "seed": seed,
            },
        }
    if model_id.startswith("stability."):
        return {
            "prompt": prompt_text,
            "negative_prompt": negative_prompt,
            "mode": "text-to-image",
            "aspect_ratio": "16:9",
            "output_format": "png",
            "seed": seed,
        }
    return {
        "taskType": "TEXT_IMAGE",
        "textToImageParams": {
            "text": prompt_text[:512],
            "negativeText": negative_prompt,
        },
        "imageGenerationConfig": {
            "numberOfImages": 1,
            "height": 720,
            "width": 1280,
            "cfgScale": 7.0,
            "seed": seed,
        },
    }


def _decode_bedrock_image_payload(payload):
    images = payload.get("images") or []
    if images:
        first = images[0]
        if isinstance(first, str):
            return base64.b64decode(first)
        if isinstance(first, dict):
            encoded = first.get("base64") or first.get("image")
            if encoded:
                return base64.b64decode(encoded)
    artifacts = payload.get("artifacts") or []
    if artifacts:
        first = artifacts[0]
        if isinstance(first, dict):
            encoded = first.get("base64") or first.get("image")
            if encoded:
                return base64.b64decode(encoded)
    return None


def _image_provider_name() -> str:
    provider = str(MEDIA_IMAGE_PROVIDER or "nova_canvas").strip().lower().replace("-", "_")
    if provider in {"liblib", "liblibai", "liblibai_star3", "star3"}:
        return "liblibai"
    return "nova_canvas"


def _image_provider_model_id() -> str:
    if _image_provider_name() == "liblibai":
        return LIBLIBAI_IMAGE_MODEL_LABEL
    return NOVA_CANVAS_MODEL_ID


def _liblibai_image_config() -> LiblibAIConfig:
    access_key = _current_liblibai_access_key()
    secret_key = _current_liblibai_secret_key()
    if not access_key or not secret_key:
        raise RuntimeError("LIBLIBAI_ACCESS_KEY / LIBLIBAI_SECRET_KEY is not configured.")
    return LiblibAIConfig(
        access_key=access_key,
        secret_key=secret_key,
        base_url=LIBLIBAI_BASE_URL,
        template_uuid=LIBLIBAI_TEMPLATE_UUID,
        img2img_template_uuid=LIBLIBAI_IMG2IMG_TEMPLATE_UUID,
        aspect_ratio=LIBLIBAI_IMAGE_ASPECT_RATIO,
        width=LIBLIBAI_IMAGE_WIDTH,
        height=LIBLIBAI_IMAGE_HEIGHT,
        include_image_size=LIBLIBAI_IMAGE_SIZE_ENABLED,
        steps=LIBLIBAI_IMAGE_STEPS,
        image_count=LIBLIBAI_IMAGE_COUNT,
        request_timeout_seconds=LIBLIBAI_REQUEST_TIMEOUT,
        poll_timeout_seconds=LIBLIBAI_POLL_TIMEOUT,
        poll_interval_seconds=LIBLIBAI_POLL_INTERVAL,
        max_prompt_length=LIBLIBAI_MAX_PROMPT_LENGTH,
        reference_control_type=LIBLIBAI_REFERENCE_CONTROL_TYPE,
        reference_mode=LIBLIBAI_REFERENCE_MODE,
        fallback_to_controlnet=LIBLIBAI_FALLBACK_TO_CONTROLNET,
    )


def _compact_liblibai_storyboard_prompt(raw_prompt, category="", model="", shot_index=0, reference_policy="use-product-reference") -> str:
    context = _storyboard_category_context(category, model, detection_text=raw_prompt)
    focus = _storyboard_visual_focus(raw_prompt, category=category, model=model)
    action_constraints = _storyboard_action_constraints(raw_prompt)
    scene_instruction = focus["constraint"] if str(reference_policy or "").startswith("skip-") else context["must"]
    detail = re.sub(r"\s+", " ", str(raw_prompt or "")).strip()
    for boilerplate in (
        "Premium 16:9 photorealistic e-commerce storyboard reference image for a Hisense product video.",
        "Premium photorealistic 16:9 e-commerce storyboard still.",
        "Follow the storyboard exactly; do not invent another product category, room, or action.",
    ):
        detail = detail.replace(boilerplate, "").strip()
    detail = re.sub(r"\b(?:Single-product|One-product) rule:.*?(?=(?:Subject:|Action/Pose:|Background:|Lighting/Color:|Style/Rendering:|Product category|Product model|Shot:|Product benefit|Visual action|Camera angle|Keep the product message|$))", "", detail).strip()
    detail = re.sub(r"\bBrand (?:text )?rule:.*?(?=(?:Subject:|Action/Pose:|Background:|Lighting/Color:|Style/Rendering:|Product category|Product model|Shot:|Product benefit|Visual action|Camera angle|Keep the product message|$))", "", detail).strip()
    detail = re.sub(r"\bThe selected product must be the main subject.*$", "", detail).strip()
    detail = detail[:520].strip()
    reference_text = (
        "preserve reference only for appliance identity, color, finish, door outline, handle/control-panel, buttons, knobs, display, cavity/drum shape, logo position, and proportions"
        if not str(reference_policy or "").startswith("skip-")
        else "no product reference image is used; obey the storyboard action/result instead of making a packshot"
    )
    lines = [
        f"Subject: exactly one physical Hisense appliance; {context['subject']}; model Hisense {model or 'from brief'}; {reference_text}; no duplicate, second unit, side-by-side appliance, product lineup, or background same-category appliance.",
        f"Action/Pose: shot {int(shot_index) + 1}; critical visual target: {focus['primary']}; composition: {focus['composition']}; scene action/result: {scene_instruction}; camera/action constraints: {action_constraints or 'follow storyboard cues'}; storyboard details: {detail}.",
        f"Background: {context['setting']}; create a new real-life scene from the storyboard row; do not copy the reference room, closet, cabinet layout, white background, lighting, crop, or camera angle.",
        "Lighting/Color: clean commercial soft daylight, realistic natural colors, readable contours, subtle rim light, balanced exposure, no harsh glare.",
        f"Style/Rendering: premium 16:9 photorealistic e-commerce storyboard still, high detail, sharp physically plausible appliance geometry, complete sharp 'Hisense' logo only if readable, no text overlay, no watermark, no discount badge, no competitor brands, no wrong product category. Negative: {_HISENSE_BRAND_NEGATIVE}, {_SINGLE_PRODUCT_NEGATIVE}, {context['negative']}, {focus['negative']}.",
    ]
    prompt = "\n".join(line for line in lines if line)
    return prompt[:LIBLIBAI_MAX_PROMPT_LENGTH].strip()


def _storyboard_image_with_hisense_brand_stamp(image_bytes: bytes) -> bytes:
    if not STORYBOARD_IMAGE_BRAND_STAMP_ENABLED or not image_bytes:
        return image_bytes
    try:
        from PIL import Image, ImageDraw, ImageFont, ImageOps

        image = Image.open(io.BytesIO(image_bytes))
        image = ImageOps.exif_transpose(image).convert("RGBA")
        width, height = image.size
        scale = max(0.75, min(width, height) / 720)
        font_size = max(26, int(32 * scale))
        font = None
        for font_path in (
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
            "/usr/share/fonts/dejavu/DejaVuSans-Bold.ttf",
            "C:/Windows/Fonts/arialbd.ttf",
            "C:/Windows/Fonts/arial.ttf",
        ):
            try:
                font = ImageFont.truetype(font_path, font_size)
                break
            except Exception:
                continue
        if font is None:
            font = ImageFont.load_default()

        overlay = Image.new("RGBA", image.size, (0, 0, 0, 0))
        draw = ImageDraw.Draw(overlay)
        text = "Hisense"
        text_bbox = draw.textbbox((0, 0), text, font=font)
        text_width = text_bbox[2] - text_bbox[0]
        text_height = text_bbox[3] - text_bbox[1]
        pad_x = int(18 * scale)
        pad_y = int(10 * scale)
        margin = int(22 * scale)
        box_width = text_width + pad_x * 2
        box_height = text_height + pad_y * 2
        x0 = margin
        y0 = height - box_height - margin
        x1 = x0 + box_width
        y1 = y0 + box_height
        draw.rounded_rectangle(
            (x0, y0, x1, y1),
            radius=int(10 * scale),
            fill=(255, 255, 255, 225),
        )
        draw.text(
            (x0 + pad_x, y0 + pad_y - text_bbox[1]),
            text,
            font=font,
            fill=(0, 129, 120, 255),
        )
        composed = Image.alpha_composite(image, overlay).convert("RGB")
        output = io.BytesIO()
        composed.save(output, format="PNG", optimize=True)
        return output.getvalue()
    except Exception:
        return image_bytes


def _start_liblibai_image(
    prompt,
    script_job_id,
    variant_index,
    shot_index,
    reference_image_bytes: bytes | None = None,
):
    client = LiblibAIClient(_liblibai_image_config())
    image_bytes, metadata = client.generate_image(prompt, reference_image_bytes=reference_image_bytes)
    image_bytes = _storyboard_image_with_hisense_brand_stamp(image_bytes)
    image_key = _nova_canvas_image_key(script_job_id, variant_index, shot_index)
    image_uri = STORAGE.write_file_bytes(image_key, image_bytes, content_type="image/png")
    return image_key, image_uri, metadata


def _start_nova_canvas_image(
    prompt,
    script_job_id,
    variant_index,
    shot_index,
    category="",
    model="",
    reference_image_bytes: bytes | None = None,
):
    """Generate a storyboard reference image.

    Strategy:
    1. Try Bedrock image model (Nova Canvas / Titan Image Generator) if configured.
    2. Fall back to Pollinations.ai (free, no auth required) if Bedrock fails.
    """
    import time as _time
    import requests as _requests

    seed = random.randint(0, 858993459)
    image_bytes = None
    failures = []

    # --- Attempt 1: Bedrock image model ---
    if NOVA_CANVAS_MODEL_ID and NOVA_CANVAS_MODEL_ID != "none":
        try:
            from botocore.config import Config

            body = _bedrock_image_request_body(
                prompt,
                seed,
                category=category,
                model=model,
                reference_image_bytes=reference_image_bytes,
            )
            client = boto3.client(
                "bedrock-runtime",
                region_name=NOVA_CANVAS_AWS_REGION,
                config=Config(connect_timeout=5, read_timeout=180, retries={"max_attempts": 2, "mode": "adaptive"}),
            )
            response = client.invoke_model(
                modelId=NOVA_CANVAS_MODEL_ID,
                body=json.dumps(body).encode("utf-8"),
                contentType="application/json",
                accept="application/json",
            )
            payload = json.loads(response["body"].read())
            image_bytes = _decode_bedrock_image_payload(payload)
            if not image_bytes:
                failures.append(f"Bedrock returned no image: {str(payload)[:300]}")
        except Exception as exc:
            failures.append(f"Bedrock {NOVA_CANVAS_MODEL_ID} in {NOVA_CANVAS_AWS_REGION}: {exc}")

    # --- Attempt 2: Pollinations.ai (free, always available from public internet) ---
    if image_bytes is None and reference_image_bytes:
        detail = " | ".join(failures[-3:]) if failures else "no image provider configured"
        raise RuntimeError(f"产品图参考分镜图生成失败：{detail}")

    if image_bytes is None:
        encoded_prompt = urllib.parse.quote(str(prompt or "product photo")[:500])
        pollinations_url = (
            f"https://image.pollinations.ai/prompt/{encoded_prompt}"
            f"?width=1280&height=720&seed={seed}&nologo=true"
        )
        for attempt in range(2):
            try:
                resp = _requests.get(pollinations_url, timeout=90, verify=False)
                if resp.status_code == 200 and len(resp.content) > 1000:
                    image_bytes = resp.content
                    break
                failures.append(f"Pollinations HTTP {resp.status_code}")
            except Exception as exc:
                failures.append(f"Pollinations: {exc}")
            if attempt < 1:
                _time.sleep(3)

    if image_bytes is None:
        detail = " | ".join(failures[-3:]) if failures else "no image provider configured"
        raise RuntimeError(f"Storyboard image generation failed: {detail}")

    # --- Attempt 3: Generate a simple placeholder with text overlay ---
    if image_bytes is None:
        try:
            from PIL import Image as _PILImage, ImageDraw as _PILDraw, ImageFont as _PILFont
            img = _PILImage.new("RGB", (1280, 720), color=(245, 245, 247))
            draw = _PILDraw.Draw(img)
            # Draw centered text
            short_text = str(prompt or "Storyboard")[:80]
            try:
                font = _PILFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 28)
            except Exception:
                font = _PILFont.load_default()
            draw.text((80, 320), short_text, fill=(100, 100, 100), font=font)
            draw.text((80, 370), f"[Placeholder - image service unavailable]", fill=(180, 180, 180), font=font)
            buf = io.BytesIO()
            img.save(buf, format="PNG")
            image_bytes = buf.getvalue()
        except Exception:
            pass

    if not image_bytes:
        raise RuntimeError("分镜图生成失败：所有图像生成方式均不可用。请检查网络连接或稍后重试。")

    image_bytes = _storyboard_image_with_hisense_brand_stamp(image_bytes)
    image_key = _nova_canvas_image_key(script_job_id, variant_index, shot_index)
    image_uri = STORAGE.write_file_bytes(image_key, image_bytes, content_type="image/png")
    return image_key, image_uri, seed


def _start_storyboard_image(
    prompt,
    script_job_id,
    variant_index,
    shot_index,
    category="",
    model="",
    reference_image_bytes: bytes | None = None,
):
    if _image_provider_name() == "liblibai":
        try:
            image_key, image_uri, metadata = _start_liblibai_image(
                prompt,
                script_job_id,
                variant_index,
                shot_index,
                reference_image_bytes=reference_image_bytes,
            )
            return image_key, image_uri, metadata.get("seed"), metadata
        except LiblibAIError:
            raise
    image_key, image_uri, seed = _start_nova_canvas_image(
        prompt,
        script_job_id,
        variant_index,
        shot_index,
        category=category,
        model=model,
        reference_image_bytes=reference_image_bytes,
    )
    return image_key, image_uri, seed, {}


def _run_storyboard_image_job(
    image_job_id: str,
    generation_prompt: str,
    script_job_id: str,
    variant_index: int,
    shot_index: int,
    category: str = "",
    model: str = "",
    reference_image_bytes: bytes | None = None,
):
    attempts = STORYBOARD_IMAGE_RETRY_COUNT + 1
    last_error = ""
    for attempt in range(attempts):
        _update_nova_canvas_job(
            image_job_id,
            status="running",
            failure_message="" if attempt == 0 else "第三方生成服务繁忙，正在自动重试...",
            attempt=attempt + 1,
        )
        try:
            image_key, image_uri, seed, provider_meta = _start_storyboard_image(
                generation_prompt,
                script_job_id,
                variant_index,
                shot_index,
                category=category,
                model=model,
                reference_image_bytes=reference_image_bytes,
            )
            _update_nova_canvas_job(
                image_job_id,
                status="succeeded",
                failure_message="",
                image_key=image_key,
                image_uri=image_uri,
                seed=seed,
                external_job_id=str((provider_meta or {}).get("generate_uuid") or ""),
                remote_image_url=str((provider_meta or {}).get("image_url") or ""),
                reference_image_url=str((provider_meta or {}).get("reference_image_url") or ""),
                image_generation_mode=str((provider_meta or {}).get("mode") or ""),
                reference_control_type=str((provider_meta or {}).get("control_type") or ""),
                provider_warning=str((provider_meta or {}).get("fallback_error") or ""),
            )
            return
        except Exception as exc:
            last_error = str(exc)
            if attempt < attempts - 1 and _is_transient_storyboard_image_error(last_error):
                _update_nova_canvas_job(
                    image_job_id,
                    status="queued",
                    failure_message="LibLibAI 服务繁忙，已进入自动重试队列。",
                    attempt=attempt + 1,
                )
                time.sleep(STORYBOARD_IMAGE_RETRY_BACKOFF_SECONDS * (attempt + 1))
                continue
            break

    _update_nova_canvas_job(
        image_job_id,
        status="failed",
        failure_message=_friendly_storyboard_image_error(last_error),
    )


def _safe_ascii_slug(text):
    raw = str(text or "").strip()
    if "微波" in raw:
        raw = "microwave"
    elif "烤箱" in raw:
        raw = "oven"
    elif "空气" in raw or "炸锅" in raw:
        raw = "air_fryer"
    slug = re.sub(r"\s+", "_", raw)
    slug = re.sub(r"[^A-Za-z0-9_.-]+", "_", slug).strip("_")
    return slug[:60] if slug else "unknown"


def _s3_output_base_uri():
    if NOVA_REEL_OUTPUT_S3_URI:
        return NOVA_REEL_OUTPUT_S3_URI
    bucket = os.getenv("S3_BUCKET") or os.getenv("APP_S3_BUCKET")
    prefix = os.getenv("S3_PREFIX", "").strip("/")
    if not bucket:
        return ""
    parts = [f"s3://{bucket}"]
    if prefix:
        parts.append(prefix)
    parts.append("nova-reel-poc")
    return "/".join(parts)


def _category_en(category):
    value = str(category or "")
    if "微波" in value:
        return "microwave oven"
    if "烤箱" in value:
        return "built-in oven"
    if "空气" in value or "炸锅" in value:
        return "air fryer"
    return "home appliance"


def _extract_variant_video_prompt(content):
    text = str(content or "").strip()
    if not text:
        return ""
    patterns = [
        r"整体AI视频生成Prompt\s*（English）\s*[:：]\s*(.*?)(?:\n\s*Negative Prompt|\n\s*Recommended Settings|$)",
        r"整体AI视频生成Prompt\s*\(English\)\s*[:：]\s*(.*?)(?:\n\s*Negative Prompt|\n\s*Recommended Settings|$)",
        r"Overall AI Video Generation Prompt\s*[:：]\s*(.*?)(?:\n\s*Negative Prompt|\n\s*Recommended Settings|$)",
    ]
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE | re.DOTALL)
        if match:
            prompt = re.sub(r"^\s*[-•]\s*", "", match.group(1).strip(), flags=re.MULTILINE)
            prompt = re.sub(r"\s+", " ", prompt).strip()
            if prompt:
                return prompt[:500]
    return ""


def _build_variant_nova_reel_prompt(variant, category, model, selected_features):
    extracted = _extract_variant_video_prompt((variant or {}).get("content", ""))
    if extracted:
        return extracted
    feature_text = "; ".join([str(x).strip() for x in selected_features if str(x).strip()])
    if not feature_text:
        feature_text = "product benefits and lifestyle usage"
    return (
        f"Six-second premium e-commerce reference video for a Hisense {_category_en(category)}, model {model}. "
        f"Show a realistic product-focused scene based on this script variant, highlighting: {feature_text}. "
        "Modern bright kitchen, cinematic soft daylight, smooth camera movement, realistic product proportions, "
        "no text overlay, no logo distortion, no extra brands."
    )[:500]


def _nova_reel_job_output_uri(category, model):
    base_uri = _s3_output_base_uri()
    if not base_uri:
        return ""
    stamp = dt.datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
    return f"{base_uri.rstrip('/')}/{stamp}_{_safe_ascii_slug(category)}_{_safe_ascii_slug(model)}_{uuid.uuid4().hex[:8]}/"


def _start_nova_reel_job(category, model, prompt, duration_seconds=6):
    output_s3_uri = _nova_reel_job_output_uri(category, model)
    if not output_s3_uri:
        raise RuntimeError("未配置 Nova Reel 输出 S3。请设置 STORAGE_BACKEND=s3/S3_BUCKET，或设置 NOVA_REEL_OUTPUT_S3_URI。")
    from botocore.config import Config

    client = boto3.client(
        "bedrock-runtime",
        region_name=NOVA_REEL_AWS_REGION,
        config=Config(connect_timeout=5, read_timeout=20, retries={"max_attempts": 2}),
    )
    response = client.start_async_invoke(
        modelId=NOVA_REEL_MODEL_ID,
        modelInput={
            "taskType": "TEXT_VIDEO",
            "textToVideoParams": {"text": prompt},
            "videoGenerationConfig": {
                "durationSeconds": int(duration_seconds),
                "fps": 24,
                "dimension": "1280x720",
                "seed": random.randint(0, 2147483646),
            },
        },
        outputDataConfig={"s3OutputDataConfig": {"s3Uri": output_s3_uri}},
        clientRequestToken=str(uuid.uuid4()),
    )
    return response["invocationArn"], output_s3_uri


def _storyboard_rows_from_variant(content: str) -> list[dict]:
    table_lines, _ = _extract_first_md_table(content)
    df = _parse_md_table_to_df(table_lines)
    if df.empty:
        return []
    body = _script_body_rows(df)
    rows = []
    for index, row in body.reset_index(drop=True).iterrows():
        segment = str(row.get("镜头分段", "") or "").strip()
        if "总时长" in segment or "total" in segment.lower():
            continue
        if not any(str(row.get(column, "") or "").strip() for column in TABLE_COLUMNS[1:-1]):
            continue
        duration = _duration_seconds(row.get("时长", ""))
        visual_prompt = str(row.get("画面示意&表现手法", "") or "").strip()
        selling_point = str(row.get("功能卖点（英文）", "") or "").strip()
        movement = str(row.get("镜头运动&运动轨迹", "") or "").strip()
        rows.append(
            {
                "row_index": int(index),
                "shot_label": segment or f"镜头{index + 1:02d}",
                "segment": segment,
                "feature": selling_point,
                "visual_prompt": visual_prompt,
                "method": visual_prompt,
                "scene_note": visual_prompt,
                "story_note": "",
                "selling_point": selling_point,
                "subtitle": str(row.get("字幕-显示卖点名及描述（英文）", "") or "").strip(),
                "angle": movement,
                "movement": movement,
                "duration": duration,
            }
        )
    return rows


def _compose_manual_shot_prompt(group_rows: list[dict], category: str, model: str, shot_index: int, shot_count: int) -> str:
    detail_parts = []
    for row in group_rows:
        detail_parts.append(
            " / ".join(
                item
                for item in (
                    row.get("shot_label"),
                    row.get("segment"),
                    row.get("feature"),
                    row.get("selling_point"),
                    row.get("visual_prompt"),
                    row.get("method"),
                    row.get("movement"),
                    row.get("scene_note"),
                    row.get("story_note"),
                    row.get("subtitle"),
                )
                if item
            )
        )
    details = " | ".join(part for part in detail_parts if part)
    prompt = (
        f"Six-second product motion segment {shot_index + 1}/{shot_count} for Hisense {_category_en(category)} model {model}. "
        f"Use supplied product image only for identity: shape, finish, logo, panel layout, proportions. Create a new scene from this script segment, "
        f"animate the product or user interaction, cinematic soft light, smooth camera. Keep first/last frames transition-friendly for smart editing. "
        f"No text overlays, no competitor brands. Segment details: {details or 'product-focused lifestyle proof shot'}."
    )
    return re.sub(r"\s+", " ", prompt).strip()[:512]


def _image_format_from_key(key: str) -> str:
    return "jpeg" if str(key or "").lower().endswith((".jpg", ".jpeg")) else "png"


def _image_format_from_bytes(data: bytes, key: str) -> str:
    try:
        from PIL import Image

        image = Image.open(io.BytesIO(data))
        image_format = str(image.format or "").upper()
        if image_format in {"JPEG", "JPG"}:
            return "jpeg"
        if image_format == "PNG":
            return "png"
    except Exception:
        pass
    return _image_format_from_key(key)


def _image_payload_from_storage_key(key: str) -> dict | None:
    if not key:
        return None
    data = STORAGE.read_file_bytes(key)
    if not data:
        return None
    return {
        "format": _image_format_from_bytes(data, key),
        "source": {"bytes": base64.b64encode(data).decode("utf-8")},
    }


def _latest_canvas_image_key_by_shot(script_job_id: str, variant_index: int) -> dict[int, str]:
    by_shot = {}
    for item in _load_nova_canvas_jobs():
        if item.get("status") != "succeeded" or not item.get("image_key"):
            continue
        if str(item.get("script_job_id") or "") != str(script_job_id):
            continue
        if int(item.get("variant_index", -1)) != int(variant_index):
            continue
        shot_index = int(item.get("shot_index", -1))
        if shot_index >= 0 and shot_index not in by_shot:
            by_shot[shot_index] = item.get("image_key")
    return by_shot


def _build_storyboard_manual_shots(script_job: dict, variant_index: int, product_image_id: str = "") -> tuple[list[dict], int, dict | None]:
    variants = script_job.get("variants") or []
    if variant_index >= len(variants):
        raise HTTPException(status_code=400, detail="Script variant not found.")
    request_payload = script_job.get("request") or {}
    variant = variants[variant_index]
    rows = _storyboard_rows_from_variant(variant.get("content", ""))
    if not rows:
        raise HTTPException(status_code=400, detail="当前方案未解析到分段脚本表格，无法智能转场合成整段视频。")

    total_duration = sum(row.get("duration") or 0 for row in rows)
    if total_duration <= 0:
        total_duration = int(request_payload.get("expected_duration") or 30)
    rounded_duration = max(12, ((int(total_duration) + 5) // 6) * 6)
    if rounded_duration > 120:
        raise HTTPException(status_code=400, detail="Nova Reel 手动多镜头视频最长支持 120 秒，请缩短脚本时长。")
    shot_count = max(2, min(20, rounded_duration // 6))

    groups = [[] for _ in range(shot_count)]
    elapsed = 0
    for row in rows:
        target = min(shot_count - 1, max(0, int(elapsed // 6)))
        groups[target].append(row)
        elapsed += max(1, int(row.get("duration") or 0))
    for index, group in enumerate(groups):
        if not group:
            groups[index] = [rows[min(index, len(rows) - 1)]]

    product_asset = _product_image_by_id(product_image_id, script_job_id=script_job.get("id", ""))
    fallback_image_key = (product_asset or {}).get("normalized_key", "")
    canvas_keys = _latest_canvas_image_key_by_shot(script_job.get("id", ""), variant_index)
    shots = []
    for index, group in enumerate(groups):
        image_key = ""
        for row in group:
            image_key = canvas_keys.get(int(row.get("row_index", -1))) or ""
            if image_key:
                break
        if not image_key:
            image_key = fallback_image_key
        shot = {
            "text": _compose_manual_shot_prompt(
                group,
                request_payload.get("category", ""),
                request_payload.get("model", ""),
                index,
                shot_count,
            )
        }
        if image_key:
            shot["image"] = _image_payload_from_storage_key(image_key)
            shot["source_image_key"] = image_key
        shots.append(shot)
    return shots, rounded_duration, product_asset


def _build_storyboard_single_shot(script_job: dict, variant_index: int, shot_index: int, product_image_id: str = "") -> tuple[list[dict], int, dict | None]:
    variants = script_job.get("variants") or []
    if variant_index >= len(variants):
        raise HTTPException(status_code=400, detail="Script variant not found.")
    request_payload = script_job.get("request") or {}
    variant = variants[variant_index]
    rows = _storyboard_rows_from_variant(variant.get("content", ""))
    if not rows:
        raise HTTPException(status_code=400, detail="当前方案未解析到分镜脚本表格，无法生成分镜视频。")
    if shot_index < 0 or shot_index >= len(rows):
        raise HTTPException(status_code=400, detail="分镜不存在，请刷新脚本结果后重试。")

    row = rows[shot_index]
    product_asset = _product_image_by_id(product_image_id, script_job_id=script_job.get("id", ""))
    fallback_image_key = (product_asset or {}).get("normalized_key", "")
    image_key = _latest_canvas_image_key_by_shot(script_job.get("id", ""), variant_index).get(shot_index) or fallback_image_key
    shot = {
        "text": _compose_manual_shot_prompt(
            [row],
            request_payload.get("category", ""),
            request_payload.get("model", ""),
            0,
            1,
        )
    }
    if image_key:
        shot["image"] = _image_payload_from_storage_key(image_key)
        shot["source_image_key"] = image_key
    return [shot], 6, product_asset


def _start_storyboard_video_job(category, model, manual_shots: list[dict]):
    output_s3_uri = _nova_reel_job_output_uri(category, model)
    if not output_s3_uri:
        raise RuntimeError("未配置 Nova Reel 输出 S3。请设置 STORAGE_BACKEND=s3/S3_BUCKET，或设置 NOVA_REEL_OUTPUT_S3_URI。")
    from botocore.config import Config

    payload_shots = []
    for shot in manual_shots:
        payload = {"text": str(shot.get("text") or "")[:512]}
        if shot.get("image"):
            payload["image"] = shot["image"]
        payload_shots.append(payload)
    client = boto3.client(
        "bedrock-runtime",
        region_name=NOVA_REEL_AWS_REGION,
        config=Config(connect_timeout=5, read_timeout=20, retries={"max_attempts": 2}),
    )
    response = client.start_async_invoke(
        modelId=NOVA_REEL_MODEL_ID,
        modelInput={
            "taskType": "MULTI_SHOT_MANUAL",
            "multiShotManualParams": {"shots": payload_shots},
            "videoGenerationConfig": {
                "fps": 24,
                "dimension": "1280x720",
                "seed": random.randint(0, 2147483646),
            },
        },
        outputDataConfig={"s3OutputDataConfig": {"s3Uri": output_s3_uri}},
        clientRequestToken=str(uuid.uuid4()),
    )
    return response["invocationArn"], output_s3_uri


def _query_nova_reel_job(invocation_arn):
    from botocore.config import Config

    client = boto3.client(
        "bedrock-runtime",
        region_name=NOVA_REEL_AWS_REGION,
        config=Config(connect_timeout=5, read_timeout=20, retries={"max_attempts": 2}),
    )
    return client.get_async_invoke(invocationArn=invocation_arn)


def _video_uri_from_bedrock_job(job):
    output_uri = (
        ((job or {}).get("outputDataConfig") or {})
        .get("s3OutputDataConfig", {})
        .get("s3Uri", "")
    )
    if not output_uri:
        output_uri = (job or {}).get("output_s3_uri", "")
    return f"{output_uri.rstrip('/')}/output.mp4" if output_uri else ""


def _presigned_url_for_s3_uri(s3_uri, expires_in=3600):
    if not str(s3_uri or "").startswith("s3://"):
        return ""
    parsed = urllib.parse.urlparse(s3_uri)
    bucket = parsed.netloc
    key = parsed.path.lstrip("/")
    if not bucket or not key:
        return ""
    try:
        client = boto3.client("s3", region_name=os.getenv("AWS_REGION") or os.getenv("AWS_DEFAULT_REGION"))
        return client.generate_presigned_url(
            "get_object",
            Params={"Bucket": bucket, "Key": key},
            ExpiresIn=expires_in,
        )
    except Exception:
        return ""


def _public_nova_reel_job(job):
    public = dict(job or {})
    public["preview_url"] = _presigned_url_for_s3_uri(public.get("video_s3_uri", ""))
    return public


def _public_nova_canvas_job(job):
    public = dict(job or {})
    preview_url = _presigned_url_for_s3_uri(public.get("image_uri", ""), expires_in=3600)
    if not preview_url and public.get("image_key"):
        preview_url = f"/api/nova-canvas/images/{public.get('id')}"
    public["preview_url"] = preview_url
    return public


def _repair_to_expected_table(
    original_content: str,
    req: GenerateRequest,
    features: list[dict],
    quality_issues: list[str] | None = None,
    script_direction: str = "",
) -> str:
    feature_lines = _feature_catalog_lines(features)
    duration_guidance = _duration_structure_guidance(req.expected_duration, req, features)
    quality_guidance = _script_quality_guidance(req, features)
    direction = script_direction or (_selected_script_directions(req)[0] if len(_selected_script_directions(req)) == 1 else "场景化/生活方式型")
    direction_guidance = _direction_specific_guidance(direction)
    issue_block = ""
    if quality_issues:
        issue_block = "本次必须修复的质量问题：\n" + "\n".join(f"- {item}" for item in quality_issues)
    repair_prompt = f"""
请把下面这段视频脚本内容改写成固定字段的 Markdown 表格。

硬性要求：
1. 第一行必须逐字等于：
{TABLE_HEADER_LINE}
2. 第二行必须逐字等于：
{TABLE_SEPARATOR_LINE}
3. 后续每一行都必须有且只有 {len(TABLE_COLUMNS)} 个字段，字段顺序不得变更、不得新增、不得删除。
4. 字段必须保持为：{", ".join(TABLE_COLUMNS)}
5. 最后一行必须是“总时长”统计；“镜头分段”列写“总时长”，“时长”列写总秒数。
6. 只输出表格，不要输出整段 AI 视频生成 Prompt、Negative Prompt、Recommended Settings 或解释。
7. 旁白（英文）和字幕-显示卖点名及描述（英文）两列必须是英文，其余列以中文为主。
8. 不要编造产品卖点；功能卖点（英文）列必须优先使用卖点库 Feature Name；没有直接功能卖点的开场/收尾行该列留空或写真实产品/场景短语。
9. 少人露出：只允许手部、手臂、背影、越肩视角或生活痕迹，产品和被处理物品必须是主视觉。
10. 必须按外部机构 AI 视频口令稿风格修复：镜头分段参考样例“镜头”字段，写成“时间段 + 具体动作 + 括号内阶段/卖点证据”；画面示意&表现手法合并画面元素、动作、道具和故事推进；镜头运动&运动轨迹参考样例“镜头运动/运镜轨迹”字段，写清景别、运动路径和方向。
11. 画面示意&表现手法必须能指导分段产品动图片段生成；镜头运动&运动轨迹必须写运动方向/路径；每行都要能通过产品图 + 分段脚本生成对应片段。
12. 表格单元格内禁止使用英文竖线“|”；镜头分段和运动轨迹分隔统一使用中文全角“｜”或中文箭头“→”。
13. 严禁在任何表格单元格中输出 Pain-point opening、Brand closing、Opening、Closing、Hook、Intro、Outro、“开头：”“结尾：”“字幕：”“卖点：”等制作结构标签；发现原文里有这些词必须改写成自然成片文案或留空。

{direction_guidance}

{duration_guidance}

{quality_guidance}

{issue_block}

产品信息：
- 产品品类：{req.category}
- 产品型号：{req.model}
- 期望时长：{req.expected_duration} 秒
- 脚本方向：{direction}
- 核心卖点：
{feature_lines}

原始内容：
{original_content}
""".strip()
    return _strip_code_fences(_call_bedrock(repair_prompt, temperature=0.2, top_p=0.7))


def _build_excel_bytes(job: dict) -> bytes:
    request = job.get("request", {})
    context_snapshot = job.get("context_snapshot") or {}
    buffer = io.BytesIO()
    with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
        pd.DataFrame([request]).to_excel(writer, index=False, sheet_name="配置")
        context_rows = []
        for asset in context_snapshot.get("competitor_assets", []) or []:
            context_rows.append(
                {
                    "类型": "竞品素材",
                    "标题": asset.get("title", ""),
                    "来源": asset.get("platform") or asset.get("source_type", ""),
                    "品牌": asset.get("brand", ""),
                    "链接": asset.get("source_url", ""),
                    "状态": asset.get("review_status", ""),
                    "备注": asset.get("ai_analysis", ""),
                }
            )
        for hotspot in context_snapshot.get("hotspots", []) or []:
            context_rows.append(
                {
                    "类型": "行业热点",
                    "标题": hotspot.get("title", ""),
                    "来源": hotspot.get("source_name", ""),
                    "品牌": "",
                    "链接": hotspot.get("source_url", ""),
                    "状态": hotspot.get("status", ""),
                    "备注": f"{hotspot.get('valid_from', '')} - {hotspot.get('valid_to', '')}",
                }
            )
        if context_rows:
            pd.DataFrame(context_rows).to_excel(writer, index=False, sheet_name="引用上下文")
        appendix_rows = []
        for index, variant in enumerate(job.get("variants", []), start=1):
            table_lines, remainder = _extract_first_md_table(_strip_overall_video_prompt_sections(variant.get("content", "")))
            df = _parse_md_table_to_df(table_lines)
            if df.empty:
                df = pd.DataFrame(columns=TABLE_COLUMNS)
            df.to_excel(writer, index=False, sheet_name=f"方案{index}")
            appendix_rows.append(
                {
                    "方案": variant.get("name", f"方案{index}"),
                    "方案标签": variant.get("label", ""),
                    "表格后附加内容": remainder,
                }
            )
        pd.DataFrame(appendix_rows).to_excel(writer, index=False, sheet_name="附加信息")
    return buffer.getvalue()


def _run_generation(job_id: str):
    jobs = _load_jobs()
    job = next((item for item in jobs if item.get("id") == job_id), None)
    if not job:
        return
    try:
        req = GenerateRequest(**job["request"])
        df = _load_products()
        if df.empty:
            raise RuntimeError("产品卖点库为空，请先上传 Excel。")
        features = _feature_rows(df, req.model, req.selected_features)
        context_snapshot = job.get("context_snapshot") or _script_context_snapshot(req)
        variants = []
        _update_job(job_id, status="running", progress=8, current_step="已读取产品卖点")
        total = max(1, req.variant_count)
        for i in range(total):
            script_direction = _script_direction_for_variant(req, i)
            prompt = _build_prompt(req, features, i, context_snapshot=context_snapshot)
            _update_job(job_id, progress=int((i / total) * 80) + 10, current_step=f"生成方案 {i + 1}/{total}")
            content = _strip_overall_video_prompt_sections(_strip_code_fences(_call_bedrock(prompt)))
            if not _has_expected_table(content):
                retry_prompt = prompt + "\n\n补充要求：输出必须完整，不要截断；若篇幅过长请压缩行文但保留完整表格与总时长行。"
                retry_content = _strip_overall_video_prompt_sections(
                    _strip_code_fences(_call_bedrock(retry_prompt, temperature=0.3, top_p=0.8))
                )
                if _has_expected_table(retry_content):
                    content = retry_content
            if not _has_expected_table(content):
                _update_job(job_id, progress=int((i / total) * 80) + 15, current_step=f"修复方案 {i + 1} 为表格格式")
                repaired = _repair_to_expected_table(content, req, features, script_direction=script_direction)
                if _has_expected_table(repaired):
                    content = _strip_overall_video_prompt_sections(repaired)
            if _has_expected_table(content) and not _has_rich_duration_structure(content, req.expected_duration):
                _update_job(job_id, progress=int((i / total) * 80) + 18, current_step=f"细化方案 {i + 1} 的分段和时长")
                repaired = _repair_to_expected_table(content, req, features, script_direction=script_direction)
                if _has_expected_table(repaired):
                    content = _strip_overall_video_prompt_sections(repaired)
            if _has_expected_table(content):
                quality_issues = _script_quality_issues(content, req, features, script_direction=script_direction)
                if quality_issues:
                    _update_job(job_id, progress=int((i / total) * 80) + 22, current_step=f"优化方案 {i + 1} 的品类场景和卖点表达")
                    repaired = _repair_to_expected_table(content, req, features, quality_issues=quality_issues, script_direction=script_direction)
                    if _has_expected_table(repaired):
                        content = _strip_overall_video_prompt_sections(repaired)
            variants.append({"name": f"方案{i + 1}", "label": script_direction, "content": content.strip()})
        _update_job(
            job_id,
            status="succeeded",
            progress=100,
            current_step="已完成",
            variants=variants,
            context_snapshot=context_snapshot,
            error_message="",
            completed_at=dt.datetime.utcnow().isoformat(timespec="seconds") + "Z",
        )
    except Exception as exc:
        _update_job(job_id, status="failed", progress=100, current_step="失败", error_message=str(exc), completed_at=dt.datetime.utcnow().isoformat(timespec="seconds") + "Z")


def _asset_evidence_lines(assets):
    lines = []
    for index, asset in enumerate(assets, start=1):
        metadata = asset.get("metadata") or {}
        videos = [item for item in asset.get("media", []) or [] if item.get("media_type") == "video"]
        media_hint = f"{len(videos)} 条视频素材" if videos else f"{len(asset.get('media', []) or [])} 个非视频媒体项"
        tags = "、".join(asset.get("ai_tags") or [])
        platform = asset.get("platform") or asset.get("source_type") or "Unknown"
        source_id = f"ASIN: {asset.get('asin')} / Domain: {asset.get('amazon_domain')}" if asset.get("asin") else f"Platform: {platform} / Source: {asset.get('source_type') or '-'}"
        review_label = {
            "featured": "精选案例",
            "approved": "已通过审核",
            "auto_collected": "待审核",
            "needs_review": "需复核",
            "rejected": "不采用",
        }.get(_asset_review_status(asset), _asset_review_status(asset))
        lines.append(
            "\n".join(
                [
                    f"[{index}] {asset.get('brand') or 'Unknown'} | {asset.get('title') or 'Untitled'}",
                    f"- {source_id}",
                    f"- Business status: {review_label}",
                    f"- Link: {asset.get('source_url') or ''}",
                    f"- Score: {asset.get('quality_score') or 0} / Rating: {metadata.get('rating') or '-'} / Reviews: {metadata.get('reviews') or '-'} / Media: {media_hint}",
                    f"- Tags: {tags or '无'}",
                    f"- Analysis: {asset.get('ai_analysis') or ''}",
                    f"- Copy excerpt: {str(asset.get('original_copy') or '')[:700]}",
                ]
            )
        )
    return "\n\n".join(lines)


def _build_competitor_research_prompt(req: CompetitorResearchRequest, assets: list[dict]) -> str:
    evidence = _asset_evidence_lines(assets)
    return f"""
你是海外电商竞品视频素材分析顾问。请只基于下方证据回答业务问题，不要编造平台数据、播放量、投放效果或竞品参数。

业务问题：
{req.question}

筛选条件：
- 品类：{req.category or "未限定"}
- 市场：{req.target_market or "未限定"}
- 平台/来源：{req.platform or "未限定"} / {req.source or "未限定"}

证据素材：
{evidence}

输出要求：
1. 用中文输出一份调研报告。
2. 每个关键结论后面都必须用 [1]、[2] 这样的证据编号标注来源。
3. 报告结构包括：核心结论、素材套路归纳、可借鉴拍摄方向、风险与限制、证据清单。
4. 如果证据不足，请明确说明，不要补充没有证据支持的判断。
5. 强调这些素材只保存链接和结构化分析，未保存竞品视频原片。
""".strip()


def _fallback_competitor_report(req: CompetitorResearchRequest, assets: list[dict]) -> str:
    if not assets:
        return (
            "## 核心结论\n"
            "当前素材库没有检索到可支撑该问题的证据。建议先通过 Rainforest、官方 API 或人工样本补充竞品素材后再生成报告。\n\n"
            "## 风险与限制\n"
            "系统不会编造竞品素材结论，也不会下载或保存竞品视频原片。"
        )
    conclusion = []
    directions = []
    evidence = []
    for index, asset in enumerate(assets, start=1):
        metadata = asset.get("metadata") or {}
        video_count = metadata.get("video_count") or 0
        tags = "、".join(asset.get("ai_tags") or [])
        conclusion.append(
            f"- {asset.get('brand') or '竞品'} 素材围绕“{asset.get('title') or '商品页'}”展开，"
            f"质量分 {asset.get('quality_score') or 0}，识别到 {video_count} 条视频素材。[{index}]"
        )
        if tags:
            directions.append(f"- 可参考 {asset.get('brand') or '竞品'} 的标签方向：{tags}。[{index}]")
        evidence.append(
            f"- [{index}] {metadata.get('platform_content_id') or asset.get('asin') or asset.get('id') or '-'} | {asset.get('platform') or '-'} | {asset.get('brand') or '-'} | "
            f"{asset.get('source_url') or ''} | 抓取时间：{asset.get('collected_at') or '-'}"
        )
    return "\n\n".join(
        [
            "## 核心结论",
            "\n".join(conclusion),
            "## 素材套路归纳",
            "\n".join(directions or ["- 当前证据以已入库素材链接、标题、图片/视频线索和结构化分析为主，仍需继续扩充样本。"]),
            "## 可借鉴拍摄方向",
            "- 优先复盘含视频或高质量社媒帖的素材，拆解开场利益点、场景演示、功能特写和收尾 CTA。",
            "## 风险与限制",
            "- 本报告仅基于已入库素材证据，不代表真实投放效果；系统未下载或保存竞品视频原片。",
            "## 证据清单",
            "\n".join(evidence),
        ]
    )


DEEP_ANALYSIS_SYSTEM_PROMPT = """
你是海外家电行业竞品短视频素材分析专家。你只能基于用户提供的入库证据、公开元数据、标题、文案、缩略图/嵌入链接字段做分析；不得声称已经观看、下载或保存竞品视频原片，不得编造播放量、评论、投放效果或未提供的画面细节。
输出必须是可解析 JSON，面向内容策划和短视频脚本生成联动使用。
""".strip()


def _asset_payload_for_deep_analysis(asset: dict) -> dict:
    metadata = asset.get("metadata") if isinstance(asset.get("metadata"), dict) else {}
    media = []
    for item in (asset.get("media") or [])[:8]:
        if isinstance(item, dict):
            media.append(
                {
                    "media_type": item.get("media_type") or "",
                    "title": item.get("title") or "",
                    "product_url": item.get("product_url") or "",
                    "thumbnail_url": item.get("thumbnail_url") or "",
                    "rights_status": item.get("rights_status") or "",
                }
            )
    return {
        "id": asset.get("id") or "",
        "platform": asset.get("platform") or asset.get("source_type") or "",
        "source_type": asset.get("source_type") or "",
        "brand": asset.get("brand") or "",
        "category": asset.get("category") or "",
        "title": asset.get("title") or "",
        "source_url": asset.get("source_url") or "",
        "embed_url": asset.get("embed_url") or "",
        "image_url": asset.get("image_url") or "",
        "media_types": sorted(_asset_media_types(asset)),
        "original_copy": str(asset.get("original_copy") or "")[:1200],
        "ai_tags": asset.get("ai_tags") or [],
        "current_analysis": asset.get("ai_analysis") or "",
        "quality_score": asset.get("quality_score") or 0,
        "review_status": asset.get("review_status") or "",
        "rights_status": asset.get("rights_status") or "",
        "metadata": {
            "platform_content_id": metadata.get("platform_content_id") or metadata.get("youtube_video_id") or "",
            "account_name": metadata.get("account_name") or metadata.get("channel_title") or "",
            "published_at": metadata.get("published_at") or "",
            "duration": metadata.get("duration") or "",
            "view_count": metadata.get("view_count") or metadata.get("views") or "",
            "like_count": metadata.get("like_count") or "",
            "comment_count": metadata.get("comment_count") or "",
            "evidence_status": metadata.get("evidence_status") or "",
            "oembed_fetch_error": metadata.get("oembed_fetch_error") or "",
        },
        "media": media,
    }


def _build_competitor_asset_deep_analysis_prompt(asset: dict, req: CompetitorDeepAnalysisRequest) -> str:
    payload = _asset_payload_for_deep_analysis(asset)
    return f"""
请对下方已入库竞品社媒/电商视频素材做深度分析，并输出严格 JSON。

业务上下文：
- 品类：{req.category or payload.get("category") or "未限定"}
- 目标市场：{req.target_market or "未限定"}
- 平台：{req.platform or payload.get("platform") or "未限定"}
- 分析目标：沉淀可用于 Hisense 家电短视频脚本生成的竞品素材洞察。

素材证据 JSON：
{json.dumps(payload, ensure_ascii=False, indent=2)}

输出 JSON Schema：
{{
  "summary": "一句话总结这条素材的内容策略价值，中文，80字以内",
  "hook_analysis": "推断其开场/标题/封面可能承担的吸引逻辑；若证据不足必须说明",
  "creative_pattern": "内容套路归纳，如痛点解决、场景演示、功能证明、测评对比等",
  "scene_structure": ["可复用的镜头/段落结构，3-6条，基于证据，不编造具体画面"],
  "selling_points": ["可借鉴的卖点表达，3-6条"],
  "visual_language": ["可借鉴的视觉/节奏/构图提示，2-5条；证据不足则写限制"],
  "script_opportunities": ["转化为 Hisense 脚本时可借鉴的方向，3-6条"],
  "risk_notes": ["合规、版权、证据不足或平台限制，2-5条"],
  "recommended_tags": ["用于素材库筛选的中文标签，4-8个"],
  "confidence": 0,
  "quality_score": 0
}}

规则：
1. confidence 和 quality_score 为 0-100 整数。
2. 不要输出 Markdown，不要包裹代码块。
3. 不得说“我观看了视频”；只能说“基于标题/描述/链接/缩略图/嵌入证据”。
4. 强调未下载、未保存竞品视频原片。
""".strip()


def _extract_json_object(text: str) -> dict:
    cleaned = _strip_code_fences(text)
    try:
        parsed = json.loads(cleaned)
        return parsed if isinstance(parsed, dict) else {}
    except Exception:
        pass
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start >= 0 and end > start:
        try:
            parsed = json.loads(cleaned[start : end + 1])
            return parsed if isinstance(parsed, dict) else {}
        except Exception:
            return {}
    return {}


def _clean_analysis_list(value, limit=8):
    if isinstance(value, str):
        raw = re.split(r"[\n；;]", value)
    else:
        raw = value or []
    return _clean_list([str(item).strip(" -•\t") for item in raw], limit=limit)


def _clean_analysis_text(value, *, limit=600) -> str:
    if isinstance(value, list):
        return "；".join(_clean_analysis_list(value, limit=8))[:limit]
    if isinstance(value, dict):
        return json.dumps(value, ensure_ascii=False)[:limit]
    return str(value or "").strip()[:limit]


def _analysis_score(value) -> int:
    match = re.search(r"\d+", str(value or ""))
    if not match:
        return 0
    return max(0, min(100, int(match.group(0))))


def _normalize_deep_analysis_payload(payload: dict, asset: dict) -> dict:
    normalized = {
        "summary": _clean_analysis_text(payload.get("summary"), limit=180),
        "hook_analysis": _clean_analysis_text(payload.get("hook_analysis"), limit=500),
        "creative_pattern": _clean_analysis_text(payload.get("creative_pattern"), limit=500),
        "scene_structure": _clean_analysis_list(payload.get("scene_structure"), limit=6),
        "selling_points": _clean_analysis_list(payload.get("selling_points"), limit=6),
        "visual_language": _clean_analysis_list(payload.get("visual_language"), limit=5),
        "script_opportunities": _clean_analysis_list(payload.get("script_opportunities"), limit=6),
        "risk_notes": _clean_analysis_list(payload.get("risk_notes"), limit=5),
        "recommended_tags": _clean_analysis_list(payload.get("recommended_tags"), limit=8),
        "confidence": _analysis_score(payload.get("confidence")),
        "quality_score": _analysis_score(payload.get("quality_score")),
    }
    if not normalized["summary"]:
        normalized["summary"] = f"基于{asset.get('platform') or '社媒'}链接证据完成素材策略分析，仍需结合人工复核确认画面细节。"
    if not normalized["risk_notes"]:
        normalized["risk_notes"] = ["仅保存链接、嵌入地址和公开元数据，未下载或保存竞品视频原片。"]
    return normalized


def _search_research_assets(req: CompetitorResearchRequest, *, q: str = "", category: str = "", review_status: str = "", limit: int = 8) -> list[dict]:
    assets, _ = _search_competitor_assets(
        q=q,
        category=category,
        platform=req.platform,
        source=req.source,
        review_status=review_status,
        limit=limit,
    )
    return assets if review_status else _business_usable_competitor_assets(assets)


def _select_competitor_assets_for_research(req: CompetitorResearchRequest) -> list[dict]:
    limit = max(1, min(int(req.top_k or 8), 20))
    candidates = []
    for status in ("featured", "approved"):
        candidates.extend(_search_research_assets(req, q=req.question, category=req.category, review_status=status, limit=limit))
        unique = _unique_competitor_assets(candidates, limit)
        if len(unique) >= limit:
            return unique
    candidates.extend(_search_research_assets(req, q=req.question, category=req.category, limit=limit * 2))
    unique = _unique_competitor_assets(candidates, limit)
    if len(unique) >= limit:
        return unique
    if req.category:
        candidates.extend(_search_research_assets(req, category=req.category, limit=limit * 2))
        unique = _unique_competitor_assets(candidates, limit)
        if len(unique) >= limit:
            return unique
    if req.platform or req.source:
        candidates.extend(_search_research_assets(req, limit=limit * 2))
    return _unique_competitor_assets(candidates, limit)


def _call_competitor_asset_deep_analysis(asset: dict, req: CompetitorDeepAnalysisRequest) -> dict:
    text = _call_bedrock(
        _build_competitor_asset_deep_analysis_prompt(asset, req),
        temperature=0.2,
        top_p=0.75,
        system_prompt=DEEP_ANALYSIS_SYSTEM_PROMPT,
        max_tokens=2200,
    )
    parsed = _extract_json_object(text)
    if not parsed:
        raise RuntimeError("模型返回内容不是可解析 JSON。")
    return _normalize_deep_analysis_payload(parsed, asset)


def _merge_deep_analysis_into_asset(asset: dict, analysis: dict) -> dict:
    now = _utc_now()
    item = _ensure_asset_admin_defaults(asset)
    deep_analysis = {
        **analysis,
        "analyzed_at": now,
        "model_provider": "aws_bedrock",
        "model_id": BEDROCK_MODEL_ID,
        "analysis_version": "competitor_asset_deep_v1",
        "evidence_basis": "link_thumbnail_oembed_public_metadata_no_raw_video",
    }
    item["deep_analysis"] = deep_analysis
    item["ai_analysis"] = "；".join(
        part
        for part in (
            analysis.get("summary"),
            f"套路：{analysis.get('creative_pattern')}" if analysis.get("creative_pattern") else "",
            f"Hook：{analysis.get('hook_analysis')}" if analysis.get("hook_analysis") else "",
        )
        if part
    )[:900]
    tags = _clean_list(
        list(item.get("ai_tags") or []) + list(analysis.get("recommended_tags") or []) + ["大模型深度分析"],
        limit=30,
    )
    item["ai_tags"] = tags
    if int(analysis.get("quality_score") or 0):
        item["quality_score"] = max(int(item.get("quality_score") or 0), int(analysis.get("quality_score") or 0))
    metadata = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
    metadata = dict(metadata)
    metadata["deep_analysis_status"] = "succeeded"
    metadata["deep_analyzed_at"] = now
    metadata["deep_analysis_model_id"] = BEDROCK_MODEL_ID
    item["metadata"] = metadata
    item["updated_at"] = now
    return _ensure_asset_admin_defaults(item)


def _select_competitor_assets_for_deep_analysis(req: CompetitorDeepAnalysisRequest) -> list[dict]:
    if req.asset_ids:
        assets = _assets_by_ids(req.asset_ids)
    else:
        assets, _ = _search_competitor_assets(
            q=req.q,
            category=req.category,
            platform=req.platform,
            source=req.source,
            media_type=req.media_type,
            review_status=req.review_status,
            rights_status="link_only_no_raw_video",
            sort="quality_desc",
            limit=req.max_assets,
        )
        if not req.review_status:
            assets = _business_usable_competitor_assets(assets)
    if not req.force:
        assets = [asset for asset in assets if not (asset.get("deep_analysis") or {}).get("summary")]
    return assets[: req.max_assets]


def _save_analyzed_competitor_asset(asset: dict):
    with competitor_lock:
        assets = _load_competitor_assets()
        by_id = {str(item.get("id") or ""): dict(item) for item in assets if item.get("id")}
        by_id[str(asset.get("id") or "")] = asset
        _save_competitor_assets(list(by_id.values()))


def _run_competitor_deep_analysis_job(run_id: str):
    run = next((item for item in _load_competitor_analysis_runs() if item.get("id") == run_id), None)
    if not run:
        return
    try:
        req = CompetitorDeepAnalysisRequest(**(run.get("request") or {}))
        assets = _select_competitor_assets_for_deep_analysis(req)
        target_count = len(assets)
        if not target_count:
            _update_competitor_analysis_run(
                run_id,
                status="succeeded",
                progress=100,
                current_step="没有需要分析的素材",
                target_count=0,
                completed_count=0,
                updated_asset_ids=[],
                errors=[],
            )
            return

        _update_competitor_analysis_run(
            run_id,
            status="running",
            progress=5,
            current_step=f"正在并发分析 {target_count} 条素材",
            target_count=target_count,
            completed_count=0,
            updated_asset_ids=[],
            errors=[],
        )
        completed = 0
        updated_ids = []
        errors = []
        workers = max(1, min(int(req.concurrency or 4), 8, target_count))
        with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as executor:
            future_map = {executor.submit(_call_competitor_asset_deep_analysis, asset, req): asset for asset in assets}
            for future in concurrent.futures.as_completed(future_map):
                asset = future_map[future]
                asset_id = str(asset.get("id") or "")
                try:
                    analysis = future.result()
                    updated = _merge_deep_analysis_into_asset(asset, analysis)
                    _save_analyzed_competitor_asset(updated)
                    updated_ids.append(asset_id)
                except Exception as exc:
                    errors.append({"asset_id": asset_id, "title": asset.get("title") or "", "error": str(exc)[:500]})
                completed += 1
                _update_competitor_analysis_run(
                    run_id,
                    progress=min(95, 5 + int(completed * 90 / target_count)),
                    current_step=f"已完成 {completed}/{target_count} 条",
                    completed_count=completed,
                    updated_asset_ids=updated_ids,
                    errors=errors[:20],
                )

        status = "succeeded" if not errors else ("partial" if updated_ids else "failed")
        _update_competitor_analysis_run(
            run_id,
            status=status,
            progress=100,
            current_step="已完成" if status == "succeeded" else "部分完成" if status == "partial" else "失败",
            completed_count=completed,
            updated_asset_ids=updated_ids,
            errors=errors[:20],
            error_message="" if updated_ids else (errors[0]["error"] if errors else "分析失败"),
        )
    except Exception as exc:
        _update_competitor_analysis_run(
            run_id,
            status="failed",
            progress=100,
            current_step="失败",
            error_message=str(exc),
        )


def _run_competitor_research_job(job_id: str):
    job = next((item for item in _load_competitor_research_jobs() if item.get("id") == job_id), None)
    if not job:
        return
    try:
        req = CompetitorResearchRequest(**(job.get("request") or {}))
        _update_competitor_research_job(job_id, status="running", progress=25, current_step="正在按精选/通过状态检索素材证据")
        assets = _select_competitor_assets_for_research(req)
        if not assets:
            report = _fallback_competitor_report(req, [])
        else:
            _update_competitor_research_job(job_id, progress=60, current_step="正在生成调研报告")
            try:
                report = _strip_code_fences(_call_bedrock(_build_competitor_research_prompt(req, assets), temperature=0.25, top_p=0.8))
            except Exception:
                report = _fallback_competitor_report(req, assets)
        _update_competitor_research_job(
            job_id,
            status="succeeded",
            progress=100,
            current_step="已完成",
            report=report,
            evidence=[_public_competitor_asset(item) for item in assets],
            error_message="",
        )
    except Exception as exc:
        _update_competitor_research_job(
            job_id,
            status="failed",
            progress=100,
            current_step="失败",
            error_message=str(exc),
        )


def _strip_admin_script_generation(html: str) -> str:
    html = re.sub(r'\s*<button[^>]*data-page="scripts"[^>]*>.*?</button>', "", html, flags=re.S)
    start = html.find('<section id="pageScripts"')
    if start != -1:
        end = html.find('<section id="pageAssets"', start)
        if end != -1:
            html = html[:start] + html[end:]
    return (
        html.replace("竞品素材 · 行业热点 · 脚本生成", "竞品素材 · 行业热点 · 配置审计")
        .replace("把竞品素材、行业热点和产品卖点沉淀为可追溯的脚本生成上下文。", "把竞品素材和行业热点沉淀为可追溯的内容上下文。")
        .replace("当前可供脚本引用的行业热点。", "当前可供内容策略引用的行业热点。")
    )


def _render_static_html(path: str, base_path: str = ""):
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as handle:
            html = handle.read()
            html = html.replace("__APP_BASE_PATH__", base_path)
            html = html.replace("__BASE_PATH__", base_path)
            return HTMLResponse(
                html,
                headers={"Cache-Control": "no-store, max-age=0"},
            )
    return HTMLResponse(
        "<h1>海外爆款内容引擎 API</h1>",
        headers={"Cache-Control": "no-store, max-age=0"},
    )


@app.get("/", response_class=HTMLResponse)
def index():
    return _render_static_html(os.path.join(static_dir, "index.html"), "")


@app.get(ADMIN_BASE_PATH, response_class=HTMLResponse)
@app.get(f"{ADMIN_BASE_PATH}/", response_class=HTMLResponse)
def admin_index():
    return _render_static_html(os.path.join(static_dir, "admin", "index.html"), ADMIN_BASE_PATH)

@app.get("/healthz")
def healthz():
    return {"ok": True}


@app.get("/api/auth/status")
def auth_status():
    return {"enabled": _access_control_active()}


@app.post("/api/auth/login")
def auth_login(req: AuthLoginRequest):
    if not _access_control_active():
        return {"ok": True}
    if not _current_access_password():
        raise HTTPException(status_code=503, detail="访问密码尚未配置。")
    if not _is_valid_access_token(req.password):
        raise HTTPException(status_code=401, detail="访问密码不正确。")
    response = JSONResponse({"ok": True})
    response.delete_cookie(key=_API_ACCESS_COOKIE_NAME, samesite="lax")
    return response


@app.post("/api/auth/logout")
def auth_logout():
    response = JSONResponse({"ok": True})
    response.delete_cookie(key=_API_ACCESS_COOKIE_NAME, samesite="lax")
    return response


@app.get("/api/auth/check", dependencies=[Depends(_verify_access)])
def auth_check():
    return {"ok": True}


@app.get("/api/summary", dependencies=[Depends(_verify_access)])
def summary():
    df = _load_products()
    meta = _read_json(CACHE_META_KEY, {})
    if df.empty:
        return {"loaded": False, "meta": meta, "category_count": 0, "model_count": 0, "row_count": 0}
    return {
        "loaded": True,
        "meta": meta,
        "category_count": int(df["Category"].nunique()) if "Category" in df.columns else 0,
        "model_count": int(df["model"].nunique()) if "model" in df.columns else 0,
        "row_count": int(len(df)),
    }


@app.get("/api/admin/overview", dependencies=[Depends(_verify_access)])
def admin_overview():
    today = dt.date.today()
    assets = _visible_competitor_assets(_load_competitor_assets())
    hotspots, active_hotspot_total = _search_hotspots(active_only=True, limit=6)
    latest_assets = sorted(
        _business_usable_competitor_assets(assets),
        key=lambda item: str(item.get("updated_at") or item.get("collected_at") or ""),
        reverse=True,
    )[:6]
    recent_runs = _load_collection_runs()[:8]
    video_asset_count = sum(1 for item in assets if _asset_media_types(item) & {"video", "gif"})
    today_asset_count = sum(1 for item in assets if (_parse_date(item.get("collected_at") or item.get("created_at")) == today))
    pending_review_count = sum(1 for item in assets if _asset_review_status(item) in {"auto_collected", "needs_review"})
    approved_count = sum(1 for item in assets if _asset_review_status(item) == "approved")
    featured_count = sum(1 for item in assets if _asset_review_status(item) == "featured")
    rejected_count = sum(1 for item in assets if _asset_review_status(item) == "rejected")
    failed_run_count = sum(1 for item in recent_runs if str(item.get("status") or "").lower() == "failed")
    return {
        "metrics": {
            "asset_count": len(assets),
            "video_gif_count": video_asset_count,
            "today_new_assets": today_asset_count,
            "pending_review_count": pending_review_count,
            "approved_count": approved_count,
            "featured_count": featured_count,
            "rejected_count": rejected_count,
            "active_hotspot_count": active_hotspot_total,
            "recent_failed_runs": failed_run_count,
        },
        "latest_assets": [_public_competitor_asset(item, compact=True) for item in latest_assets],
        "hotspots": hotspots,
        "recent_runs": recent_runs,
    }


@app.post("/api/upload", dependencies=[Depends(_verify_access)])
async def upload_product_features(file: UploadFile = File(...)):
    if not file.filename.lower().endswith((".xlsx", ".xls")):
        raise HTTPException(status_code=400, detail="请上传 Excel 文件。")
    data = await file.read()
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail=f"文件过大，最大允许 {MAX_UPLOAD_BYTES // (1024 * 1024)}MB。")
    try:
        df = pd.read_excel(io.BytesIO(data))
        df_filtered = filter_product_features(df)
        meta = PRODUCT_FEATURE_STORE.save(file.filename, data, df_filtered)
        return {"ok": True, "meta": meta}
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"解析文件失败：{exc}") from exc


@app.post("/api/product-images", dependencies=[Depends(_verify_access)])
async def upload_product_image(
    file: UploadFile = File(...),
    script_job_id: str = Form(...),
    variant_index: int = Form(0),
):
    script_job = next((item for item in _load_jobs() if item.get("id") == script_job_id), None)
    if not script_job:
        raise HTTPException(status_code=404, detail="Script job not found.")
    variants = script_job.get("variants") or []
    if variant_index < 0 or variant_index >= len(variants):
        raise HTTPException(status_code=400, detail="Script variant not found.")
    data = await file.read()
    if len(data) > MAX_PRODUCT_IMAGE_BYTES:
        raise HTTPException(status_code=413, detail=f"产品图过大，最大允许 {MAX_PRODUCT_IMAGE_BYTES // (1024 * 1024)}MB。")
    try:
        image_format, width, height, normalized_bytes = _normalize_product_image_bytes(data)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    image_id = uuid.uuid4().hex[:12]
    original_key, normalized_key = _product_image_keys(script_job_id, variant_index, image_id, image_format)
    content_type = "image/jpeg" if image_format == "JPEG" else "image/png"
    image_uri = STORAGE.write_file_bytes(original_key, data, content_type=content_type)
    normalized_uri = STORAGE.write_file_bytes(normalized_key, normalized_bytes, content_type="image/png")
    now = _utc_now()
    asset = {
        "id": image_id,
        "script_job_id": script_job_id,
        "variant_index": int(variant_index),
        "filename": file.filename or "product-image",
        "image_key": original_key,
        "normalized_key": normalized_key,
        "image_uri": image_uri,
        "normalized_uri": normalized_uri,
        "format": image_format,
        "width": int(width),
        "height": int(height),
        "created_at": now,
        "updated_at": now,
    }
    with job_lock:
        assets = _load_product_image_assets()
        assets.insert(0, asset)
        _save_product_image_assets(assets)
    return {"asset": _public_product_image_asset(asset)}


@app.get("/api/product-images", dependencies=[Depends(_verify_access)])
def product_images(script_job_id: str = "", variant_index: int = -1):
    assets = _load_product_image_assets()
    if script_job_id:
        assets = [item for item in assets if str(item.get("script_job_id") or "") == str(script_job_id)]
    if variant_index >= 0:
        assets = [item for item in assets if int(item.get("variant_index", -1)) == int(variant_index)]
    return {"assets": [_public_product_image_asset(item) for item in assets[:30]]}


@app.get("/api/product-images/{image_id}", dependencies=[Depends(_verify_access)])
def product_image_preview(image_id: str):
    asset = _product_image_by_id(image_id)
    if not asset:
        raise HTTPException(status_code=404, detail="Product image not found.")
    try:
        data = STORAGE.read_file_bytes(asset.get("normalized_key") or asset.get("image_key"))
    except Exception as exc:
        raise HTTPException(status_code=404, detail="Product image not found.") from exc
    return Response(data, media_type="image/png", headers={"Cache-Control": "private, max-age=300"})


@app.get("/api/options", dependencies=[Depends(_verify_access)])
def options():
    df = _load_products()
    if df.empty:
        return {"categories": [], "models_by_category": {}}
    categories = sorted(str(x) for x in df["Category"].dropna().unique().tolist())
    models_by_category = {}
    for category in categories:
        rows = df[df["Category"].astype(str) == category]
        models_by_category[category] = sorted(str(x) for x in rows["model"].dropna().unique().tolist())
    return {"categories": categories, "models_by_category": models_by_category}


@app.get("/api/features", dependencies=[Depends(_verify_access)])
def features(category: str, model: str):
    df = _load_products()
    if df.empty:
        return {"features": []}
    rows = df[(df["Category"].astype(str) == category) & (df["model"].astype(str) == model)]
    names = []
    for value in rows["Feature Name"].dropna().astype(str).tolist():
        value = value.strip()
        if value and value not in names:
            names.append(value)
    return {"features": names}


@app.post("/api/generate", dependencies=[Depends(_verify_access)])
def generate(req: GenerateRequest):
    job_id = uuid.uuid4().hex[:12]
    context_snapshot = _script_context_snapshot(req)
    job = {
        "id": job_id,
        "created_at": dt.datetime.utcnow().isoformat(timespec="seconds") + "Z",
        "updated_at": dt.datetime.utcnow().isoformat(timespec="seconds") + "Z",
        "status": "pending",
        "progress": 0,
        "current_step": "已提交",
        "request": req.model_dump(),
        "context_snapshot": context_snapshot,
        "variants": [],
        "error_message": "",
    }
    with job_lock:
        jobs = _load_jobs()
        jobs.insert(0, job)
        _save_jobs(jobs)
    threading.Thread(target=_run_generation, args=(job_id,), daemon=True).start()
    return {"job_id": job_id}


@app.get("/api/jobs", dependencies=[Depends(_verify_access)])
def jobs():
    return {"jobs": [_public_job_summary(item) for item in _load_jobs()[:30]]}


@app.get("/api/jobs/{job_id}", dependencies=[Depends(_verify_access)])
def job(job_id: str):
    found = next((item for item in _load_jobs() if item.get("id") == job_id), None)
    if not found:
        raise HTTPException(status_code=404, detail="任务不存在。")
    return found


@app.post("/api/competitor-sources/rainforest/discover", dependencies=[Depends(_verify_access)])
def rainforest_discover(req: RainforestDiscoverRequest):
    api_key = _current_rainforest_api_key()
    if not api_key:
        raise HTTPException(status_code=503, detail="RAINFOREST_API_KEY 尚未配置。")
    try:
        result = rainforest_discover_asins(
            api_key,
            category=req.category,
            brands=req.brands,
            keywords=req.keywords,
            target_market=req.target_market,
            amazon_domain=req.amazon_domain,
            default_domain=RAINFOREST_DEFAULT_AMAZON_DOMAIN,
            max_results=req.max_results,
            request_limit=req.request_limit,
            timeout=RAINFOREST_REQUEST_TIMEOUT,
        )
        return {"ok": True, **result}
    except RainforestApiError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/competitor-assets/rainforest/refresh", dependencies=[Depends(_verify_access)])
def rainforest_refresh(req: RainforestRefreshRequest):
    api_key = _current_rainforest_api_key()
    if not api_key:
        raise HTTPException(status_code=503, detail="RAINFOREST_API_KEY 尚未配置。")

    domain = amazon_domain_for_market(req.target_market, req.amazon_domain, RAINFOREST_DEFAULT_AMAZON_DOMAIN)
    asin_sources: dict[str, dict] = {}
    for asin in req.asins or []:
        clean = clean_asin(asin)
        if clean:
            asin_sources[clean] = {"asin": clean, "source_query": "manual", "position": None, "amazon_domain": domain}

    discovery_result = {"amazon_domain": domain, "queries": [], "asins": []}
    if req.use_discovery and (req.keywords or req.brands or req.category):
        try:
            discovery_result = rainforest_discover_asins(
                api_key,
                category=req.category,
                brands=req.brands,
                keywords=req.keywords,
                target_market=req.target_market,
                amazon_domain=req.amazon_domain,
                default_domain=RAINFOREST_DEFAULT_AMAZON_DOMAIN,
                max_results=req.max_search_results,
                request_limit=req.request_limit,
                timeout=RAINFOREST_REQUEST_TIMEOUT,
            )
            domain = discovery_result.get("amazon_domain") or domain
            for item in discovery_result.get("asins", []):
                asin = clean_asin(item.get("asin", ""))
                if asin and asin not in asin_sources:
                    asin_sources[asin] = item
        except RainforestApiError as exc:
            if not asin_sources:
                raise HTTPException(status_code=400, detail=str(exc)) from exc

    if not asin_sources:
        raise HTTPException(status_code=400, detail="请提供手动 ASIN，或提供品类/品牌/关键词用于自动发现。")
    if len(asin_sources) > 200:
        raise HTTPException(status_code=400, detail="单次超过 200 个 ASIN 时请使用 Rainforest Collections 批处理后导入 S3 结果。")

    max_products = min(req.max_products, RAINFOREST_MAX_PRODUCTS_PER_REFRESH, 200)
    normalized_assets = []
    errors = []
    for asin, source in list(asin_sources.items())[:max_products]:
        try:
            product_payload = rainforest_fetch_product(
                api_key,
                asin,
                amazon_domain=source.get("amazon_domain") or domain,
                timeout=RAINFOREST_REQUEST_TIMEOUT,
            )
            normalized_assets.append(
                normalize_product_response(
                    product_payload,
                    asin=asin,
                    amazon_domain=source.get("amazon_domain") or domain,
                    category=req.category,
                    source_query=source.get("source_query") or "",
                    search_position=source.get("position"),
                    preferred_brands=req.brands,
                )
            )
            time.sleep(0.15)
        except RainforestApiError as exc:
            errors.append({"asin": asin, "error": str(exc)})
        except Exception as exc:
            errors.append({"asin": asin, "error": str(exc)})

    upsert = _upsert_competitor_assets(normalized_assets)
    return {
        "ok": True,
        "amazon_domain": domain,
        "discovery": discovery_result,
        "requested_asin_count": len(asin_sources),
        "fetched_count": len(normalized_assets),
        "upsert": upsert,
        "errors": errors[:20],
        "assets": [_public_competitor_asset(item) for item in normalized_assets],
        "note": "仅保存链接、缩略图、元数据和分析结果；不会下载或保存竞品视频原片。",
    }


@app.post("/api/social-assets/import-url", dependencies=[Depends(_verify_access)])
def social_import_url(req: SocialUrlImportRequest):
    urls = []
    for value in req.urls or []:
        clean = str(value or "").strip()
        if clean and clean not in urls:
            urls.append(clean)
    if not urls:
        raise HTTPException(status_code=400, detail="请先输入至少一个 Amazon 商品或社媒素材 URL。")

    token = _current_social_oembed_token()
    rainforest_api_key = _current_rainforest_api_key()
    normalized_assets = []
    collection_sources = []
    errors = []
    for url in urls[:50]:
        try:
            amazon_product = _amazon_product_from_url(url)
            if amazon_product:
                if not rainforest_api_key:
                    errors.append({"url": url, "error": "RAINFOREST_API_KEY 尚未配置，无法导入 Amazon 商品 URL。"})
                    continue
                amazon_domain = amazon_product.get("amazon_domain") or amazon_domain_for_market(req.target_market, "", RAINFOREST_DEFAULT_AMAZON_DOMAIN)
                product_payload = rainforest_fetch_product(
                    rainforest_api_key,
                    amazon_product.get("asin") or "",
                    amazon_domain=amazon_domain,
                    timeout=RAINFOREST_REQUEST_TIMEOUT,
                )
                normalized_assets.append(
                    normalize_product_response(
                        product_payload,
                        asin=amazon_product.get("asin") or "",
                        amazon_domain=amazon_domain,
                        category=req.category,
                        source_query="manual_amazon_url",
                        search_position=None,
                        preferred_brands=req.brands,
                    )
                )
                continue

            classification = classify_social_url(url)
            if classification.get("platform") == "Social":
                errors.append({"url": url, "error": "暂不支持该 URL；当前 URL 导入支持 Amazon 商品页、YouTube 视频、Instagram Reel/Post、TikTok 视频、Pinterest Pin 和 Facebook 视频。"})
                continue
            if classification.get("kind") != "asset":
                collection_sources.append(
                    {
                        "id": _stable_id("social-source", classification.get("platform", ""), classification.get("url", url)),
                        "platform": classification.get("platform") or "Social",
                        "handle": classification.get("handle") or "",
                        "source_url": classification.get("url") or url,
                        "category": req.category,
                        "target_market": req.target_market,
                        "brands": req.brands,
                        "status": "source_only",
                        "message": classification.get("message") or "This URL is a source/profile page, not a concrete media asset.",
                        "created_at": _utc_now(),
                        "updated_at": _utc_now(),
                    }
                )
                continue
            normalized_assets.append(
                normalize_social_url(
                    url,
                    category=req.category,
                    brands=req.brands,
                    fetch_oembed=req.fetch_oembed,
                    oembed_access_token=token,
                    timeout=SOCIAL_REQUEST_TIMEOUT,
                )
            )
        except SocialApiError as exc:
            errors.append({"url": url, "error": str(exc)})
        except RainforestApiError as exc:
            errors.append({"url": url, "error": str(exc)})
        except Exception as exc:
            errors.append({"url": url, "error": str(exc)})

    upsert = _upsert_competitor_assets(normalized_assets)
    source_upsert = _upsert_social_collection_sources(collection_sources) if collection_sources else {"inserted": 0, "updated": 0, "total": 0}
    return {
        "ok": True,
        "requested_url_count": len(urls),
        "imported_count": len(normalized_assets),
        "source_count": len(collection_sources),
        "upsert": upsert,
        "source_upsert": source_upsert,
        "errors": errors[:20],
        "assets": [_public_competitor_asset(item) for item in normalized_assets],
        "sources": collection_sources,
        "note": "Amazon 商品 URL 会通过 Rainforest 拉取商品页图片、站内视频和 A+ 证据入库；具体 Reel/Post/视频 URL 才会入库为社媒竞品素材；账号主页、频道页只保存为采集来源线索，不会伪造成素材。系统仅保存链接、缩略图、公开元数据和结构化分析；不会下载或保存竞品视频原片。",
    }


@app.post("/api/competitor-sources/youtube/discover", dependencies=[Depends(_verify_access)])
def youtube_discover(req: YouTubeDiscoverRequest):
    api_key = _current_youtube_api_key()
    if not api_key:
        raise HTTPException(status_code=503, detail="YOUTUBE_API_KEY 尚未配置；可先用社媒 URL 手动入库。")
    try:
        result = discover_youtube_videos(
            api_key,
            category=req.category,
            brands=req.brands,
            keywords=req.keywords,
            target_market=req.target_market,
            region_code=req.region_code,
            max_results=req.max_results,
            request_limit=req.request_limit,
            timeout=SOCIAL_REQUEST_TIMEOUT,
        )
        return {"ok": True, **result, "assets": [_public_competitor_asset(item) for item in result.get("assets", [])]}
    except SocialApiError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/competitor-assets/youtube/refresh", dependencies=[Depends(_verify_access)])
def youtube_refresh(req: YouTubeRefreshRequest):
    api_key = _current_youtube_api_key()
    if not api_key:
        raise HTTPException(status_code=503, detail="YOUTUBE_API_KEY 尚未配置；可先用社媒 URL 手动入库。")

    video_sources: dict[str, dict] = {}
    for value in req.video_ids or []:
        video_id = extract_youtube_video_id(value) or str(value or "").strip()
        if video_id:
            video_sources[video_id] = {"video_id": video_id, "source_query": "manual", "position": None}

    discovery_result = {"platform": "YouTube", "queries": [], "video_ids": [], "assets": []}
    if req.use_discovery and (req.keywords or req.brands or req.category):
        try:
            discovery_result = discover_youtube_videos(
                api_key,
                category=req.category,
                brands=req.brands,
                keywords=req.keywords,
                target_market=req.target_market,
                region_code=req.region_code,
                max_results=req.max_results,
                request_limit=req.request_limit,
                timeout=SOCIAL_REQUEST_TIMEOUT,
            )
            for asset in discovery_result.get("assets", []) or []:
                metadata = asset.get("metadata") or {}
                video_id = str(metadata.get("youtube_video_id") or metadata.get("platform_content_id") or "").strip()
                if video_id and video_id not in video_sources:
                    video_sources[video_id] = {
                        "video_id": video_id,
                        "source_query": metadata.get("source_query") or "",
                        "position": metadata.get("search_position"),
                        "asset": asset,
                    }
        except SocialApiError as exc:
            if not video_sources:
                raise HTTPException(status_code=400, detail=str(exc)) from exc

    if not video_sources:
        if discovery_result.get("filtered_count"):
            raise HTTPException(
                status_code=400,
                detail=f"YouTube 已发现 {discovery_result.get('filtered_count')} 条视频，但均不符合官方竞品家电素材规则；请检查品牌和品类检索词。",
            )
        raise HTTPException(status_code=400, detail="请提供 YouTube 视频 ID/链接，或提供品类/品牌/关键词用于自动发现。")

    normalized_assets = []
    errors = []
    for video_id, source in list(video_sources.items())[: req.max_videos]:
        try:
            if source.get("asset"):
                normalized_assets.append(source["asset"])
                continue
            details = fetch_youtube_videos(api_key, [video_id], timeout=SOCIAL_REQUEST_TIMEOUT)
            if not details:
                errors.append({"video_id": video_id, "error": "YouTube video not found."})
                continue
            normalized_assets.append(
                normalize_youtube_video_item(
                    details[0],
                    category=req.category,
                    preferred_brands=req.brands,
                    source_query=source.get("source_query") or "",
                    search_position=source.get("position"),
                )
            )
        except SocialApiError as exc:
            errors.append({"video_id": video_id, "error": str(exc)})
        except Exception as exc:
            errors.append({"video_id": video_id, "error": str(exc)})

    upsert = _upsert_competitor_assets(normalized_assets)
    return {
        "ok": True,
        "requested_video_count": len(video_sources),
        "fetched_count": len(normalized_assets),
        "discovery": {**discovery_result, "assets": [_public_competitor_asset(item) for item in discovery_result.get("assets", [])]},
        "upsert": upsert,
        "errors": errors[:20],
        "assets": [_public_competitor_asset(item) for item in normalized_assets],
        "note": "YouTube 素材仅保存链接、缩略图 URL、公开元数据和分析结果；不会下载或保存竞品视频原片。",
    }


@app.post("/api/competitor-assets/social/thumbnails/refresh", dependencies=[Depends(_verify_access)])
def social_thumbnail_refresh(req: SocialThumbnailRefreshRequest):
    if req.asset_ids:
        wanted = {str(item) for item in req.asset_ids}
        assets = [item for item in _load_competitor_assets() if str(item.get("id") or "") in wanted]
    else:
        assets, _ = _search_competitor_assets(
            q=req.q,
            category=req.category,
            platform=req.platform,
            source=req.source,
            media_type=req.media_type,
            limit=req.limit,
        )

    youtube_key = _current_youtube_api_key()
    token = _current_social_oembed_token()
    refreshed_assets = []
    errors = []
    for asset in assets[: req.limit]:
        try:
            refreshed, changed, error = refresh_social_thumbnail(
                asset,
                youtube_api_key=youtube_key,
                oembed_access_token=token,
                timeout=SOCIAL_REQUEST_TIMEOUT,
            )
            if changed:
                refreshed_assets.append(refreshed)
            elif error:
                errors.append({"id": asset.get("id"), "error": error})
        except Exception as exc:
            errors.append({"id": asset.get("id"), "error": str(exc)})

    upsert = _upsert_competitor_assets(refreshed_assets) if refreshed_assets else {"inserted": 0, "updated": 0, "total": 0}
    return {
        "ok": True,
        "matched_count": len(assets),
        "refreshed_count": len(refreshed_assets),
        "upsert": upsert,
        "errors": errors[:20],
        "assets": [_public_competitor_asset(item) for item in refreshed_assets],
    }


@app.get("/api/competitor-assets/search", dependencies=[Depends(_verify_access)])
def competitor_asset_search(
    q: str = "",
    category: str = "",
    brand: str = "",
    platform: str = "",
    source: str = "",
    media_type: str = "",
    review_status: str = "",
    rights_status: str = "",
    date_from: str = "",
    date_to: str = "",
    min_quality: int = 0,
    sort: str = "quality_desc",
    offset: int = 0,
    page: int = 1,
    page_size: int = 0,
    limit: int = 20,
    compact: bool = True,
):
    if page_size:
        limit = page_size
        offset = max(0, int(page or 1) - 1) * int(page_size or 20)
    limit = max(1, min(int(limit or 20), 100))
    assets, total = _search_competitor_assets(
        q=q,
        category=category,
        brand=brand,
        platform=platform,
        source=source,
        media_type=media_type,
        review_status=review_status,
        rights_status=rights_status,
        date_from=date_from,
        date_to=date_to,
        min_quality=min_quality,
        sort=sort,
        offset=offset,
        limit=limit,
    )
    return {
        "assets": [_public_competitor_asset(item, compact=compact) for item in assets],
        "count": len(assets),
        "total": total,
        "offset": max(0, int(offset or 0)),
        "limit": limit,
    }


@app.post("/api/competitor-assets/deep-analysis-runs", dependencies=[Depends(_verify_access)])
def create_competitor_deep_analysis_run(req: CompetitorDeepAnalysisRequest):
    selected_assets = _select_competitor_assets_for_deep_analysis(req)
    if not selected_assets:
        raise HTTPException(status_code=400, detail="没有匹配到需要深度分析的竞品素材。")
    now = _utc_now()
    run_id = uuid.uuid4().hex[:12]
    job = {
        "id": run_id,
        "created_at": now,
        "updated_at": now,
        "completed_at": "",
        "status": "pending",
        "progress": 0,
        "current_step": "已提交",
        "request": req.model_dump(),
        "target_count": len(selected_assets),
        "completed_count": 0,
        "updated_asset_ids": [],
        "errors": [],
        "error_message": "",
        "model_provider": "aws_bedrock",
        "model_id": BEDROCK_MODEL_ID,
        "concurrency": min(int(req.concurrency or 4), 8, len(selected_assets)),
    }
    with competitor_lock:
        runs = _load_competitor_analysis_runs()
        runs.insert(0, job)
        _save_competitor_analysis_runs(runs)
    threading.Thread(target=_run_competitor_deep_analysis_job, args=(run_id,), daemon=True).start()
    return {"run_id": run_id, "job": job}


@app.get("/api/competitor-assets/deep-analysis-runs", dependencies=[Depends(_verify_access)])
def list_competitor_deep_analysis_runs(limit: int = 20):
    limit = max(1, min(int(limit or 20), 100))
    return {"jobs": _load_competitor_analysis_runs()[:limit]}


@app.get("/api/competitor-assets/deep-analysis-runs/{run_id}", dependencies=[Depends(_verify_access)])
def get_competitor_deep_analysis_run(run_id: str):
    found = next((item for item in _load_competitor_analysis_runs() if item.get("id") == run_id), None)
    if not found:
        raise HTTPException(status_code=404, detail="深度分析任务不存在。")
    updated_ids = found.get("updated_asset_ids") or []
    assets = _assets_by_ids(updated_ids[:20]) if updated_ids else []
    return {**found, "assets": [_public_competitor_asset(item) for item in assets]}


@app.get("/api/competitor-assets/{asset_id}", dependencies=[Depends(_verify_access)])
def get_competitor_asset(asset_id: str):
    found = next((item for item in _load_competitor_assets() if str(item.get("id") or "") == asset_id), None)
    if not found:
        raise HTTPException(status_code=404, detail="竞品素材不存在。")
    return {"asset": _public_competitor_asset(_ensure_asset_admin_defaults(found))}


@app.patch("/api/competitor-assets/{asset_id}", dependencies=[Depends(_verify_access)])
def update_competitor_asset(asset_id: str, req: CompetitorAssetPatchRequest):
    allowed_statuses = {"auto_collected", "approved", "featured", "rejected", "needs_review"}
    updates = {key: value for key, value in req.model_dump(exclude_unset=True).items() if value is not None}
    if "review_status" in updates and updates["review_status"] not in allowed_statuses:
        raise HTTPException(status_code=400, detail="review_status 不合法。")
    with competitor_lock:
        assets = _load_competitor_assets()
        found = None
        for index, item in enumerate(assets):
            if str(item.get("id") or "") == asset_id:
                updated = _ensure_asset_admin_defaults({**item, **updates, "updated_at": _utc_now()})
                assets[index] = updated
                found = updated
                break
        if not found:
            raise HTTPException(status_code=404, detail="竞品素材不存在。")
        _save_competitor_assets(assets)
    return {"ok": True, "asset": _public_competitor_asset(found)}


@app.post("/api/competitor-assets/bulk-review", dependencies=[Depends(_verify_access)])
def bulk_review_competitor_assets(req: CompetitorBulkReviewRequest):
    wanted = {str(item) for item in req.asset_ids}
    changed = []
    with competitor_lock:
        assets = _load_competitor_assets()
        for index, item in enumerate(assets):
            if str(item.get("id") or "") in wanted:
                updated = _ensure_asset_admin_defaults({**item, "review_status": req.review_status, "updated_at": _utc_now()})
                assets[index] = updated
                changed.append(updated)
        _save_competitor_assets(assets)
    return {"ok": True, "updated_count": len(changed), "assets": [_public_competitor_asset(item) for item in changed]}


@app.post("/api/competitor-research/jobs", dependencies=[Depends(_verify_access)])
def create_competitor_research_job(req: CompetitorResearchRequest):
    now = dt.datetime.utcnow().isoformat(timespec="seconds") + "Z"
    job_id = uuid.uuid4().hex[:12]
    job = {
        "id": job_id,
        "created_at": now,
        "updated_at": now,
        "status": "pending",
        "progress": 0,
        "current_step": "已提交",
        "request": req.model_dump(),
        "report": "",
        "evidence": [],
        "error_message": "",
    }
    with competitor_lock:
        jobs = _load_competitor_research_jobs()
        jobs.insert(0, job)
        _save_competitor_research_jobs(jobs)
    threading.Thread(target=_run_competitor_research_job, args=(job_id,), daemon=True).start()
    return {"job_id": job_id}


@app.get("/api/competitor-research/jobs", dependencies=[Depends(_verify_access)])
def competitor_research_jobs():
    return {"jobs": _load_competitor_research_jobs()[:30]}


@app.get("/api/competitor-research/jobs/{job_id}", dependencies=[Depends(_verify_access)])
def competitor_research_job(job_id: str):
    found = next((item for item in _load_competitor_research_jobs() if item.get("id") == job_id), None)
    if not found:
        raise HTTPException(status_code=404, detail="竞品调研任务不存在。")
    return found


@app.get("/api/hotspots", dependencies=[Depends(_verify_access)])
def list_hotspots(
    q: str = "",
    category: str = "",
    target_market: str = "",
    platform: str = "",
    source_type: str = "",
    status: str = "",
    active_only: bool = False,
    offset: int = 0,
    limit: int = 50,
):
    limit = max(1, min(int(limit or 50), 200))
    hotspots, total = _search_hotspots(
        q=q,
        category=category,
        target_market=target_market,
        platform=platform,
        source_type=source_type,
        status=status,
        active_only=active_only,
        offset=offset,
        limit=limit,
    )
    return {"hotspots": hotspots, "count": len(hotspots), "total": total, "offset": max(0, int(offset or 0)), "limit": limit}


@app.post("/api/hotspots", dependencies=[Depends(_verify_access)])
def create_hotspot(req: HotspotRequest):
    hotspot = _normalize_hotspot(req.model_dump())
    upsert = _upsert_hotspots([hotspot])
    return {"ok": True, "upsert": upsert, "hotspot": hotspot}


@app.patch("/api/hotspots/{hotspot_id}", dependencies=[Depends(_verify_access)])
def update_hotspot(hotspot_id: str, req: HotspotPatchRequest):
    updates = {key: value for key, value in req.model_dump(exclude_unset=True).items() if value is not None}
    with competitor_lock:
        hotspots = _load_hotspots()
        found = None
        for index, item in enumerate(hotspots):
            current = _normalize_hotspot(item)
            if current.get("id") == hotspot_id:
                found = _normalize_hotspot({**current, **updates, "updated_at": _utc_now()})
                hotspots[index] = found
                break
        if not found:
            raise HTTPException(status_code=404, detail="热点不存在。")
        _save_hotspots(hotspots)
    return {"ok": True, "hotspot": found}


@app.delete("/api/hotspots/{hotspot_id}", dependencies=[Depends(_verify_access)])
def delete_hotspot(hotspot_id: str):
    with competitor_lock:
        hotspots = [_normalize_hotspot(item) for item in _load_hotspots()]
        next_items = [item for item in hotspots if item.get("id") != hotspot_id]
        if len(next_items) == len(hotspots):
            raise HTTPException(status_code=404, detail="热点不存在。")
        _save_hotspots(next_items)
    return {"ok": True}


@app.post("/api/hotspots/refresh", dependencies=[Depends(_verify_access)])
def refresh_hotspots(req: HotspotRefreshRequest):
    result = _refresh_hotspots(req)
    return {"ok": True, **result, "hotspots": result.get("hotspots", [])[:50]}


@app.get("/api/hotspot-sources", dependencies=[Depends(_verify_access)])
def list_hotspot_sources():
    return {"sources": _load_hotspot_sources()}


@app.post("/api/hotspot-sources", dependencies=[Depends(_verify_access)])
def create_hotspot_source(req: HotspotSourceRequest):
    now = _utc_now()
    source = {
        **req.model_dump(),
        "id": _stable_id("hotspot-source", req.source_type, req.name, req.url),
        "created_at": now,
        "updated_at": now,
    }
    sources = _load_hotspot_sources()
    sources = [item for item in sources if item.get("id") != source["id"]]
    sources.insert(0, source)
    _save_hotspot_sources(sources)
    return {"ok": True, "source": source}


@app.patch("/api/hotspot-sources/{source_id}", dependencies=[Depends(_verify_access)])
def update_hotspot_source(source_id: str, req: HotspotSourcePatchRequest):
    updates = {key: value for key, value in req.model_dump(exclude_unset=True).items() if value is not None}
    with competitor_lock:
        sources = _load_hotspot_sources()
        found = None
        for index, item in enumerate(sources):
            if str(item.get("id") or "") == source_id:
                found = {**item, **updates, "updated_at": _utc_now()}
                sources[index] = found
                break
        if not found:
            raise HTTPException(status_code=404, detail="热点源不存在。")
        _save_hotspot_sources(sources)
    return {"ok": True, "source": found}


@app.delete("/api/hotspot-sources/{source_id}", dependencies=[Depends(_verify_access)])
def delete_hotspot_source(source_id: str):
    with competitor_lock:
        sources = _load_hotspot_sources()
        next_sources = [item for item in sources if str(item.get("id") or "") != source_id]
        if len(next_sources) == len(sources):
            raise HTTPException(status_code=404, detail="热点源不存在。")
        _save_hotspot_sources(next_sources)
    return {"ok": True}


@app.get("/api/competitor-configs", dependencies=[Depends(_verify_access)])
def list_competitor_configs(category: str = ""):
    configs = _load_competitor_configs()
    if category:
        configs = [item for item in configs if str(item.get("category") or "").lower() == category.lower()]
    return {"configs": configs}


@app.post("/api/competitor-configs", dependencies=[Depends(_verify_access)])
def upsert_competitor_config(req: CompetitorConfigRequest):
    now = _utc_now()
    incoming = {**req.model_dump(), "id": _stable_id("competitor-config", req.category), "updated_at": now}
    configs = _load_competitor_configs()
    replaced = False
    for index, item in enumerate(configs):
        if str(item.get("category") or "").lower() == req.category.lower():
            incoming["created_at"] = item.get("created_at") or now
            configs[index] = incoming
            replaced = True
            break
    if not replaced:
        incoming["created_at"] = now
        configs.insert(0, incoming)
    _save_competitor_configs(configs)
    return {"ok": True, "config": incoming}


@app.patch("/api/competitor-configs/{category}", dependencies=[Depends(_verify_access)])
def patch_competitor_config(category: str, req: CompetitorConfigPatchRequest):
    now = _utc_now()
    updates = {key: value for key, value in req.model_dump(exclude_unset=True).items() if value is not None}
    configs = _load_competitor_configs()
    found = None
    for index, item in enumerate(configs):
        if str(item.get("category") or "").lower() == category.lower():
            found = {**item, **updates, "updated_at": now}
            configs[index] = found
            break
    if not found:
        found = {
            "id": _stable_id("competitor-config", category),
            "category": category,
            "brands": updates.get("brands", []),
            "keywords": updates.get("keywords", []),
            "platforms": updates.get("platforms", []),
            "target_market": updates.get("target_market", ""),
            "refresh_frequency": updates.get("refresh_frequency", "manual"),
            "notes": updates.get("notes", ""),
            "created_at": now,
            "updated_at": now,
        }
        configs.insert(0, found)
    _save_competitor_configs(configs)
    return {"ok": True, "config": found}


@app.get("/api/competitor-collection-runs", dependencies=[Depends(_verify_access)])
def list_competitor_collection_runs(limit: int = 50):
    limit = max(1, min(int(limit or 50), 200))
    return {"runs": [_public_collection_run(item) for item in _load_collection_runs()[:limit]]}


@app.post("/api/competitor-collection-runs", dependencies=[Depends(_verify_access)])
def create_competitor_collection_run(req: CompetitorCollectionRunRequest):
    now = _utc_now()
    requested_status = str(req.status or "queued").lower()
    status = requested_status if requested_status in {"succeeded", "failed"} else "queued"
    run = {
        "id": uuid.uuid4().hex[:12],
        "created_at": now,
        "updated_at": now,
        "request": req.model_dump(),
        "status": status,
        "progress": 0 if status == "queued" else 100,
        "current_step": "已提交，等待后台执行" if status == "queued" else "手动记录",
        "error_message": req.error_message,
        "note": "提交后会在当前后台进程内执行 Amazon/Rainforest 或 YouTube 入库；其他平台请在竞品素材库导入具体 URL，或接外部采集器。",
    }
    with competitor_lock:
        runs = _load_collection_runs()
        runs.insert(0, run)
        _save_collection_runs(runs)
    if status == "queued":
        threading.Thread(target=_run_competitor_collection_job, args=(run["id"],), daemon=True).start()
    return {"ok": True, "run": run}


@app.get("/api/nova-reel/jobs", dependencies=[Depends(_verify_access)])
def nova_reel_jobs(script_job_id: str = ""):
    jobs = _load_nova_reel_jobs()
    if script_job_id:
        jobs = [item for item in jobs if item.get("script_job_id") == script_job_id]
    return {
        "jobs": [_public_nova_reel_job(item) for item in jobs[:30]],
        "model_id": NOVA_REEL_MODEL_ID,
        "region": NOVA_REEL_AWS_REGION,
        "estimated_usd_per_second": NOVA_REEL_ESTIMATED_USD_PER_SECOND,
    }


@app.get("/api/storyboard-video/jobs", dependencies=[Depends(_verify_access)])
def storyboard_video_jobs(script_job_id: str = "", variant_index: int = -1, shot_index: int = -1):
    jobs = _load_storyboard_video_jobs()
    if script_job_id:
        jobs = [item for item in jobs if item.get("script_job_id") == script_job_id]
    if variant_index >= 0:
        jobs = [item for item in jobs if int(item.get("variant_index", -1)) == int(variant_index)]
    if shot_index >= 0:
        jobs = [item for item in jobs if int(item.get("shot_index", -1)) == int(shot_index)]
    return {
        "jobs": [_public_nova_reel_job(item) for item in jobs[:30]],
        "model_id": NOVA_REEL_MODEL_ID,
        "region": NOVA_REEL_AWS_REGION,
        "estimated_usd_per_second": NOVA_REEL_ESTIMATED_USD_PER_SECOND,
    }


@app.get("/api/nova-canvas/jobs", dependencies=[Depends(_verify_access)])
def nova_canvas_jobs(script_job_id: str = "", variant_index: int = -1):
    jobs = _load_nova_canvas_jobs()
    if script_job_id:
        jobs = [item for item in jobs if item.get("script_job_id") == script_job_id]
    if variant_index >= 0:
        jobs = [item for item in jobs if int(item.get("variant_index", -1)) == int(variant_index)]
    return {
        "jobs": [_public_nova_canvas_job(item) for item in jobs[:80]],
        "provider": _image_provider_name(),
        "model_id": _image_provider_model_id(),
        "region": NOVA_CANVAS_AWS_REGION if _image_provider_name() == "nova_canvas" else "",
        "estimated_usd_per_image": NOVA_CANVAS_ESTIMATED_USD_PER_IMAGE,
    }


@app.post("/api/nova-canvas/submit", dependencies=[Depends(_verify_access)])
def submit_nova_canvas(req: NovaCanvasSubmitRequest):
    script_job = next((item for item in _load_jobs() if item.get("id") == req.script_job_id), None)
    if not script_job:
        raise HTTPException(status_code=404, detail="Script job not found.")
    if script_job.get("status") != "succeeded":
        raise HTTPException(status_code=400, detail="Script job is not completed yet.")
    variants = script_job.get("variants") or []
    if req.variant_index >= len(variants):
        raise HTTPException(status_code=400, detail="Script variant not found.")

    request_payload = script_job.get("request") or {}
    variant = variants[req.variant_index]
    now = dt.datetime.utcnow().isoformat(timespec="seconds") + "Z"
    raw_prompt = str(req.prompt or "")[:2000]
    reference_policy = _storyboard_reference_policy(
        raw_prompt,
        category=request_payload.get("category", ""),
        model=request_payload.get("model", ""),
    )
    product_asset = _product_image_by_id(req.product_image_id, script_job_id=req.script_job_id) if req.product_image_id else None
    if req.product_image_id and not product_asset:
        raise HTTPException(status_code=404, detail="Product image not found.")
    reference_image_bytes = None
    reference_metadata = {}
    if product_asset:
        raw_reference_bytes = None
        for key_name in ("image_key", "normalized_key"):
            key = product_asset.get(key_name, "")
            if not key:
                continue
            try:
                raw_reference_bytes = STORAGE.read_file_bytes(key)
                break
            except Exception:
                continue
        if raw_reference_bytes:
            reference_image_bytes, reference_metadata = _prepare_storyboard_reference_image_bytes(
                raw_reference_bytes,
                category=request_payload.get("category", ""),
                model=request_payload.get("model", ""),
                prompt=raw_prompt,
            )
    if reference_policy.startswith("skip-"):
        reference_image_bytes = None
    with job_lock:
        existing_jobs = _load_nova_canvas_jobs()
        existing_active = next(
            (
                item
                for item in existing_jobs
                if item.get("script_job_id") == req.script_job_id
                and int(item.get("variant_index", -1)) == int(req.variant_index)
                and int(item.get("shot_index", -1)) == int(req.shot_index)
                and _is_active_storyboard_image_status(item.get("status", ""))
            ),
            None,
        )
    if existing_active:
        return {"job": _public_nova_canvas_job(existing_active)}
    if _image_provider_name() == "liblibai":
        generation_prompt = _compact_liblibai_storyboard_prompt(
            raw_prompt,
            category=request_payload.get("category", ""),
            model=request_payload.get("model", ""),
            shot_index=req.shot_index,
            reference_policy=reference_policy,
        )
    else:
        generation_prompt = _enhance_storyboard_image_prompt(
            raw_prompt,
            category=request_payload.get("category", ""),
            model=request_payload.get("model", ""),
            shot_index=req.shot_index,
            reference_policy=reference_policy,
        )
    image_job = {
        "id": uuid.uuid4().hex[:12],
        "script_job_id": script_job.get("id"),
        "created_at": now,
        "updated_at": now,
        "category": request_payload.get("category", ""),
        "model": request_payload.get("model", ""),
        "variant_index": req.variant_index,
        "variant_name": variant.get("name", f"Variant {req.variant_index + 1}"),
        "variant_label": variant.get("label", ""),
        "shot_index": req.shot_index,
        "product_image_id": (product_asset or {}).get("id", ""),
        "product_image_key": (product_asset or {}).get("normalized_key", ""),
        "prompt": raw_prompt,
        "generation_prompt": generation_prompt,
        "status": "queued",
        "failure_message": "",
        "image_key": "",
        "image_uri": "",
        "provider": _image_provider_name(),
        "model_id": _image_provider_model_id(),
        "region": NOVA_CANVAS_AWS_REGION if _image_provider_name() == "nova_canvas" else "",
        "external_job_id": "",
        "remote_image_url": "",
        "reference_image_url": "",
        "image_generation_mode": (
            "image-to-image"
            if reference_image_bytes and _liblibai_uses_source_image_mode()
            else "reference-controlnet" if reference_image_bytes else "text-to-image"
        ),
        "reference_control_type": LIBLIBAI_REFERENCE_CONTROL_TYPE if _image_provider_name() == "liblibai" and reference_image_bytes else "",
        "reference_policy": reference_policy,
        "reference_preprocess": reference_metadata.get("reference_preprocess", ""),
        "reference_crop_box": reference_metadata.get("reference_crop_box", []),
        "attempt": 0,
        "seed": None,
    }
    with job_lock:
        jobs = _load_nova_canvas_jobs()
        jobs.insert(0, image_job)
        _save_nova_canvas_jobs(jobs)

    storyboard_image_executor.submit(
        _run_storyboard_image_job,
        image_job["id"],
        generation_prompt,
        req.script_job_id,
        req.variant_index,
        req.shot_index,
        image_job["category"],
        image_job["model"],
        reference_image_bytes,
    )
    return {"job": _public_nova_canvas_job(image_job)}


@app.get("/api/nova-canvas/images/{image_job_id}", dependencies=[Depends(_verify_access)])
def nova_canvas_image(image_job_id: str):
    job = next((item for item in _load_nova_canvas_jobs() if item.get("id") == image_job_id), None)
    if not job or not job.get("image_key"):
        raise HTTPException(status_code=404, detail="Image not found.")
    try:
        data = STORAGE.read_file_bytes(job["image_key"])
    except Exception as exc:
        raise HTTPException(status_code=404, detail="Image not found.") from exc
    return Response(data, media_type="image/png", headers={"Cache-Control": "private, max-age=300"})


@app.post("/api/nova-reel/submit", dependencies=[Depends(_verify_access)])
def submit_nova_reel(req: NovaReelSubmitRequest):
    script_job = next((item for item in _load_jobs() if item.get("id") == req.script_job_id), None)
    if not script_job:
        raise HTTPException(status_code=404, detail="Script job not found.")
    if script_job.get("status") != "succeeded":
        raise HTTPException(status_code=400, detail="Script job is not completed yet.")
    variants = script_job.get("variants") or []
    if req.variant_index >= len(variants):
        raise HTTPException(status_code=400, detail="Script variant not found.")

    request_payload = script_job.get("request") or {}
    variant = variants[req.variant_index]
    prompt = _build_variant_nova_reel_prompt(
        variant,
        request_payload.get("category", ""),
        request_payload.get("model", ""),
        request_payload.get("selected_features", []),
    )
    try:
        invocation_arn, output_s3_uri = _start_nova_reel_job(
            request_payload.get("category", ""),
            request_payload.get("model", ""),
            prompt,
            duration_seconds=6,
        )
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    video_job = {
        "id": uuid.uuid4().hex[:12],
        "script_job_id": script_job.get("id"),
        "created_at": dt.datetime.utcnow().isoformat(timespec="seconds") + "Z",
        "updated_at": dt.datetime.utcnow().isoformat(timespec="seconds") + "Z",
        "category": request_payload.get("category", ""),
        "model": request_payload.get("model", ""),
        "variant_index": req.variant_index,
        "variant_name": variant.get("name", f"方案{req.variant_index + 1}"),
        "variant_label": variant.get("label", ""),
        "prompt": prompt,
        "duration_seconds": 6,
        "status": "InProgress",
        "failure_message": "",
        "invocation_arn": invocation_arn,
        "output_s3_uri": output_s3_uri,
        "video_s3_uri": "",
        "model_id": NOVA_REEL_MODEL_ID,
        "region": NOVA_REEL_AWS_REGION,
    }
    jobs = _load_nova_reel_jobs()
    jobs.insert(0, video_job)
    _save_nova_reel_jobs(jobs)
    return {"job": _public_nova_reel_job(video_job)}


@app.post("/api/nova-reel/refresh", dependencies=[Depends(_verify_access)])
def refresh_nova_reel_jobs(script_job_id: str = ""):
    jobs = _load_nova_reel_jobs()
    changed = False
    for item in jobs:
        if script_job_id and item.get("script_job_id") != script_job_id:
            continue
        if item.get("status") in {"Completed", "Failed"}:
            continue
        invocation_arn = item.get("invocation_arn", "")
        if not invocation_arn:
            continue
        try:
            result = _query_nova_reel_job(invocation_arn)
            status = result.get("status") or item.get("status", "")
            item["status"] = status
            item["updated_at"] = dt.datetime.utcnow().isoformat(timespec="seconds") + "Z"
            item["failure_message"] = result.get("failureMessage") or result.get("failure_message") or ""
            output_s3_uri = (
                ((result.get("outputDataConfig") or {}).get("s3OutputDataConfig") or {}).get("s3Uri")
                or item.get("output_s3_uri", "")
            )
            item["output_s3_uri"] = output_s3_uri
            if status == "Completed":
                item["video_s3_uri"] = _video_uri_from_bedrock_job(result)
            changed = True
        except Exception as exc:
            item["failure_message"] = str(exc)
            item["updated_at"] = dt.datetime.utcnow().isoformat(timespec="seconds") + "Z"
            changed = True
    if changed:
        _save_nova_reel_jobs(jobs)
    filtered = [item for item in jobs if not script_job_id or item.get("script_job_id") == script_job_id]
    return {"jobs": [_public_nova_reel_job(item) for item in filtered[:30]]}


@app.post("/api/storyboard-video/submit", dependencies=[Depends(_verify_access)])
def submit_storyboard_video(req: StoryboardVideoSubmitRequest):
    script_job = next((item for item in _load_jobs() if item.get("id") == req.script_job_id), None)
    if not script_job:
        raise HTTPException(status_code=404, detail="Script job not found.")
    if script_job.get("status") != "succeeded":
        raise HTTPException(status_code=400, detail="Script job is not completed yet.")
    variants = script_job.get("variants") or []
    if req.variant_index >= len(variants):
        raise HTTPException(status_code=400, detail="Script variant not found.")
    if req.product_image_id and not _product_image_by_id(req.product_image_id, script_job_id=req.script_job_id):
        raise HTTPException(status_code=404, detail="Product image not found.")

    request_payload = script_job.get("request") or {}
    variant = variants[req.variant_index]
    if req.shot_index >= 0:
        manual_shots, duration_seconds, product_asset = _build_storyboard_single_shot(
            script_job,
            req.variant_index,
            req.shot_index,
            product_image_id=req.product_image_id,
        )
    else:
        manual_shots, duration_seconds, product_asset = _build_storyboard_manual_shots(
            script_job,
            req.variant_index,
            product_image_id=req.product_image_id,
        )
    try:
        invocation_arn, output_s3_uri = _start_storyboard_video_job(
            request_payload.get("category", ""),
            request_payload.get("model", ""),
            manual_shots,
        )
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    now = _utc_now()
    video_job = {
        "id": uuid.uuid4().hex[:12],
        "script_job_id": script_job.get("id"),
        "created_at": now,
        "updated_at": now,
        "source": "storyboard_manual",
        "category": request_payload.get("category", ""),
        "model": request_payload.get("model", ""),
        "variant_index": req.variant_index,
        "variant_name": variant.get("name", f"方案{req.variant_index + 1}"),
        "variant_label": variant.get("label", ""),
        "shot_index": req.shot_index,
        "product_image_id": (product_asset or {}).get("id", ""),
        "duration_seconds": duration_seconds,
        "shot_count": len(manual_shots),
        "shots": [
            {
                "text": item.get("text", ""),
                "source_image_key": item.get("source_image_key", ""),
                "has_image": bool(item.get("image")),
            }
            for item in manual_shots
        ],
        "status": "InProgress",
        "failure_message": "",
        "invocation_arn": invocation_arn,
        "output_s3_uri": output_s3_uri,
        "video_s3_uri": "",
        "model_id": NOVA_REEL_MODEL_ID,
        "region": NOVA_REEL_AWS_REGION,
    }
    jobs = _load_storyboard_video_jobs()
    jobs.insert(0, video_job)
    _save_storyboard_video_jobs(jobs)
    return {"job": _public_nova_reel_job(video_job)}


@app.post("/api/storyboard-video/refresh", dependencies=[Depends(_verify_access)])
def refresh_storyboard_video_jobs(script_job_id: str = "", variant_index: int = -1, shot_index: int = -1):
    jobs = _load_storyboard_video_jobs()
    changed = False
    for item in jobs:
        if script_job_id and item.get("script_job_id") != script_job_id:
            continue
        if variant_index >= 0 and int(item.get("variant_index", -1)) != int(variant_index):
            continue
        if shot_index >= 0 and int(item.get("shot_index", -1)) != int(shot_index):
            continue
        if item.get("status") in {"Completed", "Failed"}:
            continue
        invocation_arn = item.get("invocation_arn", "")
        if not invocation_arn:
            continue
        try:
            result = _query_nova_reel_job(invocation_arn)
            status = result.get("status") or item.get("status", "")
            item["status"] = status
            item["updated_at"] = _utc_now()
            item["failure_message"] = result.get("failureMessage") or result.get("failure_message") or ""
            output_s3_uri = (
                ((result.get("outputDataConfig") or {}).get("s3OutputDataConfig") or {}).get("s3Uri")
                or item.get("output_s3_uri", "")
            )
            item["output_s3_uri"] = output_s3_uri
            if status == "Completed":
                item["video_s3_uri"] = _video_uri_from_bedrock_job(result)
            changed = True
        except Exception as exc:
            item["failure_message"] = str(exc)
            item["updated_at"] = _utc_now()
            changed = True
    if changed:
        _save_storyboard_video_jobs(jobs)
    filtered = [
        item
        for item in jobs
        if (not script_job_id or item.get("script_job_id") == script_job_id)
        and (variant_index < 0 or int(item.get("variant_index", -1)) == int(variant_index))
        and (shot_index < 0 or int(item.get("shot_index", -1)) == int(shot_index))
    ]
    return {"jobs": [_public_nova_reel_job(item) for item in filtered[:30]]}


@app.get("/api/jobs/{job_id}/download", dependencies=[Depends(_verify_access)])
def download(job_id: str):
    found = next((item for item in _load_jobs() if item.get("id") == job_id), None)
    if not found:
        raise HTTPException(status_code=404, detail="任务不存在。")
    if found.get("status") != "succeeded":
        raise HTTPException(status_code=400, detail="任务尚未完成。")
    data = _build_excel_bytes(found)
    model = re.sub(r"[^A-Za-z0-9_-]+", "_", str((found.get("request") or {}).get("model", "model")))[:50]
    return Response(
        data,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="video_script_{model}.xlsx"'},
    )

import base64
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
from fastapi import FastAPI, Depends, File, Header, HTTPException, Request, UploadFile
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
    discover_youtube_videos,
    extract_youtube_video_id,
    fetch_youtube_videos,
    normalize_social_url,
    normalize_youtube_video_item,
    refresh_social_thumbnail,
)
from storage_adapters import RuntimeStorage


APP_DATA_DIR = os.getenv("APP_DATA_DIR", ".")
os.makedirs(APP_DATA_DIR, exist_ok=True)
APP_BASE_PATH = "/" + os.getenv("APP_BASE_PATH", "").strip().strip("/")
if APP_BASE_PATH == "/":
    APP_BASE_PATH = ""

STORAGE = RuntimeStorage()
PRODUCT_FEATURE_STORE = ProductFeatureStore(STORAGE)

CACHE_META_KEY = "cache_meta.json"
HTTP_JOBS_KEY = "http_script_jobs.json"
NOVA_REEL_JOBS_KEY = "nova_reel_poc_jobs.json"
NOVA_CANVAS_JOBS_KEY = "nova_canvas_storyboard_jobs.json"
COMPETITOR_ASSETS_KEY = "competitor_assets.json"
SEED_COMPETITOR_ASSETS_KEY = "seed_competitor_assets.json"
COMPETITOR_RESEARCH_JOBS_KEY = "competitor_research_jobs.json"
HOTSPOTS_KEY = "hotspots.json"
HOTSPOT_SOURCES_KEY = "hotspot_sources.json"
COMPETITOR_CONFIGS_KEY = "competitor_configs.json"
COMPETITOR_COLLECTION_RUNS_KEY = "competitor_collection_runs.json"
TABLE_COLUMNS = [
    "结构分段",
    "功能点",
    "表现手法",
    "旁白（英文）",
    "字幕-显示卖点名及描述（英文）",
    "拍摄角度",
    "运镜方式",
    "竞品链接",
    "竞品盖帽",
    "时长",
]
TABLE_HEADER_LINE = "| " + " | ".join(TABLE_COLUMNS) + " |"
TABLE_SEPARATOR_LINE = "| " + " | ".join([":---"] * len(TABLE_COLUMNS)) + " |"
SYSTEM_PROMPT = f"""##角色
你是“海外爆款内容引擎”的资深海外短视频创意导演、社媒内容策划和可落地分镜编剧。你的任务不是平铺产品参数，而是把海信产品卖点转化为以产品和被处理物品为主角、细节突出、动作清晰、适合轻量拍摄的海外电商短视频脚本，并保持可拍摄、可执行、可导出为 Excel 的 Markdown 表格。

##创意质量原则
1. 先在内部完成创意策略，不要输出思考过程：明确目标观众、产品主视觉、核心卖点的可视化证据、开场 hook、结尾品牌记忆点。
2. 默认采用“产品/物品展示优先”：产品本体、局部细节、操作面板、内部空间、配件、食材/衣物/餐具等被处理对象和使用前后效果必须是画面中心。除非用户明确要求人物剧情，不要写专业模特、正脸表演、复杂家庭成员关系或多人出镜。
3. 人物不是必需元素；如需出现，只写手部、手臂、背影、越肩视角或生活痕迹。人物动作必须服务于产品操作，如按键、开门、取放、倒入、擦拭、摆放，不让人物抢主视觉。
4. 每个核心卖点都要变成“可看见的产品动作或物品状态变化”：例如食物状态变化、操作面板反馈、内部空间利用、收纳前后、清洁前后、传统方式 vs 本品方式、成品质感特写。
5. 微波炉、烤箱、空气炸锅等厨房电器必须优先生成“产品 + 食材/餐具/台面/蒸汽/成品状态”的可拍摄场景；如果用户没有给出重点，请自动选择 1-2 个最适合的物品场景，如忙碌早餐食材入炉、电影夜爆米花膨胀、剩饭复热冒气、冷冻食品解冻、热饮加热、快速出餐摆盘。
6. 表现手法必须具体到镜头动作和画面内容：不要写“展示产品功能”这种空话，要写“披萨放入腔体，手指按下触控面板，屏幕数字跳动，切到拉丝芝士和产品门体反光特写”这类产品清晰露出的可拍画面。
7. 每套方案必须明显不同：开场 hook、产品视角、物品状态、道具环境、镜头组织至少两处不同。避免三套都只是“产品特写 + 功能展示 + 品牌收尾”。
8. 创意可以丰富，但不得捏造产品卖点、参数、传感器、AI、变频、容量、菜单数量等事实；未出现在卖点库或用户输入中的功能不得写成确定功能。

##格式与语言硬约束
1. 第一输出必须是标准 Markdown 表格，绝对不要包裹在 ```markdown 或 ``` 代码块中。
2. 表格必须统一使用以下 10 列，并逐字使用该表头：
{TABLE_HEADER_LINE}
{TABLE_SEPARATOR_LINE}
3. 表格最后一行必须是“总时长”统计。
4. “旁白（英文）”和“字幕-显示卖点名及描述（英文）”两列必须是纯英文句子，不得带字段名/标签/括号前缀。
5. 其余列必须以中文为主，便于国内制作团队执行；允许少量 UI/LED/4K 等缩写。
6. 竞品链接/竞品盖帽字段保留；没有可用竞品链接时留空，不要编造。
7. 品牌收尾必须是产品静置全景特写 + Hisense Designed to Ease, Crafted to Cheer.

##额外输出
表格后必须追加：

整体AI视频生成Prompt（English）:
- 用一段完整英文描述整支视频的统一风格、镜头语言、光影、场景、产品露出、细节特写、道具/被处理对象、可选手部操作和品牌调性，且要与表格中的具体场景一致；不要描述专业模特或正脸表演。
- 必须包含：4k, cinematic lighting, shallow depth of field, smooth camera movement。
- 必须包含品牌收尾：Hisense Designed to Ease, Crafted to Cheer.

Negative Prompt（English，选填）:
- 输出一行即可。

Recommended Settings（选填）:
- 输出一行即可。
"""

BEDROCK_AWS_REGION = (
    os.getenv("BEDROCK_AWS_REGION")
    or os.getenv("AWS_REGION")
    or os.getenv("AWS_DEFAULT_REGION")
    or "us-east-1"
)
BEDROCK_MODEL_ID = os.getenv("BEDROCK_MODEL_ID", "eu.anthropic.claude-sonnet-4-5-20250929-v1:0")
BEDROCK_MODEL_FALLBACK_IDS = [
    model_id.strip()
    for model_id in os.getenv("BEDROCK_MODEL_FALLBACK_IDS", "eu.amazon.nova-pro-v1:0").split(",")
    if model_id.strip() and model_id.strip() != BEDROCK_MODEL_ID
]
BEDROCK_MAX_TOKENS = int(os.getenv("BEDROCK_MAX_TOKENS", "8192"))
BEDROCK_FALLBACK_MAX_TOKENS = int(os.getenv("BEDROCK_FALLBACK_MAX_TOKENS", "4096"))
NOVA_REEL_AWS_REGION = os.getenv("NOVA_REEL_AWS_REGION", "us-east-1")
NOVA_REEL_MODEL_ID = os.getenv("NOVA_REEL_MODEL_ID", "amazon.nova-reel-v1:1")
NOVA_REEL_OUTPUT_S3_URI = os.getenv("NOVA_REEL_OUTPUT_S3_URI", "").rstrip("/")
NOVA_REEL_ESTIMATED_USD_PER_SECOND = float(os.getenv("NOVA_REEL_ESTIMATED_USD_PER_SECOND", "0.08"))
NOVA_CANVAS_AWS_REGION = os.getenv("NOVA_CANVAS_AWS_REGION", "us-west-2")
NOVA_CANVAS_MODEL_ID = os.getenv("NOVA_CANVAS_MODEL_ID", "stability.sd3-5-large-v1:0")
NOVA_CANVAS_ESTIMATED_USD_PER_IMAGE = float(os.getenv("NOVA_CANVAS_ESTIMATED_USD_PER_IMAGE", "0.08"))
RAINFOREST_DEFAULT_AMAZON_DOMAIN = os.getenv("RAINFOREST_DEFAULT_AMAZON_DOMAIN", "amazon.com")
RAINFOREST_SEARCH_TOP_N = int(os.getenv("RAINFOREST_SEARCH_TOP_N", "8"))
RAINFOREST_DISCOVERY_REQUEST_LIMIT = int(os.getenv("RAINFOREST_DISCOVERY_REQUEST_LIMIT", "6"))
RAINFOREST_MAX_PRODUCTS_PER_REFRESH = int(os.getenv("RAINFOREST_MAX_PRODUCTS_PER_REFRESH", "30"))
RAINFOREST_REQUEST_TIMEOUT = int(os.getenv("RAINFOREST_REQUEST_TIMEOUT", "30"))
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
    if APP_BASE_PATH:
        path = request.scope.get("path") or ""
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
            for key in ("password", "APP_ACCESS_PASSWORD", "app_access_password", "value"):
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

job_lock = threading.Lock()
competitor_lock = threading.Lock()

static_dir = os.path.join(os.path.dirname(__file__), "web_frontend")
if os.path.isdir(static_dir):
    if APP_BASE_PATH:
        app.mount(f"{APP_BASE_PATH}/static", StaticFiles(directory=static_dir), name="static_prefixed")
    app.mount("/static", StaticFiles(directory=static_dir), name="static")

register_fridge_routes(app, STORAGE, _current_access_password, _clean_access_token, _access_control_active)


class GenerateRequest(BaseModel):
    platform: str = "TikTok / Reels / Shorts"
    target_market: str = "北美 (US/CA)"
    variant_count: int = Field(default=2, ge=1, le=3)
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
    prompt: str = Field(min_length=10, max_length=2000)


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
    target_market: str = "北美 (US/CA)"
    platform: str = ""
    source: str = ""
    brands: list[str] = Field(default_factory=list, max_length=80)
    keywords: list[str] = Field(default_factory=list, max_length=80)
    status: str = "queued"
    error_message: str = ""


def _read_json(key, default_value):
    return STORAGE.read_json(key, default_value)


def _write_json(key, payload):
    return STORAGE.write_json(key, payload)


def _load_products() -> pd.DataFrame:
    return PRODUCT_FEATURE_STORE.load()


def _load_jobs():
    data = _read_json(HTTP_JOBS_KEY, [])
    return data if isinstance(data, list) else []


def _save_jobs(jobs):
    return _write_json(HTTP_JOBS_KEY, jobs[:100])


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


def _load_seed_competitor_assets():
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


def _public_competitor_asset(asset, include_source_payload=False):
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
            str(asset.get("original_copy") or ""),
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
    category_lower = str(category or "").strip().lower()
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
        asset = _ensure_asset_admin_defaults(asset)
        search_text = _competitor_asset_search_text(asset)
        if q_lower and q_lower not in search_text:
            continue
        if category_lower and category_lower not in str(asset.get("category") or "").lower() and category_lower not in search_text:
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
    else:
        matched.sort(
            key=lambda item: (
                int(item.get("quality_score") or 0),
                str(item.get("collected_at") or item.get("updated_at") or ""),
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


def _strip_code_fences(text: str) -> str:
    cleaned = str(text or "").strip()
    cleaned = re.sub(r"^\s*```(?:markdown|md)?\s*", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\s*```\s*$", "", cleaned)
    return cleaned.strip()


def _has_expected_table(content: str) -> bool:
    text = str(content or "")
    return TABLE_HEADER_LINE in text and TABLE_SEPARATOR_LINE in text and "总时长" in text


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
    for column in TABLE_COLUMNS:
        if column not in df.columns:
            df[column] = ""
    return df[TABLE_COLUMNS]


def _infer_variant_label(content: str) -> str:
    text = str(content or "")
    if re.search(r"痛点|烦恼|麻烦|困扰|对比|前后", text, flags=re.IGNORECASE):
        return "偏痛点转化"
    if re.search(r"场景|生活|家庭|厨房|日常", text, flags=re.IGNORECASE):
        return "场景化种草"
    if re.search(r"功能|展示|介绍|演示|操作", text, flags=re.IGNORECASE):
        return "功能展示"
    return "通用脚本"


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
        return "竞品素材上下文：当前没有已审核可引用的竞品素材；竞品链接和竞品盖帽两列请留空，不要编造。"
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
        "竞品素材上下文（只能引用下列链接；不得编造播放量、投放效果、竞品参数）：\n"
        + "\n".join(lines)
        + "\n请在合适镜头的“竞品链接”列填写 1-3 条上述链接，并在“竞品盖帽”列基于本品卖点写对标表达。"
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


def _build_prompt(req: GenerateRequest, features: list[dict], variant_index: int, context_snapshot: dict | None = None) -> str:
    feature_lines = "\n".join(
        f"- {item['name']}: {item['tagline']}。{item['description']}" for item in features
    )
    direction = req.video_type[variant_index % len(req.video_type)] if req.video_type else "场景化/生活方式型"
    variant_no = variant_index + 1
    context_snapshot = context_snapshot or {"competitor_assets": [], "hotspots": []}
    competitor_context = _competitor_context_prompt(context_snapshot.get("competitor_assets") or [])
    hotspot_context = _hotspot_context_prompt(context_snapshot.get("hotspots") or [])
    return f"""
请生成【方案{variant_no}】海外电商短视频脚本（只输出这一套，不要输出其他方案标题）。
- 必须先输出一张符合系统要求的 Markdown 表格（10列，行内时长为秒，最后一行为总时长）。
- 表格必须包含并使用如下表头（逐字一致）：
{TABLE_HEADER_LINE}
- 表格后紧接着输出：整体AI视频生成Prompt（English）/ Negative Prompt / Recommended Settings。
- 与其他方案保持明显差异：开场 hook、产品视角、物品状态、表现手法、镜头组织至少两处不同。
- 先在内部确定本方案的创意策略，但不要输出策略过程：方案1偏生活痛点开场，方案2偏社媒种草/情绪反差，方案3偏快节奏功能挑战；如果只生成1-2套，也必须让每套的产品切入角度、被处理物品状态和镜头组织不同。
- 默认拍摄策略：产品/物品展示优先，淡化人物角色；除非补充要求明确需要人物剧情，不要设计专业模特、正脸表演或多人关系。
- 人物处理：可用手部/手臂/背影/越肩视角完成开门、按键、取放、摆放、擦拭等操作；不要让人物成为画面主角。
- 场景优先级：厨房电器优先写“产品 + 食材/餐具/台面/蒸汽/成品状态”的互动；冰箱/洗衣机/洗碗机等家电优先写“产品内部空间 + 被处理物品 + 使用前后结果”的可视化过程。
- 表现手法必须落到产品可见动作和物品状态变化，不要只写“展示功能/突出卖点/产品特写”；每行至少包含一个产品本体或被处理物品的可拍动作、一个道具或环境细节、一个镜头处理。
- 竞品链接与竞品盖帽：只能基于“竞品素材上下文”引用真实链接；没有上下文时两列留空。
- 语言强约束：除【旁白（英文）】与【字幕-显示卖点名及描述（英文）】两列外，其余列必须以中文为主。
- 英文列格式强约束：旁白和字幕两列不得带任何字段名/标签/括号前缀，直接输出纯英文句子。
- 卖点事实强约束：不得加入核心卖点中没有出现的功能概念或参数。

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


def _call_bedrock(prompt: str, temperature=0.7, top_p=0.9) -> str:
    client = boto3.client("bedrock-runtime", region_name=BEDROCK_AWS_REGION)
    model_ids = [BEDROCK_MODEL_ID, *BEDROCK_MODEL_FALLBACK_IDS]
    last_error = None
    for model_id in model_ids:
        try:
            response = client.converse(
                modelId=model_id,
                system=[{"text": SYSTEM_PROMPT}],
                messages=[{"role": "user", "content": [{"text": prompt}]}],
                inferenceConfig={
                    "maxTokens": _bedrock_max_tokens_for_model(model_id, BEDROCK_MAX_TOKENS),
                    "temperature": temperature,
                    "topP": top_p,
                },
            )
            return response["output"]["message"]["content"][0]["text"]
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


def _nova_canvas_image_key(script_job_id, variant_index, shot_index):
    stamp = dt.datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
    return (
        "nova-canvas-storyboards/"
        f"{_safe_ascii_slug(script_job_id)}/"
        f"variant_{int(variant_index) + 1}_shot_{int(shot_index) + 1}_{stamp}_{uuid.uuid4().hex[:8]}.png"
    )


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


def _storyboard_category_context(category, model, detection_text=""):
    raw = f"{category or ''} {model or ''} {detection_text or ''}".lower()
    if _is_laundry_storyboard(raw):
        return {
            "subject": "Hisense front-loading washer-dryer combo or front-loading laundry appliances",
            "setting": "a modern laundry room or utility room, never a kitchen",
            "must": (
                "The frame must clearly show front-loading laundry appliances and laundry-room cues such as folded towels, "
                "a laundry basket, hanging clothes, or a utility shelf as relevant to the shot."
            ),
            "negative": (
                "kitchen, stove, stovetop, oven, microwave, refrigerator, kettle, cookware, food preparation, "
                "dining room, cooking appliance, kitchen island"
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
    if _storyboard_requires_user(raw):
        constraints.append(
            "A visible adult user must be present in the foreground and actively interacting with the product or the laundry task."
        )
    if _storyboard_switching_laundry(raw):
        constraints.append(
            "Show the pain point literally: the user is centered between two front-loading laundry machines, holding a laundry basket full of wet clothes and moving clothes from washer to dryer."
        )
    if "top-down" in lower or "overhead" in lower or "\u4fef\u62cd" in raw:
        constraints.append("Use a high-angle overhead or three-quarter top-down camera view, not a straight eye-level kitchen view.")
    if "fixed" in lower or "\u56fa\u5b9a" in raw:
        constraints.append("Use a stable locked-off composition with no motion blur.")
    return " ".join(constraints)


def _storyboard_requires_user(prompt):
    raw = str(prompt or "")
    lower = raw.lower()
    return any(token in lower for token in ("user", "person", "woman", "man")) or "\u7528\u6237" in raw


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


def _enhance_storyboard_image_prompt(prompt, category="", model="", shot_index=0):
    raw_prompt = str(prompt or "").strip()[:900]
    context = _storyboard_category_context(category, model, detection_text=raw_prompt)
    action_constraints = _storyboard_action_constraints(raw_prompt)
    lines = [
        "Create one premium 16:9 photorealistic e-commerce storyboard still.",
        "Hard requirement: depict the exact storyboard row below; do not substitute another product, room, or action.",
        f"Required product and place: {context['subject']} in {context['setting']}.",
        f"Shot number: {int(shot_index) + 1}.",
        f"Mandatory primary scene: {context['must']}",
        f"Camera and action constraints: {action_constraints}" if action_constraints else "",
        "The selected product must occupy the foreground or visual center and remain recognizable.",
        f"Storyboard details to preserve: {raw_prompt}",
        f"Do not show: {context['negative']}.",
        (
            "Visual style: clean commercial lighting, realistic product proportions, product-focused composition, "
            "natural colors, no UI mockups, no text overlay, no watermarks, no competitor brands, no wrong product category."
        ),
    ]
    return "\n".join(line for line in lines if line).strip()[:3000]


def _image_negative_prompt(prompt, category="", model=""):
    context = _storyboard_category_context(category, model, detection_text=prompt)
    terms = [
        "competitor brands",
        "distorted logo",
        "unreadable text",
        "text overlay",
        "watermark",
        "low quality",
        "blurry",
        "cartoon",
        "cgi look",
        "deformed product",
        "extra products",
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
        terms.extend(["three washing machines", "more than two laundry machines", "appliance showroom lineup"])
    return ", ".join(item for item in terms if item)


def _bedrock_image_request_body(prompt, seed, category="", model=""):
    model_id = str(NOVA_CANVAS_MODEL_ID or "")
    prompt_text = str(prompt or "premium e-commerce storyboard reference image")[:3000]
    negative_prompt = _image_negative_prompt(prompt_text, category=category, model=model)
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


def _start_nova_canvas_image(prompt, script_job_id, variant_index, shot_index, category="", model=""):
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

            body = _bedrock_image_request_body(prompt, seed, category=category, model=model)
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

    image_key = _nova_canvas_image_key(script_job_id, variant_index, shot_index)
    image_uri = STORAGE.write_file_bytes(image_key, image_bytes, content_type="image/png")
    return image_key, image_uri, seed


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


def _repair_to_expected_table(original_content: str, req: GenerateRequest, features: list[dict]) -> str:
    feature_lines = "\n".join(
        f"- {item['name']}: {item['tagline']}。{item['description']}" for item in features
    )
    repair_prompt = f"""
请把下面这段视频脚本内容改写成固定字段的 Markdown 表格。

硬性要求：
1. 第一行必须逐字等于：
{TABLE_HEADER_LINE}
2. 第二行必须逐字等于：
{TABLE_SEPARATOR_LINE}
3. 后续每一行都必须有且只有 10 个字段，字段顺序不得变更、不得新增、不得删除。
4. 字段必须保持为：{", ".join(TABLE_COLUMNS)}
5. 最后一行必须是“总时长”统计。
6. 只输出表格和表格后的“整体AI视频生成Prompt（English）/ Negative Prompt / Recommended Settings”，不要输出解释。
7. 旁白（英文）和字幕-显示卖点名及描述（英文）两列必须是英文，其余列以中文为主。
8. 不要编造产品卖点；如没有竞品链接，竞品链接和竞品盖帽留空。

产品信息：
- 产品品类：{req.category}
- 产品型号：{req.model}
- 期望时长：{req.expected_duration} 秒
- 脚本方向：{" / ".join(req.video_type or [])}
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
            table_lines, remainder = _extract_first_md_table(variant.get("content", ""))
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
            prompt = _build_prompt(req, features, i, context_snapshot=context_snapshot)
            _update_job(job_id, progress=int((i / total) * 80) + 10, current_step=f"生成方案 {i + 1}/{total}")
            content = _strip_code_fences(_call_bedrock(prompt))
            if not _has_expected_table(content):
                retry_prompt = prompt + "\n\n补充要求：输出必须完整，不要截断；若篇幅过长请压缩行文但保留完整表格与总时长行。"
                retry_content = _strip_code_fences(_call_bedrock(retry_prompt, temperature=0.3, top_p=0.8))
                if _has_expected_table(retry_content):
                    content = retry_content
            if not _has_expected_table(content):
                _update_job(job_id, progress=int((i / total) * 80) + 15, current_step=f"修复方案 {i + 1} 为表格格式")
                repaired = _repair_to_expected_table(content, req, features)
                if _has_expected_table(repaired):
                    content = repaired
            variants.append({"name": f"方案{i + 1}", "label": _infer_variant_label(content), "content": content.strip()})
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
        lines.append(
            "\n".join(
                [
                    f"[{index}] {asset.get('brand') or 'Unknown'} | {asset.get('title') or 'Untitled'}",
                    f"- {source_id}",
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


def _run_competitor_research_job(job_id: str):
    job = next((item for item in _load_competitor_research_jobs() if item.get("id") == job_id), None)
    if not job:
        return
    try:
        req = CompetitorResearchRequest(**(job.get("request") or {}))
        _update_competitor_research_job(job_id, status="running", progress=25, current_step="正在检索素材证据")
        assets, _ = _search_competitor_assets(
            q=req.question,
            category=req.category,
            platform=req.platform,
            source=req.source,
            limit=req.top_k,
        )
        if not assets and req.category:
            assets, _ = _search_competitor_assets(category=req.category, platform=req.platform, source=req.source, limit=req.top_k)
        if not assets and (req.platform or req.source):
            assets, _ = _search_competitor_assets(platform=req.platform, source=req.source, limit=req.top_k)
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


@app.get("/", response_class=HTMLResponse)
def index():
    path = os.path.join(static_dir, "index.html")
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as handle:
            html = handle.read()
            html = html.replace("__APP_BASE_PATH__", APP_BASE_PATH)
            html = html.replace("__BASE_PATH__", APP_BASE_PATH)
            return HTMLResponse(
                html,
                headers={"Cache-Control": "no-store, max-age=0"},
            )
    return HTMLResponse(
        "<h1>海外爆款内容引擎 API</h1>",
        headers={"Cache-Control": "no-store, max-age=0"},
    )


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
    assets = [_ensure_asset_admin_defaults(item) for item in _load_competitor_assets()]
    hotspots, active_hotspot_total = _search_hotspots(active_only=True, limit=6)
    latest_assets = sorted(assets, key=lambda item: str(item.get("updated_at") or item.get("collected_at") or ""), reverse=True)[:6]
    recent_runs = _load_collection_runs()[:8]
    video_asset_count = sum(1 for item in assets if _asset_media_types(item) & {"video", "gif"})
    today_asset_count = sum(1 for item in assets if (_parse_date(item.get("collected_at") or item.get("created_at")) == today))
    pending_review_count = sum(1 for item in assets if str(item.get("review_status") or "") in {"auto_collected", "needs_review"})
    failed_run_count = sum(1 for item in recent_runs if str(item.get("status") or "").lower() == "failed")
    return {
        "metrics": {
            "asset_count": len(assets),
            "video_gif_count": video_asset_count,
            "today_new_assets": today_asset_count,
            "pending_review_count": pending_review_count,
            "active_hotspot_count": active_hotspot_total,
            "recent_failed_runs": failed_run_count,
        },
        "latest_assets": [_public_competitor_asset(item) for item in latest_assets],
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
    return {"jobs": _load_jobs()[:30]}


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
        raise HTTPException(status_code=400, detail="请先输入至少一个社媒素材 URL。")

    token = _current_social_oembed_token()
    normalized_assets = []
    errors = []
    for url in urls[:50]:
        try:
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
        except Exception as exc:
            errors.append({"url": url, "error": str(exc)})

    upsert = _upsert_competitor_assets(normalized_assets)
    return {
        "ok": True,
        "requested_url_count": len(urls),
        "imported_count": len(normalized_assets),
        "upsert": upsert,
        "errors": errors[:20],
        "assets": [_public_competitor_asset(item) for item in normalized_assets],
        "note": "社媒 URL 入库仅保存链接、缩略图 URL、嵌入信息和结构化分析；不会下载或保存竞品视频原片。",
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
        "assets": [_public_competitor_asset(item) for item in assets],
        "count": len(assets),
        "total": total,
        "offset": max(0, int(offset or 0)),
        "limit": limit,
    }


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
    return {"runs": _load_collection_runs()[: max(1, min(int(limit or 50), 200))]}


@app.post("/api/competitor-collection-runs", dependencies=[Depends(_verify_access)])
def create_competitor_collection_run(req: CompetitorCollectionRunRequest):
    now = _utc_now()
    run = {
        "id": uuid.uuid4().hex[:12],
        "created_at": now,
        "updated_at": now,
        "request": req.model_dump(),
        "status": req.status,
        "error_message": req.error_message,
        "note": "首版记录采集配置与运行状态；实际自动采集由 Rainforest/YouTube 刷新接口或外部调度触发。",
    }
    runs = _load_collection_runs()
    runs.insert(0, run)
    _save_collection_runs(runs)
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


@app.get("/api/nova-canvas/jobs", dependencies=[Depends(_verify_access)])
def nova_canvas_jobs(script_job_id: str = "", variant_index: int = -1):
    jobs = _load_nova_canvas_jobs()
    if script_job_id:
        jobs = [item for item in jobs if item.get("script_job_id") == script_job_id]
    if variant_index >= 0:
        jobs = [item for item in jobs if int(item.get("variant_index", -1)) == int(variant_index)]
    return {
        "jobs": [_public_nova_canvas_job(item) for item in jobs[:80]],
        "model_id": NOVA_CANVAS_MODEL_ID,
        "region": NOVA_CANVAS_AWS_REGION,
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
    generation_prompt = _enhance_storyboard_image_prompt(
        raw_prompt,
        category=request_payload.get("category", ""),
        model=request_payload.get("model", ""),
        shot_index=req.shot_index,
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
        "prompt": raw_prompt,
        "generation_prompt": generation_prompt,
        "status": "succeeded",
        "failure_message": "",
        "image_key": "",
        "image_uri": "",
        "model_id": NOVA_CANVAS_MODEL_ID,
        "region": NOVA_CANVAS_AWS_REGION,
        "seed": None,
    }
    try:
        image_key, image_uri, seed = _start_nova_canvas_image(
            generation_prompt,
            req.script_job_id,
            req.variant_index,
            req.shot_index,
            category=image_job["category"],
            model=image_job["model"],
        )
        image_job["image_key"] = image_key
        image_job["image_uri"] = image_uri
        image_job["seed"] = seed
    except Exception as exc:
        image_job["status"] = "failed"
        image_job["failure_message"] = str(exc)
        with job_lock:
            jobs = _load_nova_canvas_jobs()
            jobs.insert(0, image_job)
            _save_nova_canvas_jobs(jobs)
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    with job_lock:
        jobs = _load_nova_canvas_jobs()
        jobs.insert(0, image_job)
        _save_nova_canvas_jobs(jobs)
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

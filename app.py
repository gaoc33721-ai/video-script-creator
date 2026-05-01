import streamlit as st
import requests
import os
import pandas as pd
import datetime as dt
import io
import random
import re
import hmac
import time
import hashlib
import xml.etree.ElementTree as ET
from html import escape as _html_escape
import urllib.parse
import uuid
import threading

from product_feature_store import ProductFeatureStore, filter_product_features
from storage_adapters import RuntimeStorage

try:
    from dotenv import load_dotenv
    load_dotenv()
except ModuleNotFoundError:
    load_dotenv = None

# 运行时数据目录：本地默认写入仓库内，AWS 部署时可切换为 S3/RDS。
APP_DATA_DIR = os.getenv("APP_DATA_DIR", ".")
os.makedirs(APP_DATA_DIR, exist_ok=True)

STORAGE = RuntimeStorage()
PRODUCT_FEATURE_STORE = ProductFeatureStore(STORAGE)

CACHE_META_KEY = "cache_meta.json"
HISTORY_KEY = "script_history.json"
FEEDBACK_KEY = "trial_feedback.json"
NOVA_REEL_POC_JOBS_KEY = "nova_reel_poc_jobs.json"
SCRIPT_JOBS_KEY = "script_generation_jobs.json"

BEDROCK_AWS_REGION = (
    os.getenv("BEDROCK_AWS_REGION")
    or os.getenv("AWS_REGION")
    or os.getenv("AWS_DEFAULT_REGION")
    or "us-east-1"
)
BEDROCK_MODEL_ID = os.getenv("BEDROCK_MODEL_ID", "eu.amazon.nova-pro-v1:0")
BEDROCK_MAX_TOKENS = int(os.getenv("BEDROCK_MAX_TOKENS", "4096"))
NOVA_REEL_AWS_REGION = os.getenv("NOVA_REEL_AWS_REGION", "us-east-1")
NOVA_REEL_MODEL_ID = os.getenv("NOVA_REEL_MODEL_ID", "amazon.nova-reel-v1:1")
NOVA_REEL_OUTPUT_S3_URI = os.getenv("NOVA_REEL_OUTPUT_S3_URI", "").rstrip("/")
NOVA_REEL_ESTIMATED_USD_PER_SECOND = float(os.getenv("NOVA_REEL_ESTIMATED_USD_PER_SECOND", "0.08"))
NOVA_REEL_MAX_SUBMISSIONS_PER_CLICK = int(os.getenv("NOVA_REEL_MAX_SUBMISSIONS_PER_CLICK", "2"))
APP_ACCESS_PASSWORD = os.getenv("APP_ACCESS_PASSWORD", "")
APP_ACCESS_PASSWORD_SECRET_ID = os.getenv("APP_ACCESS_PASSWORD_SECRET_ID", "")
APP_ACCESS_CONTROL_ENABLED = os.getenv("APP_ACCESS_CONTROL_ENABLED", "false").strip().lower() in {"1", "true", "yes", "on"}
APP_ACCESS_PASSWORD_CACHE_TTL = int(os.getenv("APP_ACCESS_PASSWORD_CACHE_TTL", "300"))
APP_ACCESS_PASSWORD_FETCH_TIMEOUT = int(os.getenv("APP_ACCESS_PASSWORD_FETCH_TIMEOUT", "2"))
_ACCESS_PASSWORD_CACHE = {"value": None, "expires_at": 0, "error": ""}

OWN_BRAND_ALIASES = {"hisense", "海信"}

COMPETITOR_BRAND_POOL = {
    "空气炸锅": ["Ninja", "COSORI", "Philips", "Instant", "Breville"],
    "微波炉": ["Panasonic", "LG", "Samsung", "Toshiba", "Sharp"],
    "冰箱": ["Samsung", "LG", "Whirlpool", "GE", "Haier", "Bosch"],
    "洗衣机": ["LG", "Samsung", "Whirlpool", "GE", "Bosch"],
    "洗碗机": ["Bosch", "Whirlpool", "GE", "LG", "Samsung"],
    "烤箱": ["Bosch", "Samsung", "LG", "Whirlpool", "Breville"],
    "空调": ["Daikin", "Mitsubishi Electric", "LG", "Samsung", "Gree", "Midea"],
    "电视": ["Samsung", "LG", "Sony", "TCL"],
}

CATEGORY_QUERY_TERMS = {
    "空气炸锅": ["air fryer", "airfryer"],
    "微波炉": ["microwave"],
    "冰箱": ["refrigerator", "fridge", "french door refrigerator", "side by side refrigerator"],
    "洗衣机": ["washing machine", "washer", "laundry"],
    "洗碗机": ["dishwasher"],
    "烤箱": ["oven", "electric oven"],
    "空调": ["air conditioner", "AC"],
    "电视": ["TV", "television"],
}

CATEGORY_ALIASES = [
    ("refrigerator", "冰箱"),
    ("fridge", "冰箱"),
    ("tv", "电视"),
    ("television", "电视"),
    ("washing machine", "洗衣机"),
    ("washer", "洗衣机"),
    ("laundry", "洗衣机"),
    ("dishwasher", "洗碗机"),
    ("air fryer", "空气炸锅"),
    ("airfryer", "空气炸锅"),
    ("microwave", "微波炉"),
    ("oven", "烤箱"),
    ("air conditioner", "空调"),
    ("ac", "空调"),
]

DEFAULT_PAIN_POINTS = {
    "空气炸锅": "想吃炸物但担心高油高脂；忙碌时不想开火做饭；清洁麻烦、油烟大；一次做不够家人/聚会份量",
    "微波炉": "解冻不均匀、外热内冷；加热后食物发干口感差；操作复杂、火力档位不清晰；空间占用大、不好清洁",
    "冰箱": "食材存放易串味；保鲜时间短、蔬果易蔫；容量不够、分区不合理；噪音大、能耗高；开门找东西麻烦",
    "洗衣机": "洗不干净顽固污渍；大件衣物/被子不好洗；衣物易缠绕、易损伤；噪音/震动大；烘干不透、容易返潮",
    "洗碗机": "手洗费时费水、油污难洗；担心洗不干净/有残留；锅具大件难摆放；烘干不彻底有水渍；安装/维护麻烦",
    "烤箱": "温度不准导致烤不均匀；预热慢、时间不好掌握；容量不足一次烤不够；油污难清洁；新手不懂模式选择",
    "空调": "制冷/制热慢；温差大吹着不舒服；噪音影响睡眠；耗电高；异味/清洁维护麻烦；不同房间覆盖不均",
    "电视": "画面不够清晰/拖影影响观影和游戏；音效不够沉浸；反光严重白天看不清；接口不够/连接麻烦；系统卡顿广告多",
}

DEFAULT_TARGET_AUDIENCE = {
    "空气炸锅": "忙碌的年轻上班族/学生；健身控/轻食人群；小家庭用户",
    "微波炉": "快节奏上班族；租房党/小户型用户；有娃家庭",
    "冰箱": "家庭用户；精细化备餐人群；爱囤货用户",
    "洗衣机": "家庭用户；有娃家庭；健身/通勤高频换洗人群",
    "洗碗机": "有娃家庭；双职工家庭；不愿手洗油污的人群",
    "烤箱": "烘焙新手；家庭用户；周末做饭/聚会人群",
    "空调": "对睡眠舒适度敏感的人群；家庭用户；小户型租房用户",
    "电视": "家庭观影人群；游戏玩家；追剧党",
}

COMPETITOR_VIDEO_REFERENCES = {
    "空气炸锅": [
        {
            "brand": "Ninja",
            "title": "Ninja Air Fryers（产品宣传示例）",
            "url": "https://www.youtube.com/watch?v=6TryhZRuZBs",
            "focus_points": ["空气炸技术/快速脆炸", "多功能模式", "家庭份量/大容量诉求"],
        },
        {
            "brand": "COSORI",
            "title": "COSORI（官方频道）",
            "url": "https://m.youtube.com/c/Cosori/videos",
            "focus_points": ["配方/菜单内容", "易上手操作", "外观与场景化内容"],
        },
        {
            "brand": "Philips",
            "title": "Philips Airfryer（设置与使用示例）",
            "url": "https://m.youtube.com/watch?v=MOjKt3MfXNs",
            "focus_points": ["使用方法/教程向", "健康少油定位", "稳定品质心智"],
        },
        {
            "brand": "Instant",
            "title": "Instant Vortex ClearCook（产品演示示例）",
            "url": "https://m.youtube.com/watch?v=NeN9yBfTVNo",
            "focus_points": ["可视化烹饪/可视窗口", "快速出餐节奏", "场景演示"],
        },
    ],
    "微波炉": [],
}

def _safe_read_json(key, default_value):
    return STORAGE.read_json(key, default_value)

def _safe_write_json(key, payload):
    return STORAGE.write_json(key, payload)

def load_cache_meta():
    return _safe_read_json(CACHE_META_KEY, {})

def save_cache_meta(file_name, df):
    payload = {
        "file_name": file_name,
        "row_count": int(len(df)) if df is not None else 0,
        "model_count": int(df["model"].nunique()) if (df is not None and "model" in df.columns) else 0,
        "category_count": int(df["Category"].nunique()) if (df is not None and "Category" in df.columns) else 0,
        "updated_at": dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }
    return _safe_write_json(CACHE_META_KEY, payload)

def load_history_records():
    data = _safe_read_json(HISTORY_KEY, [])
    return data if isinstance(data, list) else []

def append_history_record(record, limit=12):
    records = load_history_records()
    records.insert(0, record)
    records = records[:limit]
    return _safe_write_json(HISTORY_KEY, records)

def save_feedback_record(record, limit=200):
    records = _safe_read_json(FEEDBACK_KEY, [])
    if not isinstance(records, list):
        records = []
    records.insert(0, record)
    records = records[:limit]
    return _safe_write_json(FEEDBACK_KEY, records)

def load_nova_reel_poc_jobs():
    data = _safe_read_json(NOVA_REEL_POC_JOBS_KEY, [])
    return data if isinstance(data, list) else []

def save_nova_reel_poc_jobs(records, limit=200):
    if not isinstance(records, list):
        records = []
    records = records[:limit]
    return _safe_write_json(NOVA_REEL_POC_JOBS_KEY, records)

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

def _poc_category_models(df_products, categories=None, max_models_per_category=2):
    categories = categories or ["烤箱", "微波炉", "空气炸锅"]
    targets = []
    if df_products.empty or "Category" not in df_products.columns or "model" not in df_products.columns:
        return targets
    for category in categories:
        matched = df_products[df_products["Category"].astype(str).str.contains(category, na=False)]
        models = []
        if not matched.empty:
            models = [
                str(x).strip()
                for x in matched["model"].dropna().astype(str).tolist()
                if str(x).strip()
            ]
            models = list(dict.fromkeys(models))[:max_models_per_category]
        targets.append({"category": category, "models": models})
    return targets

def _feature_summary_for_model(df_products, model, limit=3):
    if df_products.empty or not model:
        return []
    rows = df_products[df_products["model"].astype(str) == str(model)]
    features = []
    for _, row in rows.head(12).iterrows():
        name = str(row.get("Feature Name", "") or "").strip()
        desc = str(row.get("Feature Description", "") or "").strip()
        tagline = str(row.get("Tagline", "") or "").strip()
        text = " - ".join([x for x in [name, tagline, desc] if x])
        if text:
            features.append(text)
    return list(dict.fromkeys(features))[:limit]

def build_nova_reel_prompt(category, model, features):
    category_en = {
        "烤箱": "built-in oven",
        "微波炉": "microwave oven",
        "空气炸锅": "air fryer",
    }.get(category, "home appliance")
    feature_text = "; ".join(features[:3]) if features else "clean design, practical everyday cooking benefit"
    prompt = (
        f"Six-second premium e-commerce product video for a Hisense {category_en}, model {model}. "
        f"Show a modern bright kitchen, realistic product beauty shot, smooth slow dolly-in camera movement, "
        f"cinematic soft daylight, clean countertop, premium home lifestyle mood. "
        f"Highlight these product benefits visually: {feature_text}. "
        "No text overlay, no logo distortion, no extra brands, no people close-up, realistic product proportions."
    )
    return prompt[:500]

def extract_variant_video_prompt(content):
    text = str(content or "").strip()
    if not text:
        return ""
    match = re.search(
        r"整体AI视频生成Prompt（English）\s*:\s*(.*?)(?:\n\s*Negative Prompt|\n\s*Recommended Settings|$)",
        text,
        flags=re.IGNORECASE | re.DOTALL,
    )
    if not match:
        match = re.search(
            r"Overall AI Video Generation Prompt\s*:\s*(.*?)(?:\n\s*Negative Prompt|\n\s*Recommended Settings|$)",
            text,
            flags=re.IGNORECASE | re.DOTALL,
        )
    if not match:
        return ""
    prompt = re.sub(r"^\s*[-•]\s*", "", match.group(1).strip(), flags=re.MULTILINE)
    prompt = re.sub(r"\s+", " ", prompt).strip()
    return prompt[:500]

def build_variant_nova_reel_prompt(variant, category, model, selected_features):
    extracted = extract_variant_video_prompt((variant or {}).get("content", ""))
    if extracted:
        return extracted
    feature_text = "; ".join([str(x) for x in selected_features if str(x).strip()]) or "product benefits and lifestyle usage"
    category_en = {
        "烤箱": "built-in oven",
        "微波炉": "microwave oven",
        "空气炸锅": "air fryer",
    }.get(_category_key(category), "home appliance")
    return (
        f"Six-second premium e-commerce reference video for a Hisense {category_en}, model {model}. "
        f"Show a realistic product-focused scene based on this script variant, highlighting: {feature_text}. "
        "Modern bright kitchen, cinematic soft daylight, smooth camera movement, realistic product proportions, "
        "no text overlay, no logo distortion, no extra brands."
    )[:500]

def _nova_reel_job_output_uri(category, model):
    base_uri = _s3_output_base_uri()
    if not base_uri:
        return ""
    stamp = dt.datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
    # Bedrock video async output expects an S3 bucket or directory URI.
    # Keep the generated directory ASCII-only and slash-terminated for stricter service validation.
    return f"{base_uri.rstrip('/')}/{stamp}_{_safe_ascii_slug(category)}_{_safe_ascii_slug(model)}_{uuid.uuid4().hex[:8]}/"

def start_nova_reel_job(category, model, prompt, duration_seconds=6):
    output_s3_uri = _nova_reel_job_output_uri(category, model)
    if not output_s3_uri:
        raise RuntimeError("未配置 Nova Reel 输出 S3。请设置 STORAGE_BACKEND=s3/S3_BUCKET，或设置 NOVA_REEL_OUTPUT_S3_URI。")

    import boto3
    from botocore.config import Config

    client = boto3.client(
        "bedrock-runtime",
        region_name=NOVA_REEL_AWS_REGION,
        config=Config(connect_timeout=5, read_timeout=20, retries={"max_attempts": 2}),
    )
    model_input = {
        "taskType": "TEXT_VIDEO",
        "textToVideoParams": {"text": prompt},
        "videoGenerationConfig": {
            "durationSeconds": int(duration_seconds),
            "fps": 24,
            "dimension": "1280x720",
            "seed": random.randint(0, 2147483646),
        },
    }
    response = client.start_async_invoke(
        modelId=NOVA_REEL_MODEL_ID,
        modelInput=model_input,
        outputDataConfig={"s3OutputDataConfig": {"s3Uri": output_s3_uri}},
        clientRequestToken=str(uuid.uuid4()),
    )
    return response["invocationArn"], output_s3_uri

def query_nova_reel_job(invocation_arn):
    import boto3
    from botocore.config import Config

    client = boto3.client(
        "bedrock-runtime",
        region_name=NOVA_REEL_AWS_REGION,
        config=Config(connect_timeout=5, read_timeout=20, retries={"max_attempts": 2}),
    )
    return client.get_async_invoke(invocationArn=invocation_arn)

def _video_uri_from_job(job):
    output_uri = (
        ((job or {}).get("outputDataConfig") or {})
        .get("s3OutputDataConfig", {})
        .get("s3Uri", "")
    )
    if not output_uri:
        output_uri = (job or {}).get("output_s3_uri", "")
    return f"{output_uri.rstrip('/')}/output.mp4" if output_uri else ""

def _presigned_url_for_s3_uri(s3_uri, expires_in=3600):
    if not s3_uri.startswith("s3://"):
        return ""
    parsed = urllib.parse.urlparse(s3_uri)
    bucket = parsed.netloc
    key = parsed.path.lstrip("/")
    if not bucket or not key:
        return ""
    try:
        import boto3

        client = boto3.client("s3", region_name=os.getenv("AWS_REGION") or os.getenv("AWS_DEFAULT_REGION"))
        return client.generate_presigned_url(
            "get_object",
            Params={"Bucket": bucket, "Key": key},
            ExpiresIn=expires_in,
        )
    except Exception:
        return ""

def _nth_weekday_of_month(year, month, weekday, n):
    first = dt.date(year, month, 1)
    shift = (weekday - first.weekday()) % 7
    day = 1 + shift + (n - 1) * 7
    return dt.date(year, month, day)

def _get_black_friday(year):
    return _nth_weekday_of_month(year, 11, 3, 4)

def _market_key(target_market):
    if not target_market:
        return "OTHER"
    if "北美" in target_market:
        return "NA"
    if "欧洲" in target_market:
        return "EU"
    if "东南亚" in target_market:
        return "SEA"
    return "OTHER"

def get_upcoming_nodes(market_key, publish_date, limit=4):
    if not publish_date:
        publish_date = dt.date.today()
    year = publish_date.year

    def _fixed(mm, dd):
        return dt.date(year, mm, dd)

    candidates = []

    candidates.extend([
        ("New Year", _fixed(1, 1)),
        ("Valentine's Day", _fixed(2, 14)),
        ("Halloween", _fixed(10, 31)),
        ("Christmas", _fixed(12, 25)),
    ])

    if market_key in ("NA", "EU", "SEA"):
        candidates.append(("Back to School", _fixed(8, 15)))
        candidates.append(("Singles' Day (11.11)", _fixed(11, 11)))
        candidates.append(("Double 12 (12.12)", _fixed(12, 12)))

    if market_key in ("NA", "EU"):
        bf = _get_black_friday(year)
        candidates.append(("Black Friday", bf))
        candidates.append(("Cyber Monday", bf + dt.timedelta(days=3)))

    if market_key == "NA":
        candidates.append(("Mother's Day (US)", _nth_weekday_of_month(year, 5, 6, 2)))
        candidates.append(("Father's Day (US)", _nth_weekday_of_month(year, 6, 6, 3)))

    if market_key == "EU":
        candidates.append(("Boxing Day (UK)", _fixed(12, 26)))

    future = []
    for name, d in candidates:
        if d >= publish_date:
            future.append((name, d))

    if not future:
        next_year_date = dt.date(year + 1, publish_date.month, min(publish_date.day, 28))
        return get_upcoming_nodes(market_key, next_year_date, limit=limit)

    future.sort(key=lambda x: x[1])
    nodes = []
    for name, d in future[:limit]:
        nodes.append(f"{name} ({d.strftime('%b %d')})")
    return nodes

@st.cache_data(ttl=3600, show_spinner=False)
def fetch_trending_topics(platform, target_market, limit=30):
    geo = "US"
    if target_market and "欧洲" in target_market:
        geo = "GB"
    elif target_market and "东南亚" in target_market:
        geo = "SG"
    elif target_market and "北美" in target_market:
        geo = "US"

    url = f"https://trends.google.com/trends/trendingsearches/daily/rss?geo={geo}"
    headers = {"User-Agent": "Mozilla/5.0"}
    try:
        resp = requests.get(url, headers=headers, timeout=10)
        resp.raise_for_status()
        # Disable external entity resolution to prevent XXE attacks.
        _xml_parser = ET.XMLParser()
        _xml_parser.entity = {}
        root = ET.fromstring(resp.content, parser=_xml_parser)
        items = root.findall(".//item")
        topics = []
        for it in items:
            title = (it.findtext("title") or "").strip()
            traffic = (it.findtext("{http://trends.google.com/trends/trendingsearches}approx_traffic") or "").strip()
            if not title:
                continue
            if traffic:
                topics.append(f"{title} ({traffic})")
            else:
                topics.append(title)
        topics = topics[:limit]
        if topics:
            return topics
    except Exception:
        pass

    if platform and "TikTok" in platform:
        return [
            "POV / Day in my life",
            "Before vs After",
            "3-ingredient / Lazy Meals",
            "Meal prep / 10-min recipe",
            "ASMR cooking",
        ]
    return [
        "New product launch",
        "Quick tutorial",
        "Before vs After",
        "Time-saving hack",
        "Family gathering",
    ]

def _strip_code_fences(text):
    if not text:
        return ""
    t = text.strip()
    if t.startswith("```"):
        lines = t.splitlines()
        if len(lines) >= 2 and lines[0].startswith("```"):
            t = "\n".join(lines[1:])
    if t.endswith("```"):
        t = t[:-3]
    return t.strip()

def _safe_slug(text):
    if not text:
        return "unknown"
    s = re.sub(r"\s+", "_", str(text).strip())
    s = re.sub(r"[^A-Za-z0-9_\u4e00-\u9fff-]+", "_", s)
    return s[:60] if s else "unknown"

def _safe_ascii_slug(text):
    if not text:
        return "unknown"
    mapping = {
        "烤箱": "oven",
        "微波炉": "microwave",
        "空气炸锅": "air_fryer",
    }
    raw = mapping.get(str(text).strip(), str(text).strip())
    slug = re.sub(r"\s+", "_", raw)
    slug = re.sub(r"[^A-Za-z0-9_.-]+", "_", slug).strip("_")
    return slug[:60] if slug else "unknown"

def _category_key(product_category):
    if not product_category:
        return ""
    s = str(product_category)
    for k in COMPETITOR_BRAND_POOL.keys():
        if k and k in s:
            return k
    for k in COMPETITOR_VIDEO_REFERENCES.keys():
        if k and k in s:
            return k
    s_lower = s.lower()
    for kw, mapped in CATEGORY_ALIASES:
        if kw in s_lower:
            return mapped
    return s

def _competitor_config_path(category_key):
    return f"competitor_config_{_safe_slug(category_key)}.json"

def load_competitor_config(category_key):
    data = _safe_read_json(_competitor_config_path(category_key), {})
    if isinstance(data, dict) and data:
        data["brands"] = _remove_own_brand(data.get("brands", []))
        return data
    return {"brands": _remove_own_brand(COMPETITOR_BRAND_POOL.get(category_key, [])), "selected_urls": [], "manual_urls": []}

def save_competitor_config(category_key, config):
    path = _competitor_config_path(category_key)
    payload = {
        "brands": _remove_own_brand(config.get("brands", []) if isinstance(config.get("brands", []), list) else []),
        "selected_urls": config.get("selected_urls", []) if isinstance(config.get("selected_urls", []), list) else [],
        "manual_urls": config.get("manual_urls", []) if isinstance(config.get("manual_urls", []), list) else [],
        "updated_at": dt.datetime.utcnow().isoformat(timespec="seconds") + "Z",
    }
    return _safe_write_json(path, payload)

def _get_runtime_competitor_config(category_key):
    try:
        cfg_map = st.session_state.get("__competitor_runtime_config", {})
        if isinstance(cfg_map, dict):
            v = cfg_map.get(category_key)
            if isinstance(v, dict):
                return v
    except Exception:
        pass
    return None

def _set_runtime_competitor_config(category_key, config):
    try:
        if "__competitor_runtime_config" not in st.session_state or not isinstance(st.session_state.get("__competitor_runtime_config"), dict):
            st.session_state["__competitor_runtime_config"] = {}
        st.session_state["__competitor_runtime_config"][category_key] = config
    except Exception:
        pass

def _remove_own_brand(brands):
    cleaned = []
    for b in (brands or []):
        if not b:
            continue
        b_norm = str(b).strip()
        if not b_norm:
            continue
        if b_norm.lower() in OWN_BRAND_ALIASES or b_norm in OWN_BRAND_ALIASES:
            continue
        if b_norm not in cleaned:
            cleaned.append(b_norm)
    return cleaned

def _category_terms(product_category):
    key = _category_key(product_category)
    terms = [str(product_category)] if product_category else []
    for t in CATEGORY_QUERY_TERMS.get(key, []):
        if t not in terms:
            terms.append(t)
    return terms

@st.cache_data(ttl=3600, show_spinner=False)
def fetch_competitor_videos_web(product_category, target_market, brands, limit=8):
    terms = _category_terms(product_category)
    brands = [b for b in (brands or []) if b]
    if target_market and "欧洲" in target_market:
        region = "UK"
    elif target_market and "东南亚" in target_market:
        region = "SEA"
    elif target_market and "北美" in target_market:
        region = "US"
    else:
        region = ""

    queries = []
    for term in terms[:3]:
        if region:
            queries.append(f'site:youtube.com {term} product video {region}')
        queries.append(f'site:youtube.com {term} product demo')
    for brand in brands[:6]:
        for term in terms[:2]:
            if region:
                queries.append(f'site:youtube.com {brand} {term} official {region}')
            queries.append(f'site:youtube.com {brand} {term} official')
            queries.append(f'site:youtube.com {brand} {term} demo')

    url = "https://duckduckgo.com/html/"
    headers = {"User-Agent": "Mozilla/5.0"}
    urls = []
    for q in queries:
        try:
            resp = requests.get(url, params={"q": q}, headers=headers, timeout=10)
            resp.raise_for_status()
            html = resp.text
            for m in re.finditer(r'href="https://duckduckgo\.com/l/\?uddg=([^"&]+)', html):
                u = urllib.parse.unquote(m.group(1))
                if "youtube.com/" not in u and "youtu.be/" not in u:
                    continue
                if "/watch" not in u and "youtu.be/" not in u and "/channel/" not in u and "/@" not in u and "/c/" not in u and "/playlist" not in u:
                    continue
                if u not in urls:
                    urls.append(u)
                if len(urls) >= limit:
                    break
            if len(urls) >= limit:
                break
        except Exception:
            continue

    items = [{"brand": "", "title": "全网检索结果", "url": u, "focus_points": []} for u in urls]
    return items

def get_competitor_items(product_category, platform, target_market):
    key = _category_key(product_category)
    config = load_competitor_config(key)
    runtime_cfg = _get_runtime_competitor_config(key)
    if isinstance(runtime_cfg, dict):
        merged = {
            "brands": runtime_cfg.get("brands", config.get("brands", [])),
            "selected_urls": runtime_cfg.get("selected_urls", config.get("selected_urls", [])),
            "manual_urls": runtime_cfg.get("manual_urls", config.get("manual_urls", [])),
        }
        config = merged
    brands = _remove_own_brand(config.get("brands", []) if isinstance(config.get("brands", []), list) else [])
    if not brands:
        brands = _remove_own_brand(COMPETITOR_BRAND_POOL.get(key, []))

    items = []
    if key in COMPETITOR_VIDEO_REFERENCES:
        items.extend(COMPETITOR_VIDEO_REFERENCES.get(key, []))

    for u in (config.get("selected_urls", []) or []):
        items.append({"brand": "", "title": "品类私有配置", "url": u, "focus_points": []})
    for u in (config.get("manual_urls", []) or []):
        items.append({"brand": "", "title": "手动新增", "url": u, "focus_points": []})

    web_items = fetch_competitor_videos_web(key, target_market, tuple(brands), limit=8)
    seen = set()
    merged = []
    for it in items + web_items:
        u = (it or {}).get("url", "")
        if not u or u in seen:
            continue
        seen.add(u)
        merged.append(it)
    return merged

def _escape_md_cell(v):
    if v is None:
        return ""
    s = str(v)
    s = s.replace("|", "｜")
    s = s.replace("\r\n", "\n").replace("\r", "\n")
    s = s.replace("\n", "<br>")
    return s.strip()

def _df_to_md_table(df):
    if df is None or df.empty:
        return ""
    cols = [str(c).strip() for c in df.columns.tolist()]
    header = "| " + " | ".join(cols) + " |"
    align = "| " + " | ".join([":---"] * len(cols)) + " |"
    rows = []
    for _, row in df.iterrows():
        cells = [_escape_md_cell(row.get(c, "")) for c in df.columns]
        rows.append("| " + " | ".join(cells) + " |")
    return "\n".join([header, align] + rows)

def _sanitize_competitor_fields(content, allowed_urls):
    table_lines, remainder = _extract_first_md_table(content)
    df = _parse_md_table_to_df(table_lines)
    if df.empty:
        return content
    if "竞品链接" not in df.columns:
        return content

    allow = set([u for u in (allowed_urls or []) if u])
    url_re = re.compile(r"https?://\S+")

    def _filter_urls(text):
        urls = url_re.findall(text or "")
        kept = [u for u in urls if u in allow]
        return " / ".join(kept)

    for idx in df.index:
        raw = "" if df.at[idx, "竞品链接"] is None else str(df.at[idx, "竞品链接"]).strip()
        if not allow:
            df.at[idx, "竞品链接"] = ""
        else:
            df.at[idx, "竞品链接"] = _filter_urls(raw)
        if "竞品盖帽" in df.columns:
            link_val = "" if df.at[idx, "竞品链接"] is None else str(df.at[idx, "竞品链接"]).strip()
            if not link_val:
                df.at[idx, "竞品盖帽"] = ""

    md_table = _df_to_md_table(df)
    if not md_table:
        return content
    if remainder:
        return md_table + "\n\n" + remainder
    return md_table

def _sanitize_english_cell(text):
    if text is None:
        return ""
    s = str(text).strip()
    s = re.sub(r"^\s*(?:【[^】]{0,30}】|\[[^\]]{0,30}\]|\([^\)]{0,30}\)|（[^）]{0,30}）)\s*", "", s)
    s = re.sub(r"^\s*(?:旁白|字幕|字幕-显示卖点名及描述)\s*[:：]\s*", "", s)
    s = re.sub(r"^\s*(?:voiceover|vo|subtitle|subtitles)\s*[:：]\s*", "", s, flags=re.IGNORECASE)
    s = re.sub(r"^\s*-\s*", "", s)
    s = re.sub(r"[\u4e00-\u9fff]+", " ", s)
    s = re.sub(r"\s+", " ", s)
    s = re.sub(r"\s+([,.!?;:])", r"\1", s)
    return s.strip()

def _is_pure_english_text(text):
    if text is None:
        return False
    s = str(text).strip()
    if not s:
        return True
    if re.search(r"[\u4e00-\u9fff]", s):
        return False
    return re.search(r"[A-Za-z]", s) is not None

def _sanitize_english_columns(content):
    table_lines, remainder = _extract_first_md_table(content)
    df = _parse_md_table_to_df(table_lines)
    if df.empty:
        return content
    changed = False
    for col in ["旁白（英文）", "字幕-显示卖点名及描述（英文）"]:
        if col not in df.columns:
            continue
        for idx in df.index:
            raw = df.at[idx, col]
            fixed = _sanitize_english_cell(raw)
            if (raw is None and fixed) or (raw is not None and str(raw).strip() != fixed):
                df.at[idx, col] = fixed
                changed = True
            if not _is_pure_english_text(df.at[idx, col]):
                if col == "旁白（英文）":
                    df.at[idx, col] = "Enjoy the product with ease."
                else:
                    df.at[idx, col] = "Core feature highlighted clearly."
                changed = True
    if not changed:
        return content
    md_table = _df_to_md_table(df)
    if not md_table:
        return content
    if remainder:
        return md_table + "\n\n" + remainder
    return md_table

def _normalize_variant_content(content, allowed_urls=None):
    normalized = content
    normalized = _sanitize_competitor_fields(normalized, allowed_urls or [])
    normalized = _sanitize_english_columns(normalized)
    ok_lang, _ = _validate_language_for_table(normalized)
    if not ok_lang:
        normalized = _force_non_english_columns_to_cn(normalized)
        normalized = _sanitize_english_columns(normalized)
    return normalized

def _ensure_duration_cn(text):
    if text is None:
        return ""
    s = str(text).strip()
    m = re.match(r"^(\d{1,3})\s*(?:s|sec|secs|second|seconds)$", s, flags=re.IGNORECASE)
    if m:
        return f"{m.group(1)}秒"
    m2 = re.match(r"^(\d{1,3})$", s)
    if m2:
        return f"{m2.group(1)}秒"
    return s

def _fallback_cn_cell(col_name):
    mapping = {
        "结构分段": "结构段落",
        "功能点": "功能演示",
        "表现手法": "产品特写 + 场景化对比",
        "特色效果": "关键卖点文字点亮 + 氛围光",
        "拍摄角度": "侧面中景 + 手部特写",
        "运镜方式": "慢推 + 平移",
        "竞品盖帽": "对标竞品主打点，本品用更直观镜头更快讲清卖点",
        "音效": "舒缓背景音乐 + 关键提示音",
    }
    return mapping.get(col_name, "（待补充）")

def _force_non_english_columns_to_cn(content):
    table_lines, remainder = _extract_first_md_table(content)
    df = _parse_md_table_to_df(table_lines)
    if df.empty:
        return content
    english_cols = {"旁白（英文）", "字幕-显示卖点名及描述（英文）"}
    allow_url_only_cols = {"竞品链接"}
    changed = False
    for col in df.columns:
        col_name = str(col).strip()
        if col_name in english_cols:
            continue
        for idx in df.index:
            raw = df.at[idx, col]
            cell = "" if raw is None else str(raw).strip()
            if not cell:
                continue
            if col_name == "时长":
                fixed = _ensure_duration_cn(cell)
                if fixed != cell:
                    df.at[idx, col] = fixed
                    changed = True
                continue
            if col_name in allow_url_only_cols:
                continue
            if not _is_mostly_chinese(cell):
                df.at[idx, col] = _fallback_cn_cell(col_name)
                changed = True
    if not changed:
        return content
    md_table = _df_to_md_table(df)
    if not md_table:
        return content
    if remainder:
        return md_table + "\n\n" + remainder
    return md_table

def _has_cjk(text):
    if not text:
        return False
    return re.search(r"[\u4e00-\u9fff]", str(text)) is not None

def _has_url(text):
    if not text:
        return False
    return re.search(r"https?://", str(text)) is not None

def _lang_mix_counts(text):
    if not text:
        return 0, 0
    s = str(text)
    s = re.sub(r"https?://\S+", "", s)
    cjk = len(re.findall(r"[\u4e00-\u9fff]", s))
    latin = 0
    for tok in re.findall(r"[A-Za-z]+", s):
        if tok.isupper() and len(tok) <= 4:
            continue
        latin += len(tok)
    return cjk, latin

def _is_mostly_chinese(text):
    cjk, latin = _lang_mix_counts(text)
    if (cjk == 0) and (latin == 0):
        return True
    if cjk < 2:
        return False
    return cjk >= latin

def _validate_language_for_table(content):
    table_lines, _ = _extract_first_md_table(content)
    df = _parse_md_table_to_df(table_lines)
    if df.empty:
        return False, "missing_table"

    english_cols = {"旁白（英文）", "字幕-显示卖点名及描述（英文）"}
    allow_url_only_cols = {"竞品链接"}

    violations = 0
    checks = 0
    for col in df.columns:
        col_name = str(col).strip()
        if col_name in english_cols:
            for v in df[col].tolist():
                cell = "" if v is None else str(v).strip()
                if not cell:
                    continue
                checks += 1
                if _is_pure_english_text(cell):
                    continue
                violations += 1
            continue
        for v in df[col].tolist():
            cell = "" if v is None else str(v).strip()
            if not cell:
                continue
            checks += 1
            if col_name in allow_url_only_cols:
                if _has_url(cell) or _has_cjk(cell):
                    continue
                violations += 1
            else:
                if _is_mostly_chinese(cell):
                    continue
                violations += 1

    if checks == 0:
        return True, "no_checks"
    if violations == 0:
        return True, "ok"
    ratio = violations / max(1, checks)
    return ratio < 0.05, f"violations={violations}/{checks}"

def _split_variants(text, expected_count=None):
    t = _strip_code_fences(text)
    if not t:
        return []
    parts = re.split(r"(?:^|\n)\s*【\s*方案\s*(\d+)\s*】\s*(?:\n|$)", t)
    if len(parts) <= 1:
        return [{"name": "方案1", "content": t}]

    variants = []
    base = parts[0].strip()
    if base:
        variants.append({"name": "方案1", "content": base})

    for i in range(1, len(parts), 2):
        num = parts[i]
        content = parts[i + 1] if i + 1 < len(parts) else ""
        name = f"方案{num}"
        variants.append({"name": name, "content": content.strip()})

    seen = set()
    deduped = []
    for v in variants:
        if v["name"] in seen:
            continue
        seen.add(v["name"])
        deduped.append(v)

    if expected_count and len(deduped) > expected_count:
        return deduped[:expected_count]
    return deduped

def _extract_first_md_table(text):
    if not text:
        return [], ""
    lines = text.splitlines()
    table = []
    started = False
    end_idx = None
    for idx, line in enumerate(lines):
        s = line.strip()
        if not started:
            if s.startswith("|") and s.count("|") >= 2:
                started = True
                table.append(line)
            continue
        if started:
            if s.startswith("|") and s.count("|") >= 2:
                table.append(line)
            else:
                end_idx = idx
                break
    remainder = "\n".join(lines[end_idx:]).strip() if end_idx is not None else ""
    return table, remainder

def _parse_md_table_to_df(table_lines):
    if not table_lines or len(table_lines) < 2:
        return pd.DataFrame()
    rows = []
    for line in table_lines:
        s = line.strip().strip("|")
        cells = [c.strip() for c in s.split("|")]
        rows.append(cells)
    header = rows[0]
    body = rows[2:] if len(rows) >= 3 else []
    normalized = []
    for r in body:
        if len(r) < len(header):
            r = r + [""] * (len(header) - len(r))
        if len(r) > len(header):
            r = r[:len(header)]
        normalized.append(r)
    return pd.DataFrame(normalized, columns=header)

def _build_excel_bytes(variants, config_dict, product_category, competitor_items=None):
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as writer:
        config_df = pd.DataFrame([config_dict])
        config_df.to_excel(writer, sheet_name="配置", index=False)

        prompts_rows = []
        for idx, v in enumerate(variants, start=1):
            table_lines, remainder = _extract_first_md_table(v.get("content", ""))
            df = _parse_md_table_to_df(table_lines)
            sheet_name = f"方案{idx}"
            if df.empty:
                pd.DataFrame([{"error": "未解析到表格，请检查输出格式"}]).to_excel(writer, sheet_name=sheet_name, index=False)
            else:
                df.to_excel(writer, sheet_name=sheet_name, index=False)
            prompts_rows.append({
                "方案": sheet_name,
                "方案标签": v.get("label", ""),
                "表格后附加内容": remainder,
                "竞品参考链接": build_reference_links_md(product_category, competitor_items=competitor_items).strip(),
            })
        pd.DataFrame(prompts_rows).to_excel(writer, sheet_name="附加信息", index=False)
        if competitor_items:
            pd.DataFrame([
                {
                    "品牌": (item or {}).get("brand", ""),
                    "标题": (item or {}).get("title", ""),
                    "链接": (item or {}).get("url", ""),
                    "主打点": " / ".join((item or {}).get("focus_points", []) or []),
                }
                for item in competitor_items
            ]).to_excel(writer, sheet_name="竞品候选", index=False)
    return buf.getvalue()

def infer_variant_label(content):
    text = str(content or "")
    if re.search(r"痛点|烦恼|麻烦|困扰|对比|前后", text, flags=re.IGNORECASE):
        return "偏痛点转化"
    if re.search(r"场景|氛围|晨间|夜晚|家庭|厨房|生活方式", text, flags=re.IGNORECASE):
        return "偏氛围种草"
    return "偏功能展示"

def build_reference_links_md(product_category, competitor_items=None):
    refs = competitor_items if isinstance(competitor_items, list) else []
    if not refs:
        refs = get_competitor_items(product_category, "", "")
    lines = [
        "",
        "---",
        "",
        "竞品优秀宣传视频参考链接（仅供内部学习，不代表推荐/背书）：",
    ]
    if not refs:
        lines.append("- （无可用竞品链接，留空）")
        return "\n".join(lines)
    for item in refs:
        title = item.get("title", "")
        url = item.get("url", "")
        brand = item.get("brand", "")
        focus = item.get("focus_points", []) or []
        focus_text = " / ".join([x for x in focus if x])
        suffix = f"（主打：{focus_text}）" if focus_text else ""
        prefix = f"{brand} - " if brand else ""
        lines.append(f"- {prefix}{title}：{url}{suffix}")
    return "\n".join(lines)

def build_reference_links_inline(product_category):
    refs = []
    for k, items in COMPETITOR_VIDEO_REFERENCES.items():
        if k in (product_category or ""):
            refs = items
            break
    parts = []
    for item in refs:
        title = item.get("title", "")
        url = item.get("url", "")
        brand = item.get("brand", "")
        focus = item.get("focus_points", []) or []
        focus_text = " / ".join([x for x in focus if x])
        suffix = f" (focus: {focus_text})" if focus_text else ""
        prefix = f"{brand} - " if brand else ""
        parts.append(f"{prefix}{title}: {url}{suffix}")
    return " <br> ".join(parts)

def get_product_data():
    """从存储适配层读取产品卖点数据。"""
    return PRODUCT_FEATURE_STORE.load()

def _extract_secret_password(secret_string):
    if not secret_string:
        return ""
    stripped = secret_string.strip()
    if stripped.startswith("{"):
        try:
            import json
            payload = json.loads(stripped)
            if isinstance(payload, dict):
                return str(payload.get("password") or payload.get("APP_ACCESS_PASSWORD") or "")
        except Exception:
            pass
    return secret_string

def get_access_password():
    if not APP_ACCESS_PASSWORD_SECRET_ID:
        return APP_ACCESS_PASSWORD, ""

    now = time.time()
    if _ACCESS_PASSWORD_CACHE["value"] is not None and now < _ACCESS_PASSWORD_CACHE["expires_at"]:
        return _ACCESS_PASSWORD_CACHE["value"], _ACCESS_PASSWORD_CACHE.get("error", "")

    try:
        import boto3
        from botocore.config import Config

        client = boto3.client(
            "secretsmanager",
            region_name=BEDROCK_AWS_REGION,
            config=Config(
                connect_timeout=APP_ACCESS_PASSWORD_FETCH_TIMEOUT,
                read_timeout=APP_ACCESS_PASSWORD_FETCH_TIMEOUT,
                retries={"max_attempts": 2},
            ),
        )
        response = client.get_secret_value(SecretId=APP_ACCESS_PASSWORD_SECRET_ID)
        password = _extract_secret_password(response.get("SecretString", ""))
        _ACCESS_PASSWORD_CACHE["value"] = password
        _ACCESS_PASSWORD_CACHE["expires_at"] = now + APP_ACCESS_PASSWORD_CACHE_TTL
        _ACCESS_PASSWORD_CACHE["error"] = ""
        return password, ""
    except Exception as exc:
        fallback_password = _ACCESS_PASSWORD_CACHE["value"] or APP_ACCESS_PASSWORD
        _ACCESS_PASSWORD_CACHE["value"] = fallback_password
        _ACCESS_PASSWORD_CACHE["expires_at"] = now + min(APP_ACCESS_PASSWORD_CACHE_TTL, 60)
        _ACCESS_PASSWORD_CACHE["error"] = str(exc)
        return fallback_password, str(exc)

def _password_fingerprint(password):
    return hashlib.sha256((password or "").encode("utf-8")).hexdigest()

def require_access():
    if not APP_ACCESS_CONTROL_ENABLED:
        return True

    current_password, password_error = get_access_password()
    if not current_password:
        if password_error:
            st.error(f"暂时无法读取访问密码配置：{password_error}")
        return True
    current_fingerprint = _password_fingerprint(current_password)
    if (
        st.session_state.get("__access_granted")
        and st.session_state.get("__access_password_fingerprint") == current_fingerprint
    ):
        return True

    st.title("海外爆款内容引擎")
    st.info("请输入访问密码。")
    if password_error:
        st.caption("密码配置读取暂时较慢，已使用最近一次成功读取的配置进行校验。")
    with st.form("__access_login_form"):
        password = st.text_input("访问密码", type="password")
        submitted = st.form_submit_button("进入平台", type="primary")
    if submitted:
        with st.spinner("正在验证访问权限..."):
            current_password, password_error = get_access_password()
            if password_error and not current_password:
                st.error(f"暂时无法读取访问密码配置：{password_error}")
                st.stop()
            current_fingerprint = _password_fingerprint(current_password)
            if hmac.compare_digest((password or "").encode("utf-8"), current_password.encode("utf-8")):
                st.session_state["__access_granted"] = True
                st.session_state["__access_password_fingerprint"] = current_fingerprint
                st.rerun()
            else:
                st.error("访问密码不正确。")
    st.stop()

st.set_page_config(page_title="海外爆款内容引擎", page_icon="🎬", layout="wide", initial_sidebar_state="expanded")

def apply_apple_theme():
    st.markdown(
        """
        <style>
        :root {
            --app-bg: #f5f5f7;
            --app-surface: rgba(255, 255, 255, 0.92);
            --app-surface-solid: #ffffff;
            --app-border: rgba(29, 29, 31, 0.09);
            --app-text: #1d1d1f;
            --app-muted: #6e6e73;
            --app-faint: #86868b;
            --app-blue: #0071e3;
            --app-blue-dark: #005bb5;
            --app-orange: #f56300;
            --app-green: #34c759;
            --app-red: #ff3b30;
            --app-radius: 28px;
            --app-shadow: 0 18px 42px rgba(0, 0, 0, 0.08);
        }

        html, body, [data-testid="stAppViewContainer"], .stApp {
            background: var(--app-bg) !important;
            color: var(--app-text);
            font-family: -apple-system, BlinkMacSystemFont, "SF Pro Display", "SF Pro Text", "Segoe UI", sans-serif;
        }

        .block-container {
            max-width: 1440px;
            padding-top: 1.05rem;
            padding-bottom: 5rem;
            padding-left: 3.4rem;
            padding-right: 3.4rem;
        }

        [data-testid="stHeader"] {
            height: 0 !important;
            min-height: 0 !important;
            background: transparent !important;
            box-shadow: none !important;
        }

        [data-testid="stToolbar"] {
            display: none !important;
            visibility: hidden !important;
            pointer-events: none !important;
        }

        [data-testid="collapsedControl"],
        [data-testid="stSidebarCollapsedControl"],
        [data-testid="stSidebarNav"] button,
        button[title="Open sidebar"],
        button[title="Close sidebar"] {
            display: flex !important;
            visibility: visible !important;
            opacity: 1 !important;
            pointer-events: auto !important;
        }

        [data-testid="collapsedControl"],
        [data-testid="stSidebarCollapsedControl"],
        button[title="Open sidebar"] {
            position: fixed !important;
            top: 0.75rem !important;
            left: 0.75rem !important;
            z-index: 999999 !important;
            width: 2.5rem !important;
            height: 2.5rem !important;
            border-radius: 999px !important;
            background: rgba(255, 255, 255, 0.9) !important;
            border: 1px solid rgba(0, 0, 0, 0.12) !important;
            box-shadow: 0 10px 24px rgba(0, 0, 0, 0.12) !important;
            align-items: center !important;
            justify-content: center !important;
        }

        [data-testid="stDecoration"],
        [data-testid="stStatusWidget"] {
            display: none !important;
            visibility: hidden !important;
            pointer-events: none !important;
        }

        [data-testid="stSidebar"] {
            background: rgba(245, 245, 247, 0.9) !important;
            border-right: 1px solid var(--app-border);
            backdrop-filter: blur(22px);
        }

        [data-testid="stSidebar"] [data-testid="stMarkdownContainer"] h2,
        [data-testid="stSidebar"] [data-testid="stMarkdownContainer"] h3 {
            color: var(--app-text);
            letter-spacing: 0;
        }

        h1, h2, h3 {
            color: var(--app-text);
            letter-spacing: 0 !important;
        }

        h1 {
            font-size: clamp(2rem, 3.1vw, 3rem) !important;
            line-height: 1.06 !important;
            font-weight: 760 !important;
        }

        h2, h3 {
            font-weight: 680 !important;
        }

        .apple-hero {
            display: grid;
            grid-template-columns: minmax(0, 1fr) minmax(16rem, 0.42fr);
            gap: 0.85rem;
            align-items: stretch;
            margin: 0 0 0.9rem 0;
        }

        .apple-hero-main,
        .apple-hero-side,
        .apple-store-card {
            background: var(--app-surface-solid);
            border: 1px solid rgba(29, 29, 31, 0.06);
            border-radius: var(--app-radius);
            box-shadow: var(--app-shadow);
        }

        .apple-hero-main {
            min-height: 9.2rem;
            padding: 1.35rem 1.65rem;
            background:
                radial-gradient(circle at 78% 10%, rgba(0, 113, 227, 0.11), transparent 18rem),
                linear-gradient(135deg, #ffffff 0%, #fbfbfd 58%, #eef6ff 100%);
        }

        .apple-hero-side {
            min-height: 9.2rem;
            padding: 1.2rem 1.35rem;
            background:
                radial-gradient(circle at 88% 0%, rgba(255, 149, 0, 0.14), transparent 13rem),
                #ffffff;
        }

        .apple-eyebrow {
            color: var(--app-blue);
            font-size: 0.88rem;
            font-weight: 700;
            margin-bottom: 0.55rem;
        }

        .apple-hero h1 {
            max-width: 860px;
            margin: 0;
        }

        .apple-hero h1 .apple-accent {
            color: var(--app-orange);
        }

        .apple-hero p {
            max-width: 760px;
            margin: 0.65rem 0 0;
            color: var(--app-muted);
            font-size: 0.98rem;
            line-height: 1.5;
        }

        .apple-chip-row {
            display: flex;
            flex-wrap: wrap;
            gap: 0.55rem;
            margin-top: 0.85rem;
        }

        .apple-chip {
            padding: 0.36rem 0.66rem;
            border-radius: 999px;
            color: #3a3a3c;
            background: rgba(255, 255, 255, 0.88);
            border: 1px solid rgba(0, 0, 0, 0.07);
            font-size: 0.8rem;
            font-weight: 650;
        }

        .apple-side-kicker {
            color: var(--app-orange);
            font-size: 0.76rem;
            font-weight: 700;
            margin-bottom: 0.45rem;
        }

        .apple-side-title {
            color: var(--app-text);
            font-size: 1.08rem;
            line-height: 1.16;
            font-weight: 730;
            margin: 0 0 0.42rem;
        }

        .apple-side-copy {
            color: var(--app-muted);
            font-size: 0.84rem;
            line-height: 1.42;
            margin: 0;
        }

        .apple-store-rail {
            display: grid;
            grid-template-columns: repeat(3, minmax(0, 1fr));
            gap: 0.85rem;
            margin: 0 0 1rem;
        }

        .apple-store-card {
            min-height: 6.3rem;
            padding: 1rem 1.15rem;
        }

        .apple-store-card .card-kicker {
            color: var(--app-faint);
            font-size: 0.78rem;
            font-weight: 700;
            letter-spacing: 0.02em;
            text-transform: uppercase;
        }

        .apple-store-card .card-title {
            color: var(--app-text);
            font-size: 1.06rem;
            line-height: 1.18;
            font-weight: 720;
            margin: 0.42rem 0 0.28rem;
        }

        .apple-store-card .card-copy {
            color: var(--app-muted);
            font-size: 0.82rem;
            line-height: 1.45;
        }

        .apple-section-title {
            margin: 1.65rem 0 0.35rem;
            font-size: clamp(1.72rem, 2.5vw, 2.35rem);
            line-height: 1.08;
            font-weight: 760;
            color: var(--app-text);
        }

        .apple-section-caption {
            margin: 0 0 1.15rem;
            color: var(--app-muted);
            font-size: 1.02rem;
        }

        div[data-testid="stVerticalBlock"] > div:has(> div[data-testid="stMetric"]) {
            background: #ffffff;
            border: 1px solid rgba(29, 29, 31, 0.06);
            border-radius: 24px;
            padding: 1.35rem 1.55rem;
            box-shadow: var(--app-shadow);
        }

        [data-testid="stMetricLabel"] {
            color: var(--app-muted) !important;
            font-size: 0.9rem !important;
        }

        [data-testid="stMetricValue"] {
            color: var(--app-text) !important;
            font-size: 2.15rem !important;
            font-weight: 650 !important;
        }

        div[data-testid="stExpander"] {
            background: rgba(255, 255, 255, 0.94);
            border: 1px solid rgba(29, 29, 31, 0.07);
            border-radius: 22px;
            box-shadow: 0 12px 30px rgba(0, 0, 0, 0.055);
            overflow: hidden;
        }

        div[data-testid="stExpander"] details summary {
            font-weight: 650;
            color: var(--app-text);
        }

        .stTextInput input,
        .stNumberInput input,
        .stTextArea textarea,
        div[data-baseweb="select"] > div {
            border-radius: 14px !important;
            border-color: rgba(0, 0, 0, 0.08) !important;
            background: rgba(255, 255, 255, 0.96) !important;
            box-shadow: none !important;
        }

        div[data-baseweb="select"] input,
        div[data-baseweb="select"] input:focus {
            background: transparent !important;
            border: 0 !important;
            box-shadow: none !important;
            outline: none !important;
            min-width: 2px !important;
            padding: 0 !important;
        }

        .stTextInput input:focus,
        .stNumberInput input:focus,
        .stTextArea textarea:focus {
            border-color: rgba(0, 113, 227, 0.58) !important;
            box-shadow: 0 0 0 4px rgba(0, 113, 227, 0.13) !important;
        }

        /* Hide BaseWeb's invisible measurement/ghost input that can appear as a white overlay on multiselect tags. */
        div[data-baseweb="select"] [aria-hidden="true"],
        div[data-baseweb="select"] input[aria-hidden="true"],
        div[data-baseweb="select"] div[aria-hidden="true"] {
            opacity: 0 !important;
            width: 0 !important;
            min-width: 0 !important;
            max-width: 0 !important;
            padding: 0 !important;
            margin: 0 !important;
            border: 0 !important;
            box-shadow: none !important;
            pointer-events: none !important;
        }

        .stButton > button,
        .stDownloadButton > button,
        .stFormSubmitButton > button {
            border-radius: 999px !important;
            min-height: 2.8rem;
            border: 1px solid rgba(29, 29, 31, 0.08) !important;
            font-weight: 650 !important;
            transition: transform 120ms ease, box-shadow 120ms ease, background 120ms ease;
            background: #ffffff !important;
            color: var(--app-text) !important;
        }

        .stButton > button[kind="primary"],
        .stDownloadButton > button,
        .stFormSubmitButton > button[kind="primary"] {
            background: var(--app-blue) !important;
            color: #fff !important;
            box-shadow: 0 10px 22px rgba(0, 113, 227, 0.24);
        }

        .stButton > button:hover,
        .stDownloadButton > button:hover,
        .stFormSubmitButton > button:hover {
            transform: translateY(-1px);
            box-shadow: 0 14px 28px rgba(0, 0, 0, 0.12);
        }

        [data-testid="stAlert"] {
            border-radius: 18px;
            border: 1px solid rgba(0, 0, 0, 0.06);
        }

        [data-testid="stFileUploader"] section {
            border-radius: 22px;
            border: 1px dashed rgba(0, 113, 227, 0.28);
            background: rgba(255, 255, 255, 0.92);
        }

        .stTabs [data-baseweb="tab-list"] {
            gap: 0.35rem;
            background: rgba(255, 255, 255, 0.72);
            border: 1px solid var(--app-border);
            border-radius: 999px;
            padding: 0.3rem;
        }

        .stTabs [data-baseweb="tab"] {
            border-radius: 999px;
            padding: 0.45rem 1rem;
            color: var(--app-muted);
        }

        .stTabs [aria-selected="true"] {
            background: var(--app-surface-solid);
            color: var(--app-text);
            box-shadow: 0 8px 22px rgba(0, 0, 0, 0.08);
        }

        hr {
            border: 0;
            border-top: 1px solid rgba(0, 0, 0, 0.08);
            margin: 1.8rem 0;
        }

        table {
            border-radius: 14px;
            overflow: hidden;
        }

        .stDataFrame,
        [data-testid="stTable"] {
            border-radius: 20px;
            overflow: hidden;
            box-shadow: 0 10px 28px rgba(0, 0, 0, 0.045);
        }

        @media (max-width: 760px) {
            .block-container {
                padding-left: 1rem;
                padding-right: 1rem;
            }

            .apple-hero {
                grid-template-columns: 1fr;
            }

            .apple-hero p {
                font-size: 1rem;
            }

            .apple-store-rail {
                grid-template-columns: 1fr;
            }
        }
        </style>
        """,
        unsafe_allow_html=True,
    )


def render_hero():
    st.markdown(
        """
        <section class="apple-hero">
            <div class="apple-hero-main">
                <div class="apple-eyebrow">Hisense International E-commerce</div>
                <h1>海外爆款内容引擎</h1>
                <p>海外爆款内容引擎把产品型号、核心卖点、场景和营销诉求整理成可拍摄、可导出的短视频脚本方案。</p>
                <div class="apple-chip-row">
                    <span class="apple-chip">产品卖点库驱动</span>
                    <span class="apple-chip">Amazon Bedrock 生成</span>
                    <span class="apple-chip">Excel 导出</span>
                </div>
            </div>
            <div class="apple-hero-side">
                <div class="apple-side-kicker">FOR INTERNAL TEAMS</div>
                <div class="apple-side-title">面向国际营销电商团队的内容工作台。</div>
                <p class="apple-side-copy">统一产品信息、脚本生成、任务队列和视频参考片段，减少反复整理素材与跨团队沟通成本。</p>
            </div>
        </section>
        <section class="apple-store-rail">
            <div class="apple-store-card">
                <div class="card-kicker">Knowledge</div>
                <div class="card-title">卖点库先行。</div>
                <div class="card-copy">上传后的产品文案库会作为脚本生成的事实来源，减少手工复制和信息遗漏。</div>
            </div>
            <div class="apple-store-card">
                <div class="card-kicker">Creation</div>
                <div class="card-title">多方案生成。</div>
                <div class="card-copy">围绕痛点、场景、功能展示等方向快速产出多版脚本，方便业务挑选和复用。</div>
            </div>
            <div class="apple-store-card">
                <div class="card-kicker">Delivery</div>
                <div class="card-title">导出与追踪。</div>
                <div class="card-copy">脚本文档可导出，任务队列可追踪，后续可继续接入视频生成和数据库能力。</div>
            </div>
        </section>
        """,
        unsafe_allow_html=True,
    )


def render_section_title(title, caption=""):
    st.markdown(f'<div class="apple-section-title">{_html_escape(str(title))}</div>', unsafe_allow_html=True)
    if caption:
        st.markdown(f'<div class="apple-section-caption">{_html_escape(str(caption))}</div>', unsafe_allow_html=True)


apply_apple_theme()

require_access()

render_hero()

with st.expander("基础配置", expanded=False):
    st.caption("先设置发布渠道、目标市场和生成方案数；无特殊要求时，高级风格设置保持默认即可。")
    cfg_col1, cfg_col2, cfg_col3 = st.columns(3)
    with cfg_col1:
        platform = st.selectbox(
            "发布渠道",
            ["TikTok / Reels / Shorts", "Amazon 主图视频", "独立站详情页"],
            help="决定脚本节奏和画幅表达，默认适合海外短视频投放。",
        )
    with cfg_col2:
        target_market = st.selectbox(
            "目标市场",
            ["北美 (US/CA)", "欧洲 (UK/DE/FR)", "东南亚", "其他"],
            help="用于匹配语言、节日节点和用户表达习惯。",
        )
    with cfg_col3:
        variant_count = st.selectbox("生成方案数", [2, 3], index=0, help="一次生成几套不同脚本方向。")
    with st.expander("高级风格设置", expanded=False):
        style_col1, style_col2, style_col3, style_col4 = st.columns(4)
        with style_col1:
            production_method = st.selectbox("制作方式", ["实拍", "渲染"])
        with style_col2:
            overall_style = st.selectbox("视觉风格", ["科技", "时尚", "温馨家居", "高端奢华"])
        with style_col3:
            music_style = st.selectbox("音乐氛围", ["舒缓", "日系"])
        with style_col4:
            tone_color = st.selectbox("画面色调", ["暖色", "冷色", "日系"])
    
# 检查是否已有缓存数据
df_products = get_product_data()
cache_meta = load_cache_meta()

# 数据上传模块
if df_products.empty:
    render_section_title("导入产品卖点库", "上传新版或旧版 Excel，系统会自动校验字段并保存为当前知识库。")
    st.info("首次使用请上传《产品卖点库》Excel 文件。")
    st.caption("提示：当前会通过存储适配层保存数据；配置 S3/RDS 后可持久化到 AWS。")
    uploaded_file = st.file_uploader("拖拽或点击上传 Excel 文件", type=["xlsx", "xls"])
    if uploaded_file is not None:
        with st.spinner("正在解析文件..."):
            try:
                file_bytes = uploaded_file.getvalue()
                df = pd.read_excel(io.BytesIO(file_bytes))
                df_filtered = filter_product_features(df)
                PRODUCT_FEATURE_STORE.save(uploaded_file.name, file_bytes, df_filtered)
                st.success("✅ 文件解析并保存成功！正在重新加载界面...")
                st.rerun()
            except Exception as e:
                st.error(f"解析文件失败: {e}")
    st.stop()
else:
    render_section_title("卖点库状态", "当前加载的数据会作为脚本生成的产品知识来源。")
    status_cols = st.columns(4)
    status_cols[0].metric("当前品类数", int(df_products["Category"].nunique()) if "Category" in df_products.columns else 0)
    status_cols[1].metric("当前型号数", int(df_products["model"].nunique()) if "model" in df_products.columns else 0)
    status_cols[2].metric("当前卖点行数", int(len(df_products)))
    status_cols[3].metric("缓存状态", "已加载")
    if cache_meta:
        st.caption(
            f"当前已加载文件：{cache_meta.get('file_name', '未知文件')}｜"
            f"最近更新时间：{cache_meta.get('updated_at', '未知')}｜"
            f"存储：{cache_meta.get('storage_backend', os.getenv('STORAGE_BACKEND', 'local'))}｜"
            f"数据库：{'已启用' if cache_meta.get('database_enabled') else '未启用'}"
        )
    with st.expander("更新产品卖点库", expanded=False):
        uploaded_file = st.file_uploader("如果您有最新的 Excel，可以在此上传覆盖", type=["xlsx", "xls"])
        if uploaded_file is not None:
            with st.spinner("正在更新文件..."):
                try:
                    file_bytes = uploaded_file.getvalue()
                    df = pd.read_excel(io.BytesIO(file_bytes))
                    df_filtered = filter_product_features(df)
                    PRODUCT_FEATURE_STORE.save(uploaded_file.name, file_bytes, df_filtered)
                    st.success("✅ 数据库已更新！")
                    st.rerun()
                except Exception as e:
                    st.error(f"解析文件失败: {e}")

    history_records = load_history_records()
    with st.expander("最近生成记录", expanded=False):
        if not history_records:
            st.caption("暂无历史记录。生成脚本后会自动保存最近 12 次记录，便于再次查看。")
        else:
            history_options = [
                f"{r.get('created_at', '')}｜{r.get('category', '')}｜{r.get('model', '')}｜{r.get('platform', '')}"
                for r in history_records
            ]
            history_idx = st.selectbox("选择历史记录", range(len(history_options)), format_func=lambda i: history_options[i])
            selected_history = history_records[history_idx]
            st.caption(f"历史标签：{' / '.join(selected_history.get('labels', [])) if selected_history.get('labels') else '无'}")
            if st.button("载入该次结果", use_container_width=True):
                st.session_state["generated_variants"] = selected_history.get("variants", [])
                history_category = selected_history.get("category", "")
                history_competitors = get_competitor_items(history_category, "", selected_history.get("market", ""))
                st.session_state["last_competitor_items"] = history_competitors
                st.session_state["generated_excel_bytes"] = _build_excel_bytes(
                    selected_history.get("variants", []),
                    selected_history.get("config", {}),
                    history_category,
                    competitor_items=history_competitors,
                )
                st.session_state["history_loaded_note"] = f"已载入历史记录：{history_options[history_idx]}"
                st.rerun()

if st.session_state.get("history_loaded_note"):
    st.info(st.session_state.get("history_loaded_note"))

col1, col2 = st.columns(2)

with col1:
    render_section_title("产品信息", "选择品类、型号和核心卖点，决定脚本内容的事实基础。")
    
    # 动态获取品类
    all_categories = sorted(df_products['Category'].unique().tolist()) if not df_products.empty else ["空气炸锅", "烤箱", "洗碗机", "洗衣机"]
    selected_category = st.selectbox("产品品类", all_categories)
    
    # 根据品类过滤型号
    filtered_models = df_products[df_products['Category'] == selected_category]['model'].unique().tolist() if not df_products.empty else []
    selected_model = st.selectbox("产品型号/名称 (支持搜索)", filtered_models, help="输入型号可模糊匹配搜索")
    
    model_features = df_products[df_products['model'] == selected_model]

    available_feature_names = []
    if not model_features.empty and 'Feature Name' in model_features.columns:
        raw = model_features['Feature Name'].dropna().astype(str).tolist()
        available_feature_names = list(dict.fromkeys([x.strip() for x in raw if x.strip()]))

    model_key = f"{selected_category}::{selected_model}"
    if st.session_state.get("last_model_key") != model_key:
        st.session_state["last_model_key"] = model_key
        st.session_state["feature_count"] = 3
        for i in range(1, 11):
            key = f"feature_{i}"
            if key in st.session_state:
                del st.session_state[key]

    max_feature_count = min(10, len(available_feature_names)) if available_feature_names else 10
    current_feature_count = int(st.session_state.get("feature_count", 3))
    current_feature_count = max(1, min(current_feature_count, max_feature_count))
    st.session_state["feature_count"] = current_feature_count
    feature_count = st.number_input("核心卖点数量", min_value=1, max_value=max_feature_count, value=current_feature_count, step=1)
    st.session_state["feature_count"] = int(feature_count)

    feature_options = [""] + available_feature_names
    selected_features = []
    for i in range(1, int(feature_count) + 1):
        key = f"feature_{i}"
        if key not in st.session_state:
            st.session_state[key] = available_feature_names[i - 1] if i - 1 < len(available_feature_names) else ""
        selected = st.selectbox(f"卖点 {i}", feature_options, key=key)
        if selected:
            selected_features.append(selected)

    st.caption(f"已选择 {len(selected_features)} 个卖点")

    with st.expander("竞品素材配置", expanded=False):
        category_key = _category_key(selected_category)
        cfg = load_competitor_config(category_key)

        default_brands = _remove_own_brand(COMPETITOR_BRAND_POOL.get(category_key, []))
        cfg_brands = _remove_own_brand(cfg.get("brands", []) or [])
        brand_options = sorted(list(dict.fromkeys((default_brands or []) + cfg_brands)))
        brands = st.multiselect("品类竞品品牌池", brand_options, default=[b for b in cfg_brands if b in brand_options])
        extra_brands = st.text_input("补充品牌（可选，逗号分隔）", "")
        if extra_brands.strip():
            for b in [x.strip() for x in extra_brands.split(",") if x.strip()]:
                if b not in brands and (b.lower() not in OWN_BRAND_ALIASES and b not in OWN_BRAND_ALIASES):
                    brands.append(b)

        _set_runtime_competitor_config(category_key, {"brands": _remove_own_brand(brands), "selected_urls": cfg.get("selected_urls", []), "manual_urls": cfg.get("manual_urls", [])})
        candidate_items = get_competitor_items(selected_category, platform, target_market)
        candidate_urls = []
        labels = {}
        source_counter = {"内置参考": 0, "已勾选私有配置": 0, "手动新增": 0, "全网检索": 0}
        for it in candidate_items:
            u = (it or {}).get("url", "")
            if not u:
                continue
            if u not in candidate_urls:
                candidate_urls.append(u)
            title_text = (it or {}).get("title", "")
            if "品类私有配置" in title_text:
                source_counter["已勾选私有配置"] += 1
            elif "手动新增" in title_text:
                source_counter["手动新增"] += 1
            elif "全网检索结果" in title_text:
                source_counter["全网检索"] += 1
            else:
                source_counter["内置参考"] += 1
            brand = (it or {}).get("brand", "")
            title = (it or {}).get("title", "")
            focus = (it or {}).get("focus_points", []) or []
            focus_text = " / ".join([x for x in focus if x])
            labels[u] = f"{brand + ' - ' if brand else ''}{title}{('（' + focus_text + '）') if focus_text else ''}"

        selected_urls = st.multiselect(
            "候选竞品链接（可搜索多选）",
            candidate_urls,
            default=[u for u in (cfg.get("selected_urls", []) or []) if u in candidate_urls],
            format_func=lambda u: f"{labels.get(u, '')} {u}".strip(),
        )
        manual_urls_text = st.text_area(
            "手动新增竞品链接（可选，一行一个；仅支持 YouTube）",
            "\n".join(cfg.get("manual_urls", []) or []),
            height=120,
        )
        manual_urls = []
        for line in (manual_urls_text or "").splitlines():
            u = line.strip()
            if not u:
                continue
            if ("youtube.com" in u) or ("youtu.be" in u):
                manual_urls.append(u)

        _set_runtime_competitor_config(category_key, {"brands": _remove_own_brand(brands), "selected_urls": selected_urls, "manual_urls": manual_urls})
        st.caption(
            f"当前候选链接共 {len(candidate_urls)} 条｜内置参考 {source_counter['内置参考']}｜私有配置 {source_counter['已勾选私有配置']}｜手动新增 {source_counter['手动新增']}｜全网检索 {source_counter['全网检索']}。未命中时会留空，不会乱填。"
        )

        col_save, col_hint = st.columns([1, 3])
        with col_save:
            if st.button("保存该品类配置", use_container_width=True):
                ok = save_competitor_config(category_key, {"brands": brands, "selected_urls": selected_urls, "manual_urls": manual_urls})
                if ok:
                    st.success("已保存（该品类配置会缓存在云端服务器文件中）")
                    st.rerun()
                else:
                    st.error("保存失败，请稍后重试")
        with col_hint:
            st.caption("说明：该配置按“品类”写入云端服务器本地文件，属于应用内部配置；未匹配到链接时会留空，避免乱填。")

with col2:
    render_section_title("营销诉求", "补充场景、受众、痛点和内容节点，让脚本更贴近投放需求。")
    video_usage = st.selectbox("视频用途", ["站外种草", "站内首推", "内部培训", "其他"])
    video_type = st.multiselect(
        "脚本方向（可多选）",
        ["问题解决/痛点挖掘型", "产品展示/功能介绍型", "开箱体验型", "场景化/生活方式型", "测评/对比型"],
        default=["问题解决/痛点挖掘型", "场景化/生活方式型"],
        help="用于约束脚本结构；不确定时保留默认即可。",
    )
    expected_duration = st.number_input(
        "期望视频时长(秒)",
        min_value=6,
        max_value=90,
        value=30,
        step=1,
        help="可直接输入业务希望的视频时长。建议短视频控制在 15-45 秒。",
    )
    project_type = st.selectbox("项目类型(可选)", ["常规上新", "新品上市", "大促活动", "教程培训", "其他"])
    general_audience_mode = st.checkbox("不指定目标受众（通用卖点）", value=True, key="general_audience_mode")
    _aud_key = _category_key(selected_category)
    if st.session_state.get("last_audience_category") != _aud_key:
        st.session_state["last_audience_category"] = _aud_key
        if not general_audience_mode:
            st.session_state["target_audience"] = DEFAULT_TARGET_AUDIENCE.get(_aud_key, "家庭用户；年轻上班族；追求省时省力的人群")
    if "target_audience" not in st.session_state:
        st.session_state["target_audience"] = DEFAULT_TARGET_AUDIENCE.get(_aud_key, "家庭用户；年轻上班族；追求省时省力的人群")
    if general_audience_mode:
        st.session_state["target_audience"] = ""
    else:
        if st.session_state.get("last_general_audience_mode") is True or not str(st.session_state.get("target_audience", "")).strip():
            st.session_state["target_audience"] = DEFAULT_TARGET_AUDIENCE.get(_aud_key, "家庭用户；年轻上班族；追求省时省力的人群")
    st.session_state["last_general_audience_mode"] = bool(general_audience_mode)

    target_audience = st.text_input("目标受众", key="target_audience", disabled=general_audience_mode)
    _pain_key = _category_key(selected_category)
    if st.session_state.get("last_pain_category") != _pain_key:
        st.session_state["last_pain_category"] = _pain_key
        st.session_state["pain_points"] = DEFAULT_PAIN_POINTS.get(_pain_key, "省时省力；提升体验；减少清洁与维护成本；更适合家庭/小户型使用场景")
    pain_points = st.text_area("用户痛点", key="pain_points")
    custom_requirements = st.text_area(
        "自定义需求",
        placeholder="例如：60% 内容聚焦第一个卖点；补充强调安装便利；弱化价格表达；面向美国亚马逊站外种草。",
        help="用于补充无法通过筛选项表达的具体要求，会直接传入生成提示词。",
        height=120,
    )

    with st.expander("节日与热点推荐", expanded=False):
        publish_date = st.date_input("内容发布日期", value=dt.date.today())
        market_key = _market_key(target_market)
        upcoming_nodes = get_upcoming_nodes(market_key, publish_date, limit=4)
        selected_nodes = st.multiselect("近期开节点（可多选）", upcoming_nodes, default=upcoming_nodes[:2] if upcoming_nodes else [])
        auto_topics = fetch_trending_topics(platform, target_market, limit=30)
        selected_topics = st.multiselect(
            "近期热点/趋势（自动获取，可搜索多选）",
            auto_topics,
            default=auto_topics[:3] if auto_topics else [],
        )
        extra_topic = st.text_input("补充热点（可选）", "")
        st.caption("热点来源说明：优先自动抓取公开趋势数据；若抓取失败则展示系统兜底候选，仅供参考。")

    festival_hotspot_parts = []
    if selected_nodes:
        festival_hotspot_parts.append("节日节点: " + " / ".join(selected_nodes))
    if selected_topics:
        festival_hotspot_parts.append("热点趋势: " + " / ".join(selected_topics))
    if extra_topic and extra_topic.strip():
        festival_hotspot_parts.append("补充热点: " + extra_topic.strip())
    festival_hotspot = "；".join(festival_hotspot_parts)

st.markdown("---")

# 定义系统 Prompt
SYSTEM_PROMPT = """##角色 
你是“海外爆款内容引擎”，为海信海外电商产品策划推广提供视频脚本生成服务。你需要基于海信的产品卖点，撰写不同类型（产品展示视频、产品介绍视频、产品操作视频、产品种草视频等）的视频脚本，以支持导出为word或excel形式的Markdown表格输出。 
 
##限制与优化规范
1. **时长精确控制**：脚本总时长需尽量贴近用户给定的“期望视频时长(秒)”；如用户未特别指定，建议控制在 15-45 秒。表格的“时长”列必须给出**确切的秒数**（如：5秒），并在表格最后一行增加“总时长”统计。
2. **结构模块化与落地**：对于产品展示和操作类视频，采用“步骤拆解式”的结构分段（如：开箱检查、安装放置、功能A演示、对比实验等），逻辑务实清晰。
3. **强调交互与对比镜头**：在“表现手法/拍摄角度/运镜方式”等字段中，必须包含**UI面板/按键的特写、操作反馈（如LED屏幕显示、滴滴声）**，并尽量设计**使用前后的对比实验镜头**（如：传统解冻 vs 微波炉解冻）以直观展示卖点。
4. **品牌 Slogan 收尾**：脚本的最后一段（总结）必须是固定的格式：产品静置全景特写 + 海信品牌 Slogan（"Hisense Designed to Ease, Crafted to Cheer."）。
5. **语言规范（极其重要）**：
   - 面向海外观众的内容：**【旁白（英文）】列与【字幕-显示卖点名及描述（英文）】列必须完全使用纯英文**（或对应的海外市场语言，绝对不要写中文翻译）。
   - 英文列严禁带字段名：以上两列的单元格内容不得包含“旁白/字幕/字段名/括号标签”等前缀（例如不要输出“【旁白（英文）】...”或“字幕：...”），必须直接输出纯英文内容。
   - 面向国内制作团队的内容：表格中的**所有其他列**必须严格使用全中文进行描述，以便国内的拍摄和剪辑团队能无障碍阅读和执行。禁止出现整句英文；如必须出现英文术语，只能作为中文句子中的少量术语/缩写出现。
   - 产品卖点：必须严格符合用户提供的信息，不可捏造。
6. **竞品链接**：表格中必须包含“竞品链接”字段，至少在“总结/收尾”行填写 1-3 条可用链接（使用用户提供的链接清单，不要编造）。
7. **竞品盖帽**：表格中必须新增“竞品盖帽”字段，用于一句话概括“本产品强于该竞品主打点的展示特点”。必须只基于本产品卖点写法，避免编造竞品参数/结论；可采用“对标点+本品优势”表达（例如：对标可视化/预设菜单/快速解冻，本品通过XX镜头更直观、更省事）。
8. **AI Prompt**：如需 AI 视频生成 Prompt，请将其放入“表现手法/特色效果/运镜方式”等中文描述字段中，以括号附带英文（如：[AI Prompt: xxx]）。
9. **整体要求**：必须遵循用户给定的“制作方式/风格/音乐/调性(色调)”整体要求，并在“表现手法/特色效果/整体AI视频生成Prompt”中体现一致的视觉与剪辑风格。

## 格式要求
必须以**标准的 Markdown 表格**形式输出，**请直接输出纯文本形式的表格，绝对不要将表格包裹在 ```markdown 或 ``` 代码块中！**
请确保每一行都用 `|` 完整闭合，表格必须统一使用以下 12 列：
| 结构分段 | 功能点 | 表现手法 | 旁白（英文） | 字幕-显示卖点名及描述（英文） | 特色效果 | 拍摄角度 | 运镜方式 | 竞品链接 | 竞品盖帽 | 音效 | 时长 |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |"""

# 在表格后追加整体 AI Prompt 的要求（不要影响表格渲染）
SYSTEM_PROMPT += """

## 额外输出（必须追加在表格之后）
在表格输出完成后，请紧接着追加以下内容（同样不要使用 ``` 代码块）：

整体AI视频生成Prompt（English）:
- 以一段完整英文描述输出，概括整支视频的统一风格、镜头语言、光影、场景、人物（如有）、产品露出与品牌调性，确保与表格分镜一致。
- 必须包含一致性约束：同一产品外观保持一致（颜色/材质/外观），同一厨房/家居风格保持一致，镜头节奏为短视频节奏（15-45秒）。
- 必须包含摄影/画面关键词：4k, cinematic lighting, shallow depth of field, smooth camera movement。
- 必须包含“品牌收尾”要求：最后镜头为产品静置全景特写 + Hisense Designed to Ease, Crafted to Cheer.
- 必须融合用户选择的整体要求：制作方式/风格/音乐/调性(色调)，并保持全片一致。

Negative Prompt（English，选填）:
- 输出一行即可，例如：no watermark, no subtitles baked into video, no extra logos, no deformed hands, no blurry frames

Recommended Settings（选填）:
- 输出一行即可，例如：16:9 or 9:16, 24fps, 4-6s clips per shot, realistic style
"""

def generate_script_bedrock(user_prompt, temperature=0.7, top_p=0.9, max_tokens=None):
    max_tokens = int(max_tokens or BEDROCK_MAX_TOKENS)
    try:
        import boto3

        client = boto3.client("bedrock-runtime", region_name=BEDROCK_AWS_REGION)
        response = client.converse(
            modelId=BEDROCK_MODEL_ID,
            system=[{"text": SYSTEM_PROMPT}],
            messages=[
                {
                    "role": "user",
                    "content": [{"text": user_prompt}],
                }
            ],
            inferenceConfig={
                "temperature": float(temperature),
                "maxTokens": max_tokens,
            },
        )
        content_blocks = response.get("output", {}).get("message", {}).get("content", [])
        return "\n".join([block.get("text", "") for block in content_blocks if block.get("text")]).strip()
    except Exception as e:
        import logging as _logging
        _logging.getLogger(__name__).error("Bedrock API call failed: %s", e, exc_info=True)
        return "脚本生成服务暂时不可用，请稍后重试。如持续失败请联系管理员。"


_SCRIPT_JOB_LOCK = threading.Lock()
_SCRIPT_JOB_THREADS: dict[str, threading.Thread] = {}


def load_script_jobs(limit=50):
    jobs = _safe_read_json(SCRIPT_JOBS_KEY, [])
    if not isinstance(jobs, list):
        jobs = []
    return jobs[:limit]


def save_script_jobs(jobs, limit=100):
    if not isinstance(jobs, list):
        jobs = []
    return _safe_write_json(SCRIPT_JOBS_KEY, jobs[:limit])


def _update_script_job(job_id, **fields):
    with _SCRIPT_JOB_LOCK:
        jobs = load_script_jobs(limit=100)
        for job in jobs:
            if job.get("id") == job_id:
                job.update(fields)
                job["updated_at"] = dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                save_script_jobs(jobs)
                return job
    return None


def _build_variant_prompt(i, request, competitor_links_inline, table_header_line):
    return f"""
请生成【方案{i}】海外电商短视频脚本（只输出这一套，不要输出其他方案标题）。
- 必须先输出一张符合系统要求的 Markdown 表格（12列，行内时长为秒，最后一行为总时长）。
- 表格必须包含并使用如下表头（逐字一致）：
{table_header_line}
- 表格后紧接着输出：整体AI视频生成Prompt（English）/ Negative Prompt / Recommended Settings。
- 与其他方案保持明显差异：开场hook、表现手法、镜头组织至少两处不同。
- 可用竞品链接与主打点（请从中选择填写到表格的“竞品链接”列，并在“竞品盖帽”列用一句话写本品在展示上的强项）：{competitor_links_inline}
- 若可用竞品链接为“无（请留空，不要编造）”，则表格中的“竞品链接/竞品盖帽”两列必须留空。
- 语言强约束：除【旁白（英文）】与【字幕-显示卖点名及描述（英文）】两列外，其余列（结构分段/功能点/表现手法/特色效果/拍摄角度/运镜方式/竞品盖帽/音效/时长）必须以中文为主；允许出现极少量大写缩写（如 UI/LED/4K）。
- 英文列格式强约束：两列英文内容不得带任何字段名/标签/括号前缀，直接输出纯英文句子。
- 卖点事实强约束：不得加入核心卖点中没有出现的功能概念或参数；例如核心卖点没有“变频/Inverter”，就不得把“变频”写入案例、竞品盖帽或脚本内容。
- 自定义需求优先级高于默认方向，但不得违背产品卖点事实：{request.get("自定义需求") or "无"}

输入参数：
- 目标平台：{request.get("目标平台", "")}
- 目标市场：{request.get("目标市场", "")}
- 建议视频类型：{request.get("视频类型定位", "")}
- 视频用途：{request.get("视频用途", "")}
- 期望视频时长(秒)：{request.get("期望视频时长(秒)", "")}
- 项目类型：{request.get("项目类型", "")}
- 制作方式：{request.get("制作方式", "")}
- 风格：{request.get("风格", "")}
- 音乐：{request.get("音乐", "")}
- 调性/色调：{request.get("调性/色调", "")}
- 产品品类：{request.get("产品品类", "")}
- 产品型号：{request.get("产品型号", "")}
- 核心卖点：{request.get("核心卖点", "")}
- 目标受众：{request.get("目标受众", "")}
- 用户痛点：{request.get("用户痛点", "")}
- 自定义需求：{request.get("自定义需求") or "无"}
- 内容发布日期：{request.get("内容发布日期", "")}
- 结合热点：{request.get("结合热点", "")}
""".strip()


def run_script_generation_job(job_id):
    job = _update_script_job(job_id, status="running", progress=0, error_message="")
    if not job:
        return
    try:
        request = job.get("request", {}) or {}
        competitor_items = job.get("competitor_items", []) or []
        allowed_competitor_urls = [it.get("url", "") for it in competitor_items if (it or {}).get("url")]
        if competitor_items:
            parts = []
            for it in competitor_items:
                title = (it or {}).get("title", "").strip()
                url = (it or {}).get("url", "").strip()
                brand = (it or {}).get("brand", "").strip()
                focus = (it or {}).get("focus_points", []) or []
                focus_text = " / ".join([x for x in focus if x])
                suffix = f" (focus: {focus_text})" if focus_text else ""
                prefix = f"{brand} - " if brand else ""
                parts.append(f"{prefix}{title}: {url}{suffix}")
            competitor_links_inline = " <br> ".join(parts)
        else:
            competitor_links_inline = "无（请留空，不要编造）"

        table_header_line = "| 结构分段 | 功能点 | 表现手法 | 旁白（英文） | 字幕-显示卖点名及描述（英文） | 特色效果 | 拍摄角度 | 运镜方式 | 竞品链接 | 竞品盖帽 | 音效 | 时长 |"
        variant_count = int(request.get("生成脚本套数") or 2)
        variants = []
        for i in range(1, variant_count + 1):
            fresh_job = next((j for j in load_script_jobs(limit=100) if j.get("id") == job_id), {})
            if fresh_job.get("status") == "cancel_requested":
                _update_script_job(job_id, status="cancelled", progress=int((i - 1) / variant_count * 100))
                return

            _update_script_job(job_id, status="running", progress=int((i - 1) / variant_count * 100), current_step=f"正在生成方案{i}")
            variant_prompt = _build_variant_prompt(i, request, competitor_links_inline, table_header_line)
            content = generate_script_bedrock(variant_prompt)
            content = _strip_code_fences(content)
            if (table_header_line not in content) or ("总时长" not in content):
                retry_prompt = variant_prompt + "\n\n补充要求：输出必须完整，不要截断；若篇幅过长请压缩行文但保留完整表格与总时长行。"
                content_retry = generate_script_bedrock(retry_prompt, temperature=0.3, top_p=0.8)
                content_retry = _strip_code_fences(content_retry)
                if (table_header_line in content_retry) and ("总时长" in content_retry):
                    content = content_retry

            ok_lang, _ = _validate_language_for_table(content)
            if not ok_lang:
                fix_prompt = f"""
请将下面脚本中的 Markdown 表格按以下规则“修复语言”并输出修复后的完整内容：
1) 保持表格列数/表头/行数/时长数字不变；
2) 仅【旁白（英文）】与【字幕-显示卖点名及描述（英文）】两列保留英文；
3) 表格中其他所有列必须改写为中文（禁止整句英文，允许极少量大写缩写如 UI/LED/4K 作为中文句子的一部分）；
4) 旁白/字幕两列不得带任何字段名/标签/括号前缀（不要输出“【旁白】...”或“字幕：...”），必须直接输出纯英文内容；
5) 不要添加额外解释性文字，直接输出修复后的脚本（表格+表格后附加内容）。

原内容：
{content}
""".strip()
                fixed = content
                for _ in range(2):
                    fixed_try = generate_script_bedrock(fix_prompt, temperature=0.2, top_p=0.7)
                    fixed_try = _strip_code_fences(fixed_try)
                    ok_lang2, _ = _validate_language_for_table(fixed_try)
                    if ok_lang2:
                        fixed = fixed_try
                        break
                    fixed = fixed_try
                content = fixed

            ok_lang3, _ = _validate_language_for_table(content)
            if not ok_lang3:
                content = _force_non_english_columns_to_cn(content)
            content = _normalize_variant_content(content, allowed_competitor_urls)
            variants.append({"name": f"方案{i}", "label": infer_variant_label(content), "content": content})
            _update_script_job(job_id, progress=int(i / variant_count * 100), partial_count=len(variants))

        _update_script_job(
            job_id,
            status="succeeded",
            progress=100,
            current_step="已完成",
            variants=variants,
            completed_at=dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        )
        append_history_record({
            "created_at": dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "category": request.get("产品品类", ""),
            "model": request.get("产品型号", ""),
            "platform": request.get("目标平台", ""),
            "market": request.get("目标市场", ""),
            "labels": [v.get("label", "") for v in variants],
            "variants": variants,
            "config": request,
        })
    except Exception as exc:
        _update_script_job(job_id, status="failed", error_message=str(exc), current_step="生成失败")


def start_script_generation_job(job_id):
    thread = _SCRIPT_JOB_THREADS.get(job_id)
    if thread and thread.is_alive():
        return
    thread = threading.Thread(target=run_script_generation_job, args=(job_id,), daemon=True)
    _SCRIPT_JOB_THREADS[job_id] = thread
    thread.start()


def create_script_generation_job(request, competitor_items):
    job = {
        "id": uuid.uuid4().hex[:12],
        "created_at": dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "updated_at": dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "status": "pending",
        "progress": 0,
        "current_step": "已提交，等待生成",
        "request": request,
        "competitor_items": competitor_items,
        "variants": [],
        "error_message": "",
    }
    with _SCRIPT_JOB_LOCK:
        jobs = load_script_jobs(limit=100)
        jobs.insert(0, job)
        save_script_jobs(jobs)
    start_script_generation_job(job["id"])
    return job

if "generated_variants" not in st.session_state:
    st.session_state["generated_variants"] = []
if "generated_excel_bytes" not in st.session_state:
    st.session_state["generated_excel_bytes"] = None

if st.button("提交脚本生成任务", type="primary", use_container_width=True):
    feature_details = []
    for v in selected_features:
        desc = ""
        if not model_features.empty and "Feature Description" in model_features.columns:
            matches = model_features[model_features["Feature Name"] == v]
            if not matches.empty:
                desc_val = matches.iloc[0].get("Feature Description", "")
                desc = str(desc_val).strip() if desc_val is not None else ""
        feature_details.append({"name": v, "description": desc})

    core_features_md = "；".join([
        f"{i+1}. {x['name']}{(' — ' + x['description']) if x.get('description') else ''}"
        for i, x in enumerate(feature_details)
    ]) if feature_details else ""
    competitor_items = get_competitor_items(selected_category, platform, target_market)
    st.session_state["last_competitor_items"] = competitor_items
    config_dict = {
        "目标平台": platform,
        "目标市场": target_market,
        "视频类型定位": ", ".join(video_type),
        "视频用途": video_usage,
        "期望视频时长(秒)": expected_duration,
        "项目类型": project_type,
        "制作方式": production_method,
        "风格": overall_style,
        "音乐": music_style,
        "调性/色调": tone_color,
        "产品品类": selected_category,
        "产品型号": selected_model,
        "核心卖点": core_features_md,
        "目标受众": target_audience if target_audience else "通用卖点（不指定具体人群）",
        "用户痛点": pain_points,
        "自定义需求": custom_requirements,
        "内容发布日期": str(publish_date),
        "结合热点": festival_hotspot,
        "生成脚本套数": variant_count,
    }
    job = create_script_generation_job(config_dict, competitor_items)
    st.success(f"已提交生成任务：{job['id']}。可以刷新任务中心查看进度，页面可继续操作。")
    st.rerun()

with st.expander("任务中心", expanded=True):
    jobs = load_script_jobs()
    if not jobs:
        st.caption("暂无脚本生成任务。")
    else:
        status_label = {
            "pending": "排队中",
            "running": "生成中",
            "succeeded": "已完成",
            "failed": "失败",
            "cancel_requested": "取消中",
            "cancelled": "已取消",
        }
        st.dataframe(
            pd.DataFrame([
                {
                    "任务ID": j.get("id", ""),
                    "创建时间": j.get("created_at", ""),
                    "产品": f"{(j.get('request') or {}).get('产品品类', '')} / {(j.get('request') or {}).get('产品型号', '')}",
                    "状态": status_label.get(j.get("status", ""), j.get("status", "")),
                    "进度": f"{int(j.get('progress', 0) or 0)}%",
                    "当前步骤": j.get("current_step", ""),
                    "失败原因": j.get("error_message", ""),
                }
                for j in jobs[:12]
            ]),
            use_container_width=True,
            hide_index=True,
        )
        col_refresh, col_action = st.columns(2)
        with col_refresh:
            if st.button("刷新任务状态", use_container_width=True):
                st.rerun()
        selectable_jobs = [j for j in jobs if j.get("status") in {"succeeded", "failed", "pending", "running", "cancel_requested"}]
        if selectable_jobs:
            selected_job_id = st.selectbox(
                "选择任务操作",
                [j.get("id", "") for j in selectable_jobs],
                format_func=lambda jid: next(
                    (
                        f"{jid}｜{status_label.get(j.get('status', ''), j.get('status', ''))}｜{(j.get('request') or {}).get('产品型号', '')}"
                        for j in selectable_jobs
                        if j.get("id") == jid
                    ),
                    jid,
                ),
            )
            selected_job = next((j for j in selectable_jobs if j.get("id") == selected_job_id), None)
            if selected_job:
                action_cols = st.columns(3)
                with action_cols[0]:
                    if selected_job.get("status") == "succeeded" and st.button("载入完成结果", use_container_width=True):
                        variants = selected_job.get("variants", []) or []
                        request = selected_job.get("request", {}) or {}
                        st.session_state["generated_variants"] = variants
                        st.session_state["last_competitor_items"] = selected_job.get("competitor_items", []) or []
                        st.session_state["generated_excel_bytes"] = _build_excel_bytes(
                            variants,
                            request,
                            request.get("产品品类", ""),
                            competitor_items=selected_job.get("competitor_items", []),
                        )
                        st.session_state["history_loaded_note"] = f"已载入任务结果：{selected_job_id}"
                        st.rerun()
                with action_cols[1]:
                    if selected_job.get("status") == "failed" and st.button("重试失败任务", use_container_width=True):
                        _update_script_job(selected_job_id, status="pending", progress=0, current_step="已重新提交", error_message="", variants=[])
                        start_script_generation_job(selected_job_id)
                        st.rerun()
                with action_cols[2]:
                    if selected_job.get("status") in {"pending", "running"} and st.button("取消任务", use_container_width=True):
                        _update_script_job(selected_job_id, status="cancel_requested", current_step="正在尝试取消")
                        st.rerun()

if st.session_state.get("generated_variants"):
    variants = st.session_state["generated_variants"]
    allowed_urls_view = [it.get("url", "") for it in (st.session_state.get("last_competitor_items") or []) if (it or {}).get("url")]
    display_variants = []
    for v in variants:
        nv = dict(v)
        nv["content"] = _normalize_variant_content(v.get("content", ""), allowed_urls_view)
        display_variants.append(nv)
    variants = display_variants
    tabs = st.tabs([v.get("name", f"方案{i+1}") for i, v in enumerate(variants)])
    for i, v in enumerate(variants):
        with tabs[i]:
            if v.get("label"):
                st.caption(f"方案定位：{v.get('label')}")
            st.markdown(v.get("content", "").strip() + build_reference_links_md(selected_category, competitor_items=st.session_state.get("last_competitor_items")))

    excel_bytes = st.session_state.get("generated_excel_bytes")
    if excel_bytes:
        safe_model = re.sub(r"[^A-Za-z0-9_-]+", "_", str(selected_model))[:50] if selected_model else "model"
        st.download_button(
            "下载 Excel（含多套方案）",
            data=excel_bytes,
            file_name=f"video_script_{safe_model}_{dt.date.today().isoformat()}.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            use_container_width=True,
        )

    with st.expander("生成 Nova Reel 视频参考片段", expanded=False):
        st.caption("建议先确认上方脚本文档无误，再选择一个方案生成 6 秒参考视频片段。这里的 6 秒指成片时长，不是生成等待时间；生成完成后可在页面内预览，无需进入 S3。")
        if st.session_state.get("nova_reel_submit_notice"):
            st.success(st.session_state.pop("nova_reel_submit_notice"))
        if st.session_state.get("nova_reel_submit_error"):
            st.error(st.session_state.pop("nova_reel_submit_error"))
        if st.session_state.get("nova_reel_refresh_notice"):
            st.success(st.session_state.pop("nova_reel_refresh_notice"))
        if st.session_state.get("nova_reel_refresh_error"):
            st.error(st.session_state.pop("nova_reel_refresh_error"))

        variant_idx = st.selectbox(
            "选择要生成视频片段的脚本方案",
            range(len(variants)),
            format_func=lambda i: f"{variants[i].get('name', f'方案{i+1}')}｜{variants[i].get('label', '')}".strip("｜"),
        )
        selected_variant = variants[variant_idx]
        video_prompt = build_variant_nova_reel_prompt(selected_variant, selected_category, selected_model, selected_features)
        st.text_area("将提交给 Nova Reel 的视频 Prompt", video_prompt, height=150, disabled=True)

        estimated_seconds = 6
        st.caption(
            f"本次会提交 1 条视频任务，目标成片时长约 6 秒；费用按成片时长估算："
            f"${NOVA_REEL_ESTIMATED_USD_PER_SECOND:.2f}/成片秒，约 "
            f"${estimated_seconds * NOVA_REEL_ESTIMATED_USD_PER_SECOND:.2f}。"
            "生成等待时间取决于 Bedrock 队列和账号限流，通常明显长于 6 秒；实际耗时和费用以 AWS 为准。"
        )

        col_submit_video, col_refresh_video = st.columns(2)
        with col_submit_video:
            if st.button("生成此方案的视频参考片段", use_container_width=True):
                with st.spinner(f"正在提交 Nova Reel 异步任务...（{NOVA_REEL_MODEL_ID} / {NOVA_REEL_AWS_REGION}）"):
                    jobs = load_nova_reel_poc_jobs()
                    try:
                        invocation_arn, output_s3_uri = start_nova_reel_job(selected_category, selected_model, video_prompt)
                        jobs.insert(
                            0,
                            {
                                "created_at": dt.datetime.utcnow().isoformat(timespec="seconds") + "Z",
                                "category": selected_category,
                                "model": selected_model,
                                "variant_name": selected_variant.get("name", f"方案{variant_idx + 1}"),
                                "variant_label": selected_variant.get("label", ""),
                                "prompt": video_prompt,
                                "duration_seconds": 6,
                                "status": "InProgress",
                                "invocation_arn": invocation_arn,
                                "output_s3_uri": output_s3_uri,
                                "video_s3_uri": "",
                                "model_id": NOVA_REEL_MODEL_ID,
                                "region": NOVA_REEL_AWS_REGION,
                                "source": "script_variant",
                            },
                        )
                        save_nova_reel_poc_jobs(jobs)
                        st.session_state["nova_reel_submit_notice"] = "已提交 1 条 Nova Reel 视频任务。6 秒是目标成片时长，生成任务为异步排队处理，请稍后刷新状态。"
                    except Exception as exc:
                        st.session_state["nova_reel_submit_error"] = f"视频任务提交失败：{exc}"
                st.rerun()
        with col_refresh_video:
            if st.button("刷新视频生成状态", use_container_width=True):
                with st.spinner("正在刷新 Nova Reel 任务状态..."):
                    jobs = load_nova_reel_poc_jobs()
                    refreshed = 0
                    errors = []
                    for job in jobs:
                        if job.get("source") != "script_variant":
                            continue
                        arn = job.get("invocation_arn", "")
                        if not arn or job.get("status") in {"Completed", "Failed"}:
                            continue
                        try:
                            result = query_nova_reel_job(arn)
                            job["status"] = result.get("status", job.get("status", "Unknown"))
                            job["updated_at"] = dt.datetime.utcnow().isoformat(timespec="seconds") + "Z"
                            if result.get("failureMessage"):
                                job["failure_message"] = result.get("failureMessage")
                            if job["status"] == "Completed":
                                job["output_s3_uri"] = (
                                    (result.get("outputDataConfig") or {})
                                    .get("s3OutputDataConfig", {})
                                    .get("s3Uri", job.get("output_s3_uri", ""))
                                )
                                job["video_s3_uri"] = _video_uri_from_job(job)
                            refreshed += 1
                        except Exception as exc:
                            errors.append(f"{job.get('variant_name', '')}：{exc}")
                    save_nova_reel_poc_jobs(jobs)
                    if refreshed:
                        st.session_state["nova_reel_refresh_notice"] = f"已刷新 {refreshed} 条视频任务。"
                    if errors:
                        st.session_state["nova_reel_refresh_error"] = "部分任务刷新失败：\n" + "\n".join(errors[:5])
                st.rerun()

        jobs = [
            job
            for job in load_nova_reel_poc_jobs()
            if job.get("source") == "script_variant"
            and str(job.get("category", "")) == str(selected_category)
            and str(job.get("model", "")) == str(selected_model)
        ]
        if jobs:
            display_jobs = []
            for job in jobs[:20]:
                video_uri = job.get("video_s3_uri") or _video_uri_from_job(job)
                display_jobs.append(
                    {
                        "创建时间": job.get("created_at", ""),
                        "方案": job.get("variant_name", ""),
                        "状态": job.get("status", ""),
                        "失败原因": job.get("failure_message", ""),
                        "模型": job.get("model_id", ""),
                    }
                )
            st.dataframe(pd.DataFrame(display_jobs), use_container_width=True, hide_index=True)
            completed = [job for job in jobs if (job.get("video_s3_uri") or _video_uri_from_job(job)) and job.get("status") == "Completed"]
            if completed:
                selected_job_idx = st.selectbox(
                    "选择已完成视频片段",
                    range(len(completed)),
                    format_func=lambda i: f"{completed[i].get('variant_name', '')}｜{completed[i].get('created_at', '')}",
                )
                video_uri = completed[selected_job_idx].get("video_s3_uri") or _video_uri_from_job(completed[selected_job_idx])
                presigned_url = _presigned_url_for_s3_uri(video_uri)
                if presigned_url:
                    st.video(presigned_url)
                    st.link_button("打开/下载视频片段", presigned_url, use_container_width=True)
                else:
                    st.warning("视频已生成，但临时预览链接创建失败。请联系平台维护人员检查 S3 权限。")

    with st.expander("试用反馈", expanded=False):
        feedback_score = st.radio("本次生成结果可用性", ["好用", "一般", "不可用"], horizontal=True)
        feedback_issue = st.multiselect("主要问题类型（可多选）", ["语言不合规", "表格格式", "竞品链接", "热点趋势", "脚本创意", "导出体验", "其他"])
        feedback_note = st.text_area("补充说明（可选）", "")
        if st.button("提交试用反馈", use_container_width=True):
            ok = save_feedback_record({
                "created_at": dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "category": selected_category,
                "model": selected_model,
                "platform": platform,
                "market": target_market,
                "score": feedback_score,
                "issues": feedback_issue,
                "note": feedback_note.strip(),
                "labels": [v.get("label", "") for v in variants],
            })
            if ok:
                st.success("反馈已记录，后续会用于试用期优化。")
            else:
                st.error("反馈保存失败，请稍后重试。")

import streamlit as st
import requests
import os
import pandas as pd
import datetime as dt
import io
import re
import hmac
import xml.etree.ElementTree as ET
import urllib.parse

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

BEDROCK_AWS_REGION = (
    os.getenv("BEDROCK_AWS_REGION")
    or os.getenv("AWS_REGION")
    or os.getenv("AWS_DEFAULT_REGION")
    or "us-east-1"
)
BEDROCK_MODEL_ID = os.getenv("BEDROCK_MODEL_ID", "eu.amazon.nova-pro-v1:0")
BEDROCK_MAX_TOKENS = int(os.getenv("BEDROCK_MAX_TOKENS", "4096"))
APP_ACCESS_PASSWORD = os.getenv("APP_ACCESS_PASSWORD", "")

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
    "微波炉": ["microwave", "inverter microwave"],
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
    "微波炉": [
        {
            "brand": "Panasonic",
            "title": "Panasonic Inverter Microwaves（产品视频示例）",
            "url": "https://m.youtube.com/watch?v=k50Ckg_E4rU",
            "focus_points": ["Inverter（变频）概念表达", "均匀加热/解冻体验", "操作与场景演示"],
        },
        {
            "brand": "LG",
            "title": "LG NeoChef（Smart Inverter 卖点视频示例）",
            "url": "https://m.youtube.com/watch?v=0RDCJqSF4dY",
            "focus_points": ["Smart Inverter（智能变频）概念", "控温/解冻体验", "外观与厨房场景"],
        },
    ],
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
        resp = requests.get(url, headers=headers, timeout=10, verify=False)
        resp.raise_for_status()
        root = ET.fromstring(resp.content)
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
            resp = requests.get(url, params={"q": q}, headers=headers, timeout=10, verify=False)
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
        "意境表达": "温馨生活氛围",
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

def require_access():
    if not APP_ACCESS_PASSWORD:
        return True
    if st.session_state.get("__access_granted"):
        return True

    st.title("🎬 海外电商视频脚本生成器")
    st.info("请输入访问密码。")
    password = st.text_input("访问密码", type="password")
    if st.button("进入平台", type="primary"):
        if hmac.compare_digest((password or "").encode("utf-8"), APP_ACCESS_PASSWORD.encode("utf-8")):
            st.session_state["__access_granted"] = True
            st.rerun()
        else:
            st.error("访问密码不正确。")
    st.stop()

st.set_page_config(page_title="海外电商视频脚本生成器", page_icon="🎬", layout="wide")

require_access()

st.title("🎬 海外电商视频脚本生成器")
st.markdown("基于《电商产品种草视频知识库》规范，为您自动生成高转化率的短视频脚本。")

with st.sidebar:
    st.header("⚙️ 基础配置")
    platform = st.selectbox("目标平台", ["TikTok / Reels / Shorts", "Amazon 主图视频", "独立站详情页"])
    target_market = st.selectbox("目标市场", ["北美 (US/CA)", "欧洲 (UK/DE/FR)", "东南亚", "其他"])
    video_type = st.multiselect("视频类型定位 (可多选)", 
                                ["问题解决/痛点挖掘型", "产品展示/功能介绍型", "开箱体验型", "场景化/生活方式型", "测评/对比型"],
                                default=["问题解决/痛点挖掘型", "场景化/生活方式型"])
    variant_count = st.selectbox("生成脚本套数", [2, 3], index=0)
    production_method = st.selectbox("制作方式", ["实拍", "渲染"])
    overall_style = st.selectbox("风格", ["科技", "时尚", "温馨家居", "高端奢华"])
    music_style = st.selectbox("音乐", ["舒缓", "日系"])
    tone_color = st.selectbox("调性/色调", ["暖色", "冷色", "日系"])
    
# 检查是否已有缓存数据
df_products = get_product_data()
cache_meta = load_cache_meta()

# 数据上传模块
if df_products.empty:
    st.info("👋 欢迎使用！首次使用请上传您的《产品卖点库》Excel 文件。")
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
    with st.expander("🔄 更新产品卖点库 (目前已加载数据)"):
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
    with st.expander("🕘 最近生成记录", expanded=False):
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
            if st.button("📂 载入该次结果", use_container_width=True):
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
    st.subheader("📦 产品信息")
    
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

    st.caption(f"已选择 {len(selected_features)} 个卖点（用于生成脚本）")

    with st.expander("🏷️ 竞品素材配置（按品类缓存）", expanded=False):
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
            if st.button("💾 保存该品类配置", use_container_width=True):
                ok = save_competitor_config(category_key, {"brands": brands, "selected_urls": selected_urls, "manual_urls": manual_urls})
                if ok:
                    st.success("已保存（该品类配置会缓存在云端服务器文件中）")
                    st.rerun()
                else:
                    st.error("保存失败，请稍后重试")
        with col_hint:
            st.caption("说明：该配置按“品类”写入云端服务器本地文件，属于应用内部配置；未匹配到链接时会留空，避免乱填。")

with col2:
    st.subheader("🎯 营销诉求")
    video_usage = st.selectbox("视频用途", ["站外种草", "站内首推", "内部培训", "其他"])
    expected_duration = st.slider("期望视频时长(秒)", 15, 45, 30, 1)
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

    with st.expander("📅 节日/热点推荐（可选）", expanded=False):
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
你是一位专业的视频脚本撰写智能体，为海信海外电商产品策划推广提供服务。你需要基于海信的产品卖点，撰写不同类型（产品展示视频、产品介绍视频、产品操作视频、产品种草视频等）的视频脚本，以支持导出为word或excel形式的Markdown表格输出。 
 
##限制与优化规范
1. **时长精确控制**：脚本总时长需严格控制在 15-45 秒以内，并尽量贴近用户给定的“期望视频时长(秒)”。表格的“时长”列必须给出**确切的秒数**（如：5秒），并在表格最后一行增加“总时长”统计。
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
9. **整体要求**：必须遵循用户给定的“制作方式/风格/音乐/调性(色调)”整体要求，并在“意境表达/表现手法/特色效果/整体AI视频生成Prompt”中体现一致的视觉与剪辑风格。

## 格式要求
必须以**标准的 Markdown 表格**形式输出，**请直接输出纯文本形式的表格，绝对不要将表格包裹在 ```markdown 或 ``` 代码块中！**
请确保每一行都用 `|` 完整闭合，表格必须统一使用以下 13 列：
| 结构分段 | 功能点 | 意境表达 | 表现手法 | 旁白（英文） | 字幕-显示卖点名及描述（英文） | 特色效果 | 拍摄角度 | 运镜方式 | 竞品链接 | 竞品盖帽 | 音效 | 时长 |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |"""

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
        return (
            f"Bedrock API 调用失败: {str(e)}\n"
            f"请检查 AWS 凭证、区域 {BEDROCK_AWS_REGION}、模型权限 {BEDROCK_MODEL_ID} 是否已配置。"
        )

if "generated_variants" not in st.session_state:
    st.session_state["generated_variants"] = []
if "generated_excel_bytes" not in st.session_state:
    st.session_state["generated_excel_bytes"] = None

if st.button("🚀 生成爆款脚本", type="primary", use_container_width=True):
    with st.spinner(f"正在调用 Amazon Bedrock 生成脚本...（{BEDROCK_MODEL_ID} / {BEDROCK_AWS_REGION}）"):
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
                "内容发布日期": str(publish_date),
                "结合热点": festival_hotspot,
                "生成脚本套数": variant_count,
            }

            table_header_line = "| 结构分段 | 功能点 | 意境表达 | 表现手法 | 旁白（英文） | 字幕-显示卖点名及描述（英文） | 特色效果 | 拍摄角度 | 运镜方式 | 竞品链接 | 竞品盖帽 | 音效 | 时长 |"

            variants = []
            progress = st.progress(0)
            for i in range(1, int(variant_count) + 1):
                progress.progress(int((i - 1) / int(variant_count) * 100))
                variant_prompt = f"""
请生成【方案{i}】海外电商短视频脚本（只输出这一套，不要输出其他方案标题）。
- 必须先输出一张符合系统要求的 Markdown 表格（13列，行内时长为秒，最后一行为总时长）。
- 表格必须包含并使用如下表头（逐字一致）：
{table_header_line}
- 表格后紧接着输出：整体AI视频生成Prompt（English）/ Negative Prompt / Recommended Settings。
- 与其他方案保持明显差异：开场hook、意境表达、表现手法至少两处不同。
- 可用竞品链接与主打点（请从中选择填写到表格的“竞品链接”列，并在“竞品盖帽”列用一句话写本品在展示上的强项）：{competitor_links_inline}
- 若可用竞品链接为“无（请留空，不要编造）”，则表格中的“竞品链接/竞品盖帽”两列必须留空。
- 语言强约束：除【旁白（英文）】与【字幕-显示卖点名及描述（英文）】两列外，其余列（结构分段/功能点/意境表达/表现手法/特色效果/拍摄角度/运镜方式/竞品盖帽/音效/时长）必须以中文为主；允许出现极少量大写缩写（如 UI/LED/4K）。
- 英文列格式强约束：两列英文内容不得带任何字段名/标签/括号前缀，直接输出纯英文句子。

输入参数：
- 目标平台：{platform}
- 目标市场：{target_market}
- 建议视频类型：{', '.join(video_type)}
- 视频用途：{video_usage}
- 期望视频时长(秒)：{expected_duration}
- 项目类型：{project_type}
- 制作方式：{production_method}
- 风格：{overall_style}
- 音乐：{music_style}
- 调性/色调：{tone_color}
- 产品品类：{selected_category}
- 产品型号：{selected_model}
- 核心卖点：{core_features_md}
- 目标受众：{target_audience if target_audience else "通用卖点（不指定具体人群）"}
- 用户痛点：{pain_points}
- 内容发布日期：{publish_date if 'publish_date' in locals() else ""}
- 结合热点：{festival_hotspot}
""".strip()

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

            progress.progress(100)
            
            st.success("脚本生成成功！")
            st.markdown("### 📝 生成结果预览")
            st.session_state["generated_variants"] = variants
            st.session_state["generated_excel_bytes"] = _build_excel_bytes(variants, config_dict, selected_category, competitor_items=st.session_state.get("last_competitor_items"))
            append_history_record({
                "created_at": dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "category": selected_category,
                "model": selected_model,
                "platform": platform,
                "market": target_market,
                "labels": [v.get("label", "") for v in variants],
                "variants": variants,
                "config": config_dict,
            })

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
            "⬇️ 下载Excel（含多套方案）",
            data=excel_bytes,
            file_name=f"video_script_{safe_model}_{dt.date.today().isoformat()}.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            use_container_width=True,
        )

    with st.expander("📝 试用反馈收集", expanded=False):
        feedback_score = st.radio("本次生成结果可用性", ["好用", "一般", "不可用"], horizontal=True)
        feedback_issue = st.multiselect("主要问题类型（可多选）", ["语言不合规", "表格格式", "竞品链接", "热点趋势", "脚本创意", "导出体验", "其他"])
        feedback_note = st.text_area("补充说明（可选）", "")
        if st.button("📮 提交试用反馈", use_container_width=True):
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

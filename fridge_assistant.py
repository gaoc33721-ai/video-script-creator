import datetime as dt
import hmac
import io
import json
import os
import re
import threading
import time
import uuid
from pathlib import Path
from typing import Callable

import boto3
import pandas as pd
from fastapi import Depends, File, Header, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field


FRIDGE_DATASETS = {"specs", "marketing", "competitors", "documents"}
FRIDGE_DATA_PREFIX = "fridge"
FRIDGE_MAX_UPLOAD_BYTES = int(os.getenv("FRIDGE_MAX_UPLOAD_BYTES", str(20 * 1024 * 1024)))
FRIDGE_SESSION_LIMIT = int(os.getenv("FRIDGE_SESSION_LIMIT", "100"))
FRIDGE_BEDROCK_MODEL_ID = os.getenv("FRIDGE_BEDROCK_MODEL_ID") or os.getenv("BEDROCK_MODEL_ID", "eu.amazon.nova-pro-v1:0")
FRIDGE_BEDROCK_REGION = (
    os.getenv("FRIDGE_BEDROCK_REGION")
    or os.getenv("BEDROCK_AWS_REGION")
    or os.getenv("AWS_REGION")
    or os.getenv("AWS_DEFAULT_REGION")
    or "us-east-1"
)
FRIDGE_BEDROCK_MAX_TOKENS = int(os.getenv("FRIDGE_BEDROCK_MAX_TOKENS", "2048"))
FRIDGE_CANONICAL_HOST = os.getenv("FRIDGE_CANONICAL_HOST", "videoscript.hisense.com").strip().lower()
FRIDGE_CANONICAL_SCHEME = os.getenv("FRIDGE_CANONICAL_SCHEME", "https").strip() or "https"
FRIDGE_DUPLICATE_WINDOW_SECONDS = int(os.getenv("FRIDGE_DUPLICATE_WINDOW_SECONDS", "30"))


SPEC_ALIASES = {
    "model": ["model", "型号", "产品型号", "型号编码", "sku", "product_model", "product model"],
    "series": ["series", "系列", "产品系列", "系列名称", "product_line", "product_line_name", "Product Series"],
    "brand": ["brand", "品牌", "メーカー"],
    "product_name": ["product_name", "产品名称", "型号名称", "name", "title"],
    "product_type": ["product_type", "产品类型", "Porduct Type", "Product Type", "品类", "category", "カテゴリー"],
    "market": ["market", "市场", "国家", "区域", "region", "country", "Target Market", "销售区域"],
    "country": ["country", "出口国家", "Country"],
    "launch_status": ["launch_status", "上市状态", "状态", "status"],
    "capacity_total_l": ["capacity_total_l", "总容积", "总容量", "总容积(l)", "total capacity"],
    "washing_capacity_kg": ["washing_capacity_kg", "洗涤容量", "洗濯容量", "Washing Capacity", "洗涤容量(KG)", "公斤段"],
    "drying_capacity_kg": ["drying_capacity_kg", "烘干容量", "烘干容量 (KG)", "Drying capacity (KG)", "乾燥容量"],
    "drum_volume_l": ["drum_volume_l", "内筒容积", "内筒容积（L）", "Drum Volum", "Drum Volume"],
    "fridge_capacity_l": ["fridge_capacity_l", "冷藏容积", "冷藏容量", "fridge capacity"],
    "freezer_capacity_l": ["freezer_capacity_l", "冷冻容积", "冷冻容量", "freezer capacity"],
    "energy_rating": ["energy_rating", "能效等级", "能效", "energy class", "energy rating"],
    "energy_rating_wash": ["energy_rating_wash", "能耗等级", "Energy Rating （Wash）", "Energy Rating (Wash)", "洗涤能效"],
    "energy_rating_dry": ["energy_rating_dry", "冷凝效率等级", "Energy Rating（Dry）", "Energy Rating (Dry)", "烘干能效"],
    "water_rating": ["water_rating", "Water Rating", "冷凝效率", "水效"],
    "energy_consumption": ["energy_consumption", "耗电量", "年耗电量", "energy consumption", "kwh"],
    "noise_db": ["noise_db", "噪音", "噪声", "噪音(db)", "noise", "noise level", "Noise Level", "運転音"],
    "width_mm": ["width_mm", "宽度", "净宽度", "宽(mm)", "width", "Product Width (mm)", "幅"],
    "depth_mm": ["depth_mm", "深度", "净深度", "深(mm)", "depth", "Product Depth (mm)", "奥行"],
    "height_mm": ["height_mm", "高度", "净高度", "高(mm)", "height", "Product Height (mm)", "高さ"],
    "dimensions": ["dimensions", "尺寸", "Dimensions", "Net Dimensions WxDxH (mm)", "净尺寸 WxDxH (mm)"],
    "net_weight_kg": ["net_weight_kg", "净重", "净重 (Kg)", "Net Weight", "本体重量"],
    "cooling_type": ["cooling_type", "制冷方式", "风冷", "制冷系统", "cooling"],
    "compressor_type": ["compressor_type", "压缩机", "压缩机类型", "compressor"],
    "inverter": ["inverter", "变频", "inverter compressor", "Inverter motor", "インバーター搭載"],
    "door_type": ["door_type", "门体", "开门方式", "door", "door type"],
    "drying_mode": ["drying_mode", "烘干类型", "Drying mode", "乾燥方式", "乾燥方式"],
    "installation_type": ["installation_type", "安装方式", "Built in/Free standing"],
    "supply_voltage": ["supply_voltage", "电压", "电压 (V/Hz)", "Supply Voltage (V/Hz)"],
    "programs": ["programs", "程序", "Programs", "Use Program"],
    "key_features": ["key_features", "功能", "Features"],
    "color": ["color", "颜色", "colour", "Product Color", "整机颜色", "色"],
    "image_url": ["image_url", "图片", "图片url", "主图", "product image"],
    "certification": ["certification", "认证", "认证报告", "证书", "certification report", "Cerficate", "认证要求"],
    "price": ["price", "价格", "售价", "msrp", "pvp", "fob", "FOB", "PVP Price", "Selling in Price", "価格"],
    "currency": ["currency", "币种", "货币"],
}

MARKETING_ALIASES = {
    "model": ["model", "型号", "产品型号", "适用型号", "sku"],
    "series": ["series", "系列", "适用系列", "产品系列"],
    "scope": ["scope", "范围", "适用范围", "层级"],
    "content_type": ["content_type", "类型", "素材类型", "数据项", "类别"],
    "title": ["title", "标题", "卖点标题", "问题", "faq_question", "point_name", "usp"],
    "content": ["content", "正文", "详情", "答案", "faq_answer", "long_copy", "话术", "指南"],
    "keywords": ["keywords", "关键词", "触发关键词", "同义词"],
    "pain_point": ["pain_point", "痛点", "用户痛点"],
    "objection": ["objection", "异议", "反对意见"],
    "response": ["response", "应对话术", "异议处理", "回答"],
    "priority": ["priority", "优先级", "权重"],
}

COMPETITOR_ALIASES = {
    "competitor_model": ["competitor_model", "竞品型号", "型号", "型番", "model", "sku"],
    "brand": ["brand", "竞品品牌", "品牌", "Brand\n品牌", "メーカー"],
    "category": ["category", "品类", "产品类型", "Category\n品类", "Category\n品类（已更新）", "カテゴリー"],
    "market": ["market", "市场", "区域", "Market\n区域", "上线平台", "法人"],
    "price": ["price", "价格", "上市价格", "当前价格", "msrp", "価格"],
    "currency": ["currency", "币种", "货币"],
    "capacity_total_l": ["capacity_total_l", "总容积", "容量", "总容量", "Washing Capacity", "洗濯容量", "乾燥容量", "公斤段"],
    "energy_rating": ["energy_rating", "能效", "能效等级", "Energy Rating"],
    "water_rating": ["water_rating", "Water Rating", "水效"],
    "noise_db": ["noise_db", "噪音", "噪声", "運転音"],
    "feature_name": ["feature_name", "Feature Name", "Feature Name\n卖点名", "卖点名", "卖点名称"],
    "slogan": ["slogan", "Slogan", "Slogan\n标题", "标题"],
    "meaning": ["meaning", "Meaning", "Meaning\n传播点", "传播点"],
    "description": ["description", "Description", "Description\n描述", "描述"],
    "tm_certified": ["tm_certified", "TM认证", "TM认证\nY/N", "商标认证"],
    "owner": ["owner", "Owner", "Owner\n信息主人", "信息主人"],
    "updated_at": ["updated_at", "Last Updated", "Last Updated\n更新日期", "更新日期"],
    "core_params": ["core_params", "核心参数", "参数", "卖点", "Feature Name", "Description"],
    "positive_keywords": ["positive_keywords", "正面关键词", "好评关键词"],
    "negative_keywords": ["negative_keywords", "负面关键词", "差评关键词"],
    "source_url": ["source_url", "链接", "来源链接", "商品链接", "Link\n链接", "url"],
}

DOCUMENT_ALIASES = {
    "model": ["model", "型号", "产品型号", "适用型号"],
    "series": ["series", "系列", "适用系列"],
    "title": ["title", "标题", "文件名", "文档名称", "name"],
    "summary": ["summary", "摘要", "人工摘要", "核心摘要"],
    "content": ["content", "正文", "文本", "内容", "text"],
    "source_url": ["source_url", "链接", "来源链接", "url"],
}

KEY_SPEC_COLUMNS = [
    "model",
    "brand",
    "series",
    "product_type",
    "market",
    "country",
    "capacity_total_l",
    "washing_capacity_kg",
    "drying_capacity_kg",
    "drum_volume_l",
    "fridge_capacity_l",
    "freezer_capacity_l",
    "energy_rating",
    "energy_rating_wash",
    "energy_rating_dry",
    "water_rating",
    "energy_consumption",
    "noise_db",
    "width_mm",
    "depth_mm",
    "height_mm",
    "dimensions",
    "net_weight_kg",
    "cooling_type",
    "compressor_type",
    "inverter",
    "drying_mode",
    "installation_type",
    "supply_voltage",
    "programs",
    "key_features",
    "door_type",
    "color",
    "price",
    "currency",
    "certification",
]

SPEC_LABELS = {
    "model": "型号",
    "brand": "品牌",
    "series": "系列",
    "product_type": "产品类型",
    "market": "市场",
    "country": "国家",
    "capacity_total_l": "总容积",
    "washing_capacity_kg": "洗涤容量",
    "drying_capacity_kg": "烘干容量",
    "drum_volume_l": "内筒容积",
    "fridge_capacity_l": "冷藏容积",
    "freezer_capacity_l": "冷冻容积",
    "energy_rating": "能效等级",
    "energy_rating_wash": "洗涤能效",
    "energy_rating_dry": "烘干能效",
    "water_rating": "水效/冷凝效率",
    "energy_consumption": "耗电量",
    "noise_db": "噪音",
    "width_mm": "宽度",
    "depth_mm": "深度",
    "height_mm": "高度",
    "dimensions": "净尺寸",
    "net_weight_kg": "净重",
    "cooling_type": "制冷方式",
    "compressor_type": "压缩机",
    "inverter": "变频",
    "drying_mode": "烘干方式",
    "installation_type": "安装方式",
    "supply_voltage": "电压",
    "programs": "程序",
    "key_features": "功能",
    "door_type": "开门方式",
    "color": "颜色",
    "price": "价格",
    "currency": "币种",
    "certification": "认证",
}

MODEL_CARD_COLUMNS = [
    "model",
    "brand",
    "product_type",
    "series",
    "market",
    "country",
    "washing_capacity_kg",
    "drying_capacity_kg",
    "drum_volume_l",
    "drying_mode",
    "energy_rating",
    "energy_rating_wash",
    "energy_rating_dry",
    "water_rating",
    "noise_db",
    "dimensions",
    "width_mm",
    "depth_mm",
    "height_mm",
    "net_weight_kg",
    "color",
    "installation_type",
    "supply_voltage",
    "inverter",
    "programs",
    "key_features",
    "certification",
]


class FridgeAuthLoginRequest(BaseModel):
    password: str = Field(default="", max_length=256)


class FridgeSessionCreateRequest(BaseModel):
    title: str = Field(default="新会话", max_length=80)


class FridgeSessionPatchRequest(BaseModel):
    title: str | None = Field(default=None, max_length=80)
    favorite: bool | None = None


class FridgeMessageRequest(BaseModel):
    message: str = Field(min_length=1, max_length=3000)
    model: str = Field(default="", max_length=120)
    request_id: str = Field(default="", max_length=80)


class FridgeFeedbackRequest(BaseModel):
    session_id: str = Field(default="", max_length=80)
    message_id: str = Field(default="", max_length=80)
    score: str = Field(pattern="^(up|down)$")
    issue_type: str = Field(default="", max_length=80)
    note: str = Field(default="", max_length=1000)


def _now_iso() -> str:
    return dt.datetime.utcnow().isoformat(timespec="seconds") + "Z"


def _parse_iso(value: str) -> dt.datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return dt.datetime.fromisoformat(text.replace("Z", "+00:00")).replace(tzinfo=None)
    except Exception:
        return None


def _safe_slug(text: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(text or "")).strip("_")
    return slug or "fridge"


def _normalize_name(value: str) -> str:
    return re.sub(r"[^0-9a-zA-Z\u4e00-\u9fff]+", "", str(value or "").strip().lower())


def _text(value) -> str:
    if value is None:
        return ""
    try:
        if pd.isna(value):
            return ""
    except Exception:
        pass
    text = str(value).strip()
    return "" if text.lower() == "nan" else text


def _first_existing_column(df: pd.DataFrame, aliases: list[str]) -> str | None:
    columns = {_normalize_name(col): col for col in df.columns}
    for alias in aliases:
        match = columns.get(_normalize_name(alias))
        if match is not None:
            return match
    for alias in aliases:
        normalized_alias = _normalize_name(alias)
        if len(normalized_alias) < 2:
            continue
        for normalized_column, column in columns.items():
            if normalized_alias in normalized_column:
                return column
    return None


def _apply_aliases(df: pd.DataFrame, aliases: dict[str, list[str]]) -> pd.DataFrame:
    normalized = df.copy()
    for canonical, candidates in aliases.items():
        if canonical in normalized.columns:
            normalized[canonical] = normalized[canonical].map(_text)
            continue
        source = _first_existing_column(normalized, [canonical, *candidates])
        normalized[canonical] = normalized[source].map(_text) if source else ""
    for column in normalized.columns:
        normalized[column] = normalized[column].map(_text)
    return normalized.fillna("")


def _row_search_text(row: pd.Series, preferred_columns: list[str] | None = None) -> str:
    columns = preferred_columns or list(row.index)
    values = [_text(row.get(column, "")) for column in columns if _text(row.get(column, ""))]
    return " ".join(values)


HEADER_HINTS = {
    "model",
    "型号",
    "brand",
    "品牌",
    "market",
    "区域",
    "category",
    "品类",
    "feature",
    "卖点",
    "slogan",
    "标题",
    "description",
    "描述",
    "link",
    "链接",
    "price",
    "价格",
}


def _header_score(values) -> int:
    score = 0
    for value in values:
        normalized = _normalize_name(value)
        if not normalized:
            continue
        if any(_normalize_name(hint) in normalized for hint in HEADER_HINTS):
            score += 1
    return score


def _promote_embedded_header(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df
    current_score = _header_score(df.columns)
    best_index = None
    best_score = current_score
    for index in range(min(6, len(df))):
        row_values = [_text(value) for value in df.iloc[index].tolist()]
        row_score = _header_score(row_values)
        if row_score > best_score:
            best_index = index
            best_score = row_score
    if best_index is None or best_score < 3:
        return df
    header_values = [_text(value) for value in df.iloc[best_index].tolist()]
    columns = []
    seen: dict[str, int] = {}
    for fallback, value in zip(df.columns, header_values):
        column = value or str(fallback)
        if column in seen:
            seen[column] += 1
            column = f"{column}_{seen[column]}"
        else:
            seen[column] = 0
        columns.append(column)
    promoted = df.iloc[best_index + 1 :].copy()
    promoted.columns = columns
    return promoted.reset_index(drop=True)


def _read_excel_first_sheet(data: bytes) -> pd.DataFrame:
    df = pd.read_excel(io.BytesIO(data))
    return _promote_embedded_header(df)


def _read_tabular_upload(file_name: str, data: bytes) -> pd.DataFrame:
    suffix = Path(file_name).suffix.lower()
    if suffix in {".xlsx", ".xls"}:
        return _read_excel_first_sheet(data)
    if suffix == ".csv":
        for encoding in ("utf-8-sig", "utf-8", "gb18030"):
            try:
                return pd.read_csv(io.BytesIO(data), encoding=encoding)
            except UnicodeDecodeError:
                continue
        return pd.read_csv(io.BytesIO(data))
    raise ValueError("仅支持 xlsx、xls、csv、pdf 文件。")


def _is_template_value(value: str, expected: set[str]) -> bool:
    normalized = _normalize_name(value)
    return bool(normalized) and normalized in {_normalize_name(item) for item in expected}


def _drop_template_rows(df: pd.DataFrame, column: str, expected: set[str]) -> pd.DataFrame:
    if column not in df.columns or df.empty:
        return df
    mask = df[column].astype(str).apply(lambda value: _is_template_value(value, expected))
    return df[~mask].copy()


def _join_unique(values: list[str], limit: int = 900) -> str:
    parts = []
    for value in values:
        text = _text(value)
        if text and text not in parts:
            parts.append(text)
    return "；".join(parts)[:limit]


def _enrich_competitor_rows(df: pd.DataFrame) -> pd.DataFrame:
    normalized = df.copy()
    for column in ("feature_name", "slogan", "meaning", "description", "core_params"):
        if column not in normalized.columns:
            normalized[column] = ""
    normalized["core_params"] = normalized.apply(
        lambda row: _join_unique(
            [
                row.get("feature_name"),
                row.get("slogan"),
                row.get("meaning"),
                row.get("description"),
                row.get("core_params"),
            ]
        ),
        axis=1,
    )
    normalized["positive_keywords"] = normalized.apply(
        lambda row: _text(row.get("positive_keywords")) or _join_unique([row.get("meaning"), row.get("feature_name")], limit=260),
        axis=1,
    )
    return normalized


def _extract_pdf_text(data: bytes) -> tuple[str, str]:
    try:
        import pypdf

        reader = pypdf.PdfReader(io.BytesIO(data))
        pages = [(page.extract_text() or "") for page in reader.pages]
        return "\n".join(pages).strip(), ""
    except Exception as first_error:
        try:
            import PyPDF2

            reader = PyPDF2.PdfReader(io.BytesIO(data))
            pages = [(page.extract_text() or "") for page in reader.pages]
            return "\n".join(pages).strip(), ""
        except Exception as second_error:
            return "", f"{type(first_error).__name__}: {first_error}; {type(second_error).__name__}: {second_error}"


def normalize_fridge_dataset(dataset: str, df: pd.DataFrame) -> pd.DataFrame:
    if dataset == "specs":
        normalized = _apply_aliases(df, SPEC_ALIASES)
        normalized = _drop_template_rows(normalized, "model", {"型号", "model", "产品型号"})
        missing_model = normalized["model"].astype(str).str.strip().eq("")
        normalized = normalized[~missing_model].copy()
        if normalized.empty:
            raise ValueError("规格表必须至少包含一行有效的 model / 型号。")
        normalized["search_text"] = normalized.apply(lambda row: _row_search_text(row), axis=1)
        return normalized
    if dataset == "marketing":
        normalized = _apply_aliases(df, MARKETING_ALIASES)
        normalized["content"] = normalized.apply(
            lambda row: _text(row.get("content")) or _text(row.get("response")) or _text(row.get("title")),
            axis=1,
        )
        normalized = normalized[normalized["content"].astype(str).str.strip().ne("")].copy()
        if normalized.empty:
            raise ValueError("营销素材表至少需要包含标题、正文、FAQ答案或异议处理内容。")
        normalized["search_text"] = normalized.apply(lambda row: _row_search_text(row), axis=1)
        return normalized
    if dataset == "competitors":
        normalized = _apply_aliases(df, COMPETITOR_ALIASES)
        normalized = _drop_template_rows(normalized, "brand", {"品牌", "brand"})
        normalized = _drop_template_rows(normalized, "competitor_model", {"型号", "model", "型番"})
        normalized = _enrich_competitor_rows(normalized)
        has_signal = normalized.apply(
            lambda row: any(
                _text(row.get(column))
                for column in ("competitor_model", "brand", "category", "core_params", "source_url")
            ),
            axis=1,
        )
        normalized = normalized[has_signal].copy()
        if normalized.empty:
            raise ValueError("竞品表至少需要包含品牌、型号、品类、卖点描述或来源链接中的一类有效信息。")
        normalized["search_text"] = normalized.apply(lambda row: _row_search_text(row), axis=1)
        return normalized
    if dataset == "documents":
        normalized = _apply_aliases(df, DOCUMENT_ALIASES)
        normalized["content"] = normalized.apply(
            lambda row: _text(row.get("content")) or _text(row.get("summary")) or _text(row.get("title")),
            axis=1,
        )
        normalized = normalized[normalized["content"].astype(str).str.strip().ne("")].copy()
        if normalized.empty:
            raise ValueError("文档表至少需要包含标题、摘要或正文内容。")
        normalized["search_text"] = normalized.apply(lambda row: _row_search_text(row), axis=1)
        return normalized
    raise ValueError("未知数据集。")


def _numeric_value(value: str) -> float | None:
    match = re.search(r"-?\d+(?:\.\d+)?", str(value or ""))
    return float(match.group(0)) if match else None


def _mentioned_models(question: str, available_models: list[str], selected_model: str = "") -> list[str]:
    question_text = question.lower()
    matches = []
    if selected_model:
        matches.append(selected_model)
    for model in available_models:
        model_text = str(model or "").strip()
        if model_text and model_text.lower() in question_text and model_text not in matches:
            matches.append(model_text)
    return matches


def _mentioned_series(question: str, available_series: list[str]) -> list[str]:
    question_text = question.lower()
    matches = []
    for series in available_series:
        series_text = str(series or "").strip()
        if len(series_text) < 2:
            continue
        series_lower = series_text.lower()
        if series_lower in question_text or f"{series_lower}系列" in question_text:
            matches.append(series_text)
    return matches


def _is_product_list_question(question: str) -> bool:
    question_text = str(question or "").lower()
    list_phrases = [
        "哪些产品",
        "有哪些产品",
        "都有哪些产品",
        "产品有哪些",
        "哪些型号",
        "有哪些型号",
        "都有哪些型号",
        "型号有哪些",
        "产品列表",
        "型号列表",
        "产品清单",
        "型号清单",
        "都有什么型号",
        "有什么型号",
        "有几款",
        "多少款",
        "几个型号",
        "几款产品",
        "all products",
        "which products",
        "what products",
        "model list",
    ]
    return any(phrase in question_text for phrase in list_phrases)


def _is_product_card_question(question: str) -> bool:
    question_text = str(question or "").lower()
    card_phrases = [
        "产品卡片",
        "型号卡片",
        "规格卡片",
        "详细参数",
        "规格参数",
        "完整参数",
        "参数表",
        "产品信息",
        "型号信息",
        "product card",
        "spec sheet",
    ]
    return any(phrase in question_text for phrase in card_phrases)


def _is_field_lookup_question(question: str) -> bool:
    question_text = str(question or "").lower()
    field_phrases = [
        "物料",
        "ean",
        "条码",
        "销售型号",
        "生产版本",
        "认证",
        "承诺",
        "过度",
        "能效",
        "噪音",
        "标称",
        "上市",
        "规划",
        "ir",
        "销量",
        "生产基地",
        "状态",
    ]
    return any(phrase in question_text for phrase in field_phrases)


def _is_marketing_schema_question(question: str) -> bool:
    question_text = str(question or "").lower()
    schema_phrases = [
        "营销字段",
        "字段规范",
        "字段定义",
        "数据库组织",
        "组织逻辑",
        "关联逻辑",
        "上游依赖",
        "下游关联",
        "行级数据",
        "父记录",
        "多行数据",
        "usp135",
        "金字塔",
    ]
    return any(phrase in question_text for phrase in schema_phrases)


def _question_intent(question: str) -> str:
    if _is_marketing_schema_question(question):
        return "marketing_schema"
    if _is_product_list_question(question):
        return "product_list"
    if _is_product_card_question(question):
        return "product_card"
    question_text = str(question or "").lower()
    if "对比" in question_text or "compare" in question_text:
        return "compare"
    if _is_field_lookup_question(question):
        return "field_lookup"
    return "general"


def _should_scope_to_selected_model(question: str, selected_model: str, explicit_models: list[str], requested_series: list[str]) -> bool:
    if not selected_model:
        return False
    if explicit_models or requested_series:
        return False
    if _is_product_list_question(question):
        return False
    return True


def _filter_by_series(df: pd.DataFrame, requested_series: list[str]) -> pd.DataFrame:
    if df.empty or not requested_series or "series" not in df.columns:
        return pd.DataFrame()
    return df[df["series"].astype(str).isin(requested_series)].copy()


def _keywords(question: str) -> list[str]:
    words = re.findall(r"[A-Za-z0-9][A-Za-z0-9_.-]{1,}|\d+(?:\.\d+)?|[\u4e00-\u9fff]{2,}", question)
    stop = {"请问", "一下", "这个", "产品", "冰箱", "海信", "生成", "帮我", "哪些", "如何", "什么", "怎么", "系列"}
    return [word.lower() for word in words if word.lower() not in stop]


def _contains_any(text: str, keywords: list[str]) -> bool:
    haystack = str(text or "").lower()
    return any(keyword and keyword in haystack for keyword in keywords)


def _apply_spec_filters(question: str, specs: pd.DataFrame) -> pd.DataFrame:
    if specs.empty:
        return specs
    filtered = specs.copy()
    question_text = question.lower()
    capacity_match = re.search(r"(\d+(?:\.\d+)?)\s*(l|升|litre|liter)", question_text, re.I)
    if capacity_match and "capacity_total_l" in filtered.columns:
        target = float(capacity_match.group(1))
        values = filtered["capacity_total_l"].map(_numeric_value)
        capacity_rows = filtered[values.map(lambda value: value is not None and abs(value - target) <= 20)]
        if not capacity_rows.empty:
            filtered = capacity_rows
    noise_match = re.search(r"(\d+(?:\.\d+)?)\s*(db|分贝)", question_text, re.I)
    if noise_match and "noise_db" in filtered.columns:
        target = float(noise_match.group(1))
        values = filtered["noise_db"].map(_numeric_value)
        if any(word in question_text for word in ["低于", "小于", "以内", "不超过", "<", "≤"]):
            noise_rows = filtered[values.map(lambda value: value is not None and value <= target)]
        else:
            noise_rows = filtered[values.map(lambda value: value is not None and abs(value - target) <= 3)]
        if not noise_rows.empty:
            filtered = noise_rows
    if "能效" in question_text and "energy_rating" in filtered.columns:
        rating_tokens = re.findall(r"\b[a-g][+]*\b|[一二三四五]级|一级|二级|三级|四级|五级", question_text, re.I)
        if rating_tokens:
            rating_rows = filtered[
                filtered["energy_rating"].astype(str).str.lower().apply(lambda value: any(token.lower() in value for token in rating_tokens))
            ]
            if not rating_rows.empty:
                filtered = rating_rows
    return filtered


class FridgeKnowledgeStore:
    def __init__(self, storage):
        self.storage = storage

    def dataframe_key(self, dataset: str) -> str:
        return f"{FRIDGE_DATA_PREFIX}/{dataset}.pkl"

    def meta_key(self) -> str:
        return f"{FRIDGE_DATA_PREFIX}/dataset_meta.json"

    def sessions_key(self) -> str:
        return f"{FRIDGE_DATA_PREFIX}/sessions.json"

    def feedback_key(self) -> str:
        return f"{FRIDGE_DATA_PREFIX}/feedback.json"

    def load_dataset(self, dataset: str) -> pd.DataFrame:
        if dataset not in FRIDGE_DATASETS:
            raise ValueError("未知数据集。")
        return self.storage.read_dataframe(self.dataframe_key(dataset))

    def save_dataset(self, dataset: str, file_name: str, data: bytes) -> dict:
        if dataset not in FRIDGE_DATASETS:
            raise ValueError("未知数据集。")
        if len(data) > FRIDGE_MAX_UPLOAD_BYTES:
            raise ValueError(f"文件过大，最大允许 {FRIDGE_MAX_UPLOAD_BYTES // (1024 * 1024)}MB。")
        suffix = Path(file_name).suffix.lower()
        upload_key = f"{FRIDGE_DATA_PREFIX}/uploads/{dataset}/{dt.datetime.utcnow().strftime('%Y%m%dT%H%M%SZ')}_{_safe_slug(file_name)}"
        stored_uri = self.storage.write_file_bytes(upload_key, data, content_type="application/octet-stream")
        if dataset == "documents" and suffix == ".pdf":
            text, error = _extract_pdf_text(data)
            row = {
                "model": "",
                "series": "",
                "title": Path(file_name).stem,
                "summary": text[:1200],
                "content": text[:12000],
                "source_url": "",
                "file_name": file_name,
                "storage_uri": stored_uri,
                "extract_error": error,
                "search_text": f"{Path(file_name).stem} {text[:12000]}",
            }
            existing = self.load_dataset("documents")
            normalized = pd.concat([existing, pd.DataFrame([row])], ignore_index=True).fillna("")
        else:
            df = _read_tabular_upload(file_name, data)
            normalized = normalize_fridge_dataset(dataset, df)
            if dataset == "documents":
                normalized["file_name"] = file_name
                normalized["storage_uri"] = stored_uri
        self.storage.write_dataframe(self.dataframe_key(dataset), normalized)
        meta = self.load_meta()
        meta[dataset] = {
            "file_name": file_name,
            "storage_uri": stored_uri,
            "row_count": int(len(normalized)),
            "updated_at": _now_iso(),
        }
        self.storage.write_json(self.meta_key(), meta)
        return meta[dataset]

    def load_meta(self) -> dict:
        return self.storage.read_json(self.meta_key(), {})

    def summary(self) -> dict:
        meta = self.load_meta()
        datasets = {}
        for dataset in sorted(FRIDGE_DATASETS):
            df = self.load_dataset(dataset)
            datasets[dataset] = {
                "loaded": not df.empty,
                "row_count": int(len(df)),
                "meta": meta.get(dataset, {}),
            }
        specs = self.load_dataset("specs")
        models = sorted(specs["model"].dropna().astype(str).unique().tolist()) if not specs.empty and "model" in specs else []
        series = sorted(specs["series"].dropna().astype(str).unique().tolist()) if not specs.empty and "series" in specs else []
        return {
            "datasets": datasets,
            "model_count": len(models),
            "series_count": len([item for item in series if item]),
            "models": models,
            "series": [item for item in series if item],
        }

    def options(self) -> dict:
        summary = self.summary()
        specs = self.load_dataset("specs")
        model_cards = []
        if not specs.empty:
            for _, row in specs.head(300).iterrows():
                fields = [
                    {"key": column, "label": SPEC_LABELS.get(column, column), "value": _text(row.get(column))}
                    for column in MODEL_CARD_COLUMNS
                    if _text(row.get(column))
                ]
                model_cards.append(
                    {
                        "model": _text(row.get("model")),
                        "brand": _text(row.get("brand")),
                        "series": _text(row.get("series")),
                        "product_type": _text(row.get("product_type")),
                        "market": _text(row.get("market")),
                        "capacity_total_l": _text(row.get("capacity_total_l")),
                        "washing_capacity_kg": _text(row.get("washing_capacity_kg")),
                        "drying_capacity_kg": _text(row.get("drying_capacity_kg")),
                        "energy_rating": _text(row.get("energy_rating")),
                        "energy_rating_wash": _text(row.get("energy_rating_wash")),
                        "energy_rating_dry": _text(row.get("energy_rating_dry")),
                        "water_rating": _text(row.get("water_rating")),
                        "noise_db": _text(row.get("noise_db")),
                        "fields": fields,
                    }
                )
        return {**summary, "model_cards": model_cards}

    def load_sessions(self) -> list[dict]:
        sessions = self.storage.read_json(self.sessions_key(), [])
        return sessions if isinstance(sessions, list) else []

    def save_sessions(self, sessions: list[dict]) -> None:
        sessions = sorted(sessions, key=lambda item: item.get("updated_at", ""), reverse=True)[:FRIDGE_SESSION_LIMIT]
        self.storage.write_json(self.sessions_key(), sessions)

    def create_session(self, title: str = "新会话") -> dict:
        session = {
            "id": uuid.uuid4().hex[:12],
            "title": title.strip() or "新会话",
            "favorite": False,
            "created_at": _now_iso(),
            "updated_at": _now_iso(),
            "messages": [],
        }
        sessions = self.load_sessions()
        sessions.insert(0, session)
        self.save_sessions(sessions)
        return session

    def update_session(self, session_id: str, title: str | None = None, favorite: bool | None = None) -> dict:
        sessions = self.load_sessions()
        for session in sessions:
            if session.get("id") == session_id:
                if title is not None:
                    session["title"] = title.strip() or session.get("title") or "新会话"
                if favorite is not None:
                    session["favorite"] = bool(favorite)
                session["updated_at"] = _now_iso()
                self.save_sessions(sessions)
                return session
        raise KeyError("会话不存在。")

    def delete_session(self, session_id: str) -> None:
        sessions = [session for session in self.load_sessions() if session.get("id") != session_id]
        self.save_sessions(sessions)

    def append_feedback(self, payload: dict) -> dict:
        feedback = self.storage.read_json(self.feedback_key(), [])
        if not isinstance(feedback, list):
            feedback = []
        item = {"id": uuid.uuid4().hex[:12], "created_at": _now_iso(), **payload}
        feedback.insert(0, item)
        self.storage.write_json(self.feedback_key(), feedback[:1000])
        return item

    def find_duplicate_message(self, session: dict, question: str, request_id: str = "", selected_model: str = "") -> dict | None:
        messages = list(session.get("messages") or [])
        if not messages:
            return None
        if request_id:
            for index, message in enumerate(messages):
                if message.get("role") == "user" and message.get("request_id") == request_id:
                    for candidate in messages[index + 1 :]:
                        if candidate.get("role") == "assistant" and candidate.get("content"):
                            return candidate
        normalized_question = _text(question)
        if len(messages) >= 2:
            user_message = messages[-2]
            assistant_message = messages[-1]
            created_at = _parse_iso(assistant_message.get("created_at", "")) or _parse_iso(user_message.get("created_at", ""))
            is_recent = bool(created_at and (dt.datetime.utcnow() - created_at).total_seconds() <= FRIDGE_DUPLICATE_WINDOW_SECONDS)
            if (
                is_recent
                and user_message.get("role") == "user"
                and assistant_message.get("role") == "assistant"
                and _text(user_message.get("content")) == normalized_question
                and _text(user_message.get("model")) == _text(selected_model)
                and assistant_message.get("content")
            ):
                return assistant_message
        return None

    def build_evidence(self, question: str, selected_model: str = "") -> dict:
        specs = self.load_dataset("specs")
        marketing = self.load_dataset("marketing")
        competitors = self.load_dataset("competitors")
        documents = self.load_dataset("documents")
        available_models = sorted(
            {
                _text(model)
                for frame in (specs, marketing, documents)
                if not frame.empty and "model" in frame
                for model in frame["model"].dropna().astype(str).unique().tolist()
                if _text(model)
            }
        )
        available_series = sorted(
            {
                _text(series)
                for frame in (specs, marketing, documents)
                if not frame.empty and "series" in frame
                for series in frame["series"].dropna().astype(str).unique().tolist()
                if _text(series)
            }
        )
        explicit_models = _mentioned_models(question, available_models, "")
        requested_series = [] if explicit_models else _mentioned_series(question, available_series)
        selected_model_applied = _should_scope_to_selected_model(question, selected_model, explicit_models, requested_series)
        requested_models = _mentioned_models(question, available_models, selected_model if selected_model_applied else "")
        intent = _question_intent(question)
        words = _keywords(question)

        spec_hits = pd.DataFrame()
        if not specs.empty and intent != "marketing_schema":
            if requested_series:
                spec_hits = _filter_by_series(specs, requested_series)
            elif intent == "product_list":
                spec_hits = specs.copy()
                if words and "search_text" in specs:
                    keyword_hits = specs[specs["search_text"].astype(str).str.lower().apply(lambda value: _contains_any(value, words))]
                    if not keyword_hits.empty:
                        spec_hits = keyword_hits
            elif requested_models:
                spec_hits = specs[specs["model"].astype(str).isin(requested_models)].copy()
            else:
                spec_hits = _apply_spec_filters(question, specs)
                if words and len(spec_hits) == len(specs):
                    text_hits = specs[specs["search_text"].astype(str).str.lower().apply(lambda value: _contains_any(value, words))]
                    if not text_hits.empty:
                        spec_hits = text_hits
            spec_hits = spec_hits.head(30 if intent == "product_list" else 8)

        marketing_hits = pd.DataFrame()
        if not marketing.empty:
            mask = pd.Series([False] * len(marketing), index=marketing.index)
            if requested_models and "model" in marketing:
                mask = mask | marketing["model"].astype(str).isin(requested_models)
            if requested_series and "series" in marketing:
                mask = mask | marketing["series"].astype(str).isin(requested_series)
            if words and "search_text" in marketing:
                mask = mask | marketing["search_text"].astype(str).str.lower().apply(lambda value: _contains_any(value, words))
            scope_mask = marketing.get("scope", pd.Series([""] * len(marketing), index=marketing.index)).astype(str).str.lower().isin(["global", "general", "全局", "通用"])
            direct_hits = marketing[mask]
            general_hits = marketing[scope_mask & ~mask]
            marketing_hits = pd.concat([direct_hits, general_hits], ignore_index=True).head(10)

        competitor_hits = pd.DataFrame()
        if not competitors.empty and intent != "marketing_schema":
            if words and "search_text" in competitors:
                competitor_hits = competitors[
                    competitors["search_text"].astype(str).str.lower().apply(lambda value: _contains_any(value, words))
                ].head(8)
            else:
                competitor_hits = competitors.head(5)

        document_hits = pd.DataFrame()
        if not documents.empty:
            if requested_models and "model" in documents:
                document_hits = documents[documents["model"].astype(str).isin(requested_models)]
            if requested_series and "series" in documents:
                document_hits = pd.concat([document_hits, documents[documents["series"].astype(str).isin(requested_series)]], ignore_index=True)
            if words and "search_text" in documents:
                keyword_hits = documents[documents["search_text"].astype(str).str.lower().apply(lambda value: _contains_any(value, words))]
                if requested_models and "model" in keyword_hits:
                    keyword_hits = keyword_hits[keyword_hits["model"].astype(str).isin(requested_models)]
                elif requested_series and "series" in keyword_hits:
                    keyword_hits = keyword_hits[keyword_hits["series"].astype(str).isin(requested_series)]
                if intent == "marketing_schema" and "data_source" in keyword_hits:
                    keyword_hits = keyword_hits.assign(
                        _schema_rank=keyword_hits["data_source"].astype(str).str.contains("marketing_schema", case=False, na=False).map(lambda value: 0 if value else 1)
                    ).sort_values("_schema_rank").drop(columns=["_schema_rank"])
                document_hits = pd.concat([document_hits, keyword_hits], ignore_index=True).drop_duplicates().head(6)
            elif document_hits.empty:
                document_hits = documents.head(3)

        return {
            "intent": intent,
            "requested_models": requested_models,
            "explicit_models": explicit_models,
            "requested_series": requested_series,
            "selected_model": selected_model if selected_model else "",
            "selected_model_applied": selected_model_applied,
            "specs": spec_hits.fillna("").to_dict(orient="records"),
            "marketing": marketing_hits.fillna("").to_dict(orient="records"),
            "competitors": competitor_hits.fillna("").to_dict(orient="records"),
            "documents": document_hits.fillna("").to_dict(orient="records"),
            "available_models": available_models[:200],
            "available_series": [item for item in available_series if item][:200],
        }


def _format_spec_row(row: dict) -> str:
    parts = []
    for column in KEY_SPEC_COLUMNS:
        value = _text(row.get(column))
        if value:
            parts.append(f"{SPEC_LABELS.get(column, column)}: {value}")
    return "；".join(parts)


def _format_competitor_row(row: dict) -> str:
    fields = [
        ("brand", "品牌"),
        ("competitor_model", "型号"),
        ("category", "品类"),
        ("market", "市场/渠道"),
        ("price", "价格"),
        ("capacity_total_l", "容量"),
        ("energy_rating", "能效"),
        ("water_rating", "水效"),
        ("noise_db", "噪音"),
        ("feature_name", "卖点名"),
        ("slogan", "标题"),
        ("meaning", "传播点"),
        ("description", "描述"),
        ("tm_certified", "TM认证"),
        ("source_url", "链接"),
    ]
    parts = []
    for column, label in fields:
        value = _text(row.get(column))
        if value:
            parts.append(f"{label}: {value[:700]}")
    core_params = _text(row.get("core_params"))
    if core_params and not any("卖点名:" in item or "描述:" in item for item in parts):
        parts.append(f"核心信息: {core_params[:900]}")
    return "；".join(parts)


def _evidence_to_text(evidence: dict) -> str:
    blocks = []
    if evidence.get("specs"):
        lines = [f"[规格{i}] {_format_spec_row(row)}" for i, row in enumerate(evidence["specs"], start=1)]
        blocks.append("规格数据：\n" + "\n".join(lines))
    if evidence.get("marketing"):
        lines = []
        for i, row in enumerate(evidence["marketing"], start=1):
            title = _text(row.get("title")) or _text(row.get("content_type")) or "营销素材"
            content = _text(row.get("content")) or _text(row.get("response"))
            lines.append(f"[营销{i}] {title}: {content[:700]}")
        blocks.append("营销素材：\n" + "\n".join(lines))
    if evidence.get("competitors"):
        lines = []
        for i, row in enumerate(evidence["competitors"], start=1):
            lines.append(f"[竞品{i}] {_format_competitor_row(row)}")
        blocks.append("竞品数据：\n" + "\n".join(lines))
    if evidence.get("documents"):
        lines = []
        for i, row in enumerate(evidence["documents"], start=1):
            title = _text(row.get("title")) or _text(row.get("file_name")) or "文档"
            content = _text(row.get("summary")) or _text(row.get("content"))
            lines.append(f"[文档{i}] {title}: {content[:800]}")
        blocks.append("文档摘要：\n" + "\n".join(lines))
    return "\n\n".join(blocks)


def _markdown_table(headers: list[str], rows: list[list[str]]) -> str:
    header_line = "| " + " | ".join(headers) + " |"
    sep_line = "| " + " | ".join([":---"] * len(headers)) + " |"
    body = ["| " + " | ".join(_text(cell).replace("|", "/") for cell in row) + " |" for row in rows]
    return "\n".join([header_line, sep_line, *body])


def _model_list_answer(evidence: dict) -> str:
    specs = evidence.get("specs") or []
    if not specs:
        return ""
    requested_series = [item for item in evidence.get("requested_series", []) if item]
    scope = f"{'、'.join(requested_series)} 系列" if requested_series else "当前知识库"
    rows = []
    seen = set()
    for row in specs:
        model = _text(row.get("model"))
        if not model or model in seen:
            continue
        seen.add(model)
        capacity = _join_unique(
            [
                row.get("capacity_total_l"),
                row.get("washing_capacity_kg"),
                row.get("drying_capacity_kg"),
                row.get("drum_volume_l"),
            ],
            limit=120,
        )
        status = _join_unique([row.get("launch_status"), row.get("planning_launch_date")], limit=120)
        rows.append(
            [
                model,
                _text(row.get("series")),
                _text(row.get("product_type")),
                _join_unique([row.get("country"), row.get("market")], limit=120),
                capacity,
                _text(row.get("energy_rating")) or _text(row.get("energy_rating_wash")) or _text(row.get("energy_rating_dry")),
                status,
            ]
        )
    if not rows:
        return ""
    intro = f"根据当前已入库规格/规划数据，{scope}共找到 {len(rows)} 个产品型号："
    return intro + "\n\n" + _markdown_table(["型号", "系列", "产品类型", "国家/市场", "容量信息", "能效", "状态/上市时间"], rows)


def _spec_context_answer(question: str, evidence: dict) -> str:
    specs = evidence.get("specs") or []
    if not specs:
        return ""
    question_text = str(question or "").lower()
    row = specs[0]
    fields: list[tuple[str, str]] = []

    def add(label: str, *values) -> None:
        value = _join_unique(values, limit=900)
        if value and (label, value) not in fields:
            fields.append((label, value))

    add("型号", row.get("model"))
    add("系列", row.get("series"))
    add("产品类型", row.get("product_type"))
    if any(word in question_text for word in ["物料", "ean", "条码", "销售型号", "生产版本"]):
        add("生产版本", row.get("production_version"))
        add("物料号", row.get("material_codes"))
        add("销售型号", row.get("sales_models"))
        add("EAN", row.get("eans"))
    if any(word in question_text for word in ["认证", "承诺", "过度", "能效", "噪音", "标称"]):
        add("认证", row.get("certification"))
        add("能效", row.get("energy_rating"), row.get("energy_rating_wash"), row.get("energy_rating_dry"))
        add("噪音", row.get("noise_db"))
        add("水效/冷凝效率", row.get("water_rating"))
    if any(word in question_text for word in ["上市", "规划", "ir", "销量", "生产基地", "状态"]):
        add("型号状态", row.get("launch_status"))
        add("规划上市时间", row.get("planning_launch_date"))
        add("产品 IR 完成时间", row.get("product_ir_finish_date"))
        add("规划销量", row.get("first_year_plan_volume"), row.get("second_year_plan_volume"), row.get("third_year_plan_volume"))
        add("生产基地", row.get("planned_factory"), row.get("product_list_factory_name"))
    if any(word in question_text for word in ["卖点", "优势", "场景", "介绍", "话术", "功能"]):
        add("关键功能/背景", row.get("key_features"))
        add("容量", row.get("capacity_total_l"), row.get("washing_capacity_kg"), row.get("drying_capacity_kg"), row.get("drum_volume_l"))
        add("颜色", row.get("color"))
    if len(fields) <= 3:
        add("容量", row.get("capacity_total_l"), row.get("washing_capacity_kg"), row.get("drying_capacity_kg"), row.get("drum_volume_l"))
        add("能效", row.get("energy_rating"), row.get("energy_rating_wash"), row.get("energy_rating_dry"))
        add("噪音", row.get("noise_db"))
        add("认证", row.get("certification"))
        add("关键功能/背景", row.get("key_features"))

    rows = [[label, value] for label, value in fields if value]
    if not rows:
        return ""
    answer = "以下是与问题最相关的已入库信息：\n\n" + _markdown_table(["字段", "信息"], rows[:12])
    documents = evidence.get("documents") or []
    snippets = []
    for item in documents[:2]:
        title = _text(item.get("title")) or _text(item.get("file_name")) or "文档"
        content = _text(item.get("summary")) or _text(item.get("content"))
        if content:
            snippets.append(f"- **{title}**：{content[:320]}")
    if snippets:
        answer += "\n\n### 相关文档摘要\n" + "\n".join(snippets)
    return answer


MARKETING_RELATION_FIELDS = [
    "核心定位语",
    "用户洞察结论",
    "核心人群画像",
    "核心使用场景",
    "核心购买动机",
    "场景痛点映射",
    "核心卖点USP1_卖点定义",
    "核心卖点USP1_卖点利益点",
    "支撑卖点USP3_卖点定义",
    "支撑卖点USP3_卖点利益点",
    "辅助卖点USP5_卖点定义",
    "辅助卖点USP5_卖点利益点",
    "金字塔卖点分层Tier1_卖点定义",
    "金字塔卖点分层Tier1_卖点利益点",
    "金字塔卖点体系Tier2_卖点定义",
    "金字塔卖点体系Tier2_卖点利益点",
    "金字塔卖点体系Tier3_卖点定义",
    "金字塔卖点体系Tier3_卖点利益点",
    "其他补充卖点_定义",
    "其他补充卖点_利益点",
]


def _marketing_content_fields(row: dict) -> dict[str, list[str]]:
    values: dict[str, list[str]] = {}
    content = _text(row.get("content")) or _text(row.get("response"))
    for line in content.splitlines():
        if ":" not in line and "：" not in line:
            continue
        label, value = re.split(r"[:：]", line, maxsplit=1)
        label = _text(label)
        value = _text(value)
        if not label or not value or value in {"未使用USP135体系", "见下列细分", "见下例细分"}:
            continue
        values.setdefault(label, [])
        if value not in values[label]:
            values[label].append(value)
    for field in MARKETING_RELATION_FIELDS:
        value = _text(row.get(field))
        if value and value not in {"未使用USP135体系", "见下列细分", "见下例细分"}:
            values.setdefault(field, [])
            if value not in values[field]:
                values[field].append(value)
    return values


def _marketing_context_answer(question: str, evidence: dict) -> str:
    marketing = evidence.get("marketing") or []
    if not marketing:
        return ""
    question_text = str(question or "").lower()
    field_groups = [
        ("定位", ["核心定位语"]),
        ("用户与场景", ["用户洞察结论", "核心人群画像", "核心使用场景", "核心购买动机", "场景痛点映射"]),
        ("USP135卖点体系", ["核心卖点USP1_卖点定义", "核心卖点USP1_卖点利益点", "支撑卖点USP3_卖点定义", "支撑卖点USP3_卖点利益点", "辅助卖点USP5_卖点定义", "辅助卖点USP5_卖点利益点"]),
        ("金字塔卖点体系", ["金字塔卖点分层Tier1_卖点定义", "金字塔卖点分层Tier1_卖点利益点", "金字塔卖点体系Tier2_卖点定义", "金字塔卖点体系Tier2_卖点利益点", "金字塔卖点体系Tier3_卖点定义", "金字塔卖点体系Tier3_卖点利益点"]),
        ("补充卖点", ["其他补充卖点_定义", "其他补充卖点_利益点"]),
    ]
    requested_fields = []
    for _, fields in field_groups:
        for field in fields:
            short = field.split("_")[0]
            if field.lower() in question_text or short.lower() in question_text:
                requested_fields.append(field)
    if not requested_fields:
        if any(token in question_text for token in ["场景", "人群", "用户", "动机", "痛点"]):
            requested_fields.extend(field_groups[1][1])
        if any(token in question_text for token in ["卖点", "usp", "tier", "话术", "优势"]):
            requested_fields.extend(field_groups[2][1] + field_groups[3][1] + field_groups[4][1])
        if any(token in question_text for token in ["定位", "角色"]):
            requested_fields.append("核心定位语")
    requested_fields = list(dict.fromkeys(requested_fields))

    by_model: dict[str, dict[str, list[str]]] = {}
    for row in marketing:
        model = _text(row.get("model")) or "通用"
        if model.upper() == "GENERAL":
            model = "通用"
        by_model.setdefault(model, {})
        for field, values in _marketing_content_fields(row).items():
            by_model[model].setdefault(field, [])
            for value in values:
                if value not in by_model[model][field]:
                    by_model[model][field].append(value)

    blocks = []
    for model, fields in by_model.items():
        display_fields = requested_fields or [field for _, group in field_groups for field in group]
        rows = []
        for field in display_fields:
            values = fields.get(field) or []
            if values:
                rows.append([field, "<br>".join(values[:3])])
        if not rows:
            continue
        blocks.append(f"### {model} 营销画像\n\n" + _markdown_table(["字段", "内容"], rows[:14]))
    if not blocks:
        return ""
    guidance = "已按 V0.2 规则聚合同一型号的多行营销数据：先看定位/用户场景，再看卖点体系；USP135 与金字塔体系互斥读取，通用 FAQ 仅作为补充。"
    return guidance + "\n\n" + "\n\n".join(blocks)


def _document_context_answer(evidence: dict) -> str:
    documents = evidence.get("documents") or []
    if not documents:
        return ""
    lines = ["以下依据已入库的营销字段规范 V0.2："]
    for item in documents[:5]:
        title = _text(item.get("title")) or _text(item.get("file_name")) or "文档"
        content = _text(item.get("summary")) or _text(item.get("content"))
        if content:
            lines.append(f"- **{title}**：{content[:700]}")
    return "\n".join(lines)


def _fallback_answer(question: str, evidence: dict) -> str:
    if not any(evidence.get(key) for key in ("specs", "marketing", "competitors", "documents")):
        models = "、".join(evidence.get("available_models", [])[:12])
        suffix = f"\n\n当前已入库型号：{models}" if models else ""
        return "当前知识库未覆盖这个问题所需的数据，请先在右侧上传规格、营销素材、竞品或文档后再查询。" + suffix

    question_text = question.lower()
    specs = evidence.get("specs") or []
    intent = evidence.get("intent") or _question_intent(question)
    if intent == "marketing_schema":
        document_answer = _document_context_answer(evidence)
        if document_answer:
            return document_answer
    if evidence.get("marketing"):
        marketing_answer = _marketing_context_answer(question, evidence)
        if marketing_answer and (not specs or any(token in question_text for token in ["营销", "卖点", "usp", "tier", "场景", "人群", "动机", "痛点", "定位", "话术"])):
            return marketing_answer
    if intent == "product_list" and specs:
        answer = _model_list_answer(evidence)
        if answer:
            return answer

    if ("对比" in question_text or "compare" in question_text) and len(specs) >= 2:
        headers = ["字段"] + [_text(row.get("model")) or f"型号{i}" for i, row in enumerate(specs, start=1)]
        rows = []
        for column in KEY_SPEC_COLUMNS[1:]:
            values = [_text(row.get(column)) for row in specs]
            if any(values):
                rows.append([SPEC_LABELS.get(column, column), *values])
        return "以下对比仅基于当前已上传规格表：\n\n" + _markdown_table(headers, rows[:16])

    if specs and intent == "product_card":
        row = specs[0]
        detail_rows = [[SPEC_LABELS.get(column, column), _text(row.get(column))] for column in KEY_SPEC_COLUMNS if _text(row.get(column))]
        answer = f"### {_text(row.get('model')) or '产品型号'} 产品卡片\n\n" + _markdown_table(["字段", "信息"], detail_rows)
        marketing = evidence.get("marketing") or []
        if marketing:
            points = []
            for item in marketing[:4]:
                title = _text(item.get("title")) or _text(item.get("content_type")) or "卖点"
                content = _text(item.get("content")) or _text(item.get("response"))
                points.append(f"- **{title}**：{content}")
            answer += "\n\n### 可用卖点/FAQ\n" + "\n".join(points)
        competitors = evidence.get("competitors") or []
        if competitors:
            points = []
            for item in competitors[:5]:
                title = " ".join([_text(item.get("brand")), _text(item.get("feature_name")) or _text(item.get("competitor_model"))]).strip()
                points.append(f"- **{title or '竞品资料'}**：{_format_competitor_row(item)}")
            answer += "\n\n### 竞品卖点参考\n" + "\n".join(points)
        return answer

    if specs:
        answer = _spec_context_answer(question, evidence)
        if answer:
            return answer

    lines = ["以下内容仅基于当前已上传知识库："]
    for item in evidence.get("marketing", [])[:6]:
        title = _text(item.get("title")) or _text(item.get("content_type")) or "营销素材"
        content = _text(item.get("content")) or _text(item.get("response"))
        lines.append(f"- **{title}**：{content}")
    for item in evidence.get("documents", [])[:3]:
        title = _text(item.get("title")) or _text(item.get("file_name")) or "文档"
        content = _text(item.get("summary")) or _text(item.get("content"))
        lines.append(f"- **{title}**：{content[:400]}")
    for item in evidence.get("competitors", [])[:3]:
        title = " ".join([_text(item.get("brand")), _text(item.get("competitor_model")) or _text(item.get("feature_name"))]).strip()
        lines.append(f"- **竞品 {title or '资料'}**：{_format_competitor_row(item)}")
    return "\n".join(lines)


def _call_fridge_bedrock(question: str, history: list[dict], evidence: dict) -> str:
    evidence_text = _evidence_to_text(evidence)
    if not evidence_text.strip():
        return ""
    recent_history = "\n".join(
        f"{item.get('role', '')}: {_text(item.get('content'))[:500]}" for item in history[-8:] if item.get("content")
    )
    system_prompt = """
你是“海信产品知识 AI 助手”，服务内部产品、营销、销售和渠道团队。
回答必须严格基于提供的证据，不得编造参数、认证、价格、竞品表现或功能。
如果证据不足，请明确说“当前知识库未覆盖”，并说明需要补充哪类数据。
默认用中文回答。需要对比时优先输出 Markdown 表格；需要营销材料时输出可直接给渠道使用的话术。
不要在用户未明确要求“产品卡片/规格卡片/参数表”时输出完整产品卡片。
如果用户询问“有哪些产品/哪些型号/产品清单/型号列表”，请直接输出产品型号清单，并优先按用户提到的系列或关键词筛选。
""".strip()
    user_prompt = f"""
用户问题：
{question}

最近上下文：
{recent_history or "无"}

可用证据：
{evidence_text}

请给出专业、简洁、可执行的回答，并在涉及参数时保持与证据一致。
""".strip()
    client = boto3.client("bedrock-runtime", region_name=FRIDGE_BEDROCK_REGION)
    response = client.converse(
        modelId=FRIDGE_BEDROCK_MODEL_ID,
        system=[{"text": system_prompt}],
        messages=[{"role": "user", "content": [{"text": user_prompt}]}],
        inferenceConfig={"maxTokens": FRIDGE_BEDROCK_MAX_TOKENS, "temperature": 0.2, "topP": 0.8},
    )
    return response["output"]["message"]["content"][0]["text"]


def answer_fridge_question(question: str, history: list[dict], evidence: dict) -> str:
    if evidence.get("intent") in {"product_list", "field_lookup"}:
        return _fallback_answer(question, evidence)
    try:
        answer = _call_fridge_bedrock(question, history, evidence)
        if answer.strip():
            return answer.strip()
    except Exception:
        pass
    return _fallback_answer(question, evidence)


def register_fridge_routes(
    app,
    storage,
    current_access_password: Callable[[], str],
    clean_access_token: Callable[[str], str],
    access_control_active: Callable[[], bool],
) -> None:
    store = FridgeKnowledgeStore(storage)
    route_lock = threading.Lock()
    static_dir = os.path.join(os.path.dirname(__file__), "web_frontend", "fridge")
    if os.path.isdir(static_dir):
        app.mount("/fridge/static", StaticFiles(directory=static_dir), name="fridge_static")

    def canonical_redirect_url(request: Request) -> str:
        if not FRIDGE_CANONICAL_HOST:
            return ""
        host = str(request.headers.get("host") or request.url.hostname or "").split(":")[0].lower()
        if not host or host == FRIDGE_CANONICAL_HOST or not host.endswith(".elb.amazonaws.com"):
            return ""
        query = f"?{request.url.query}" if request.url.query else ""
        return f"{FRIDGE_CANONICAL_SCHEME}://{FRIDGE_CANONICAL_HOST}{request.url.path}{query}"

    def admin_password() -> str:
        return os.getenv("FRIDGE_ADMIN_PASSWORD", "").strip() or current_access_password()

    def user_password() -> str:
        return os.getenv("FRIDGE_USER_PASSWORD", "").strip()

    def auth_enabled() -> bool:
        return bool(admin_password() or user_password() or access_control_active())

    def role_for_token(raw_token: str) -> str:
        token = clean_access_token(raw_token)
        admin = admin_password()
        user = user_password()
        if admin and hmac.compare_digest(token.encode("utf-8"), admin.encode("utf-8")):
            return "admin"
        if user and hmac.compare_digest(token.encode("utf-8"), user.encode("utf-8")):
            return "user"
        if not auth_enabled():
            return "admin"
        return ""

    async def verify_user(request: Request, authorization: str = Header(default="")) -> str:
        role = role_for_token(authorization)
        if not role:
            raise HTTPException(status_code=401, detail="冰箱助手访问密码不正确。")
        return role

    async def verify_admin(role: str = Depends(verify_user)) -> str:
        if role != "admin":
            raise HTTPException(status_code=403, detail="仅管理员可执行该操作。")
        return role

    @app.get("/fridge", response_class=HTMLResponse)
    def fridge_index(request: Request):
        redirect_url = canonical_redirect_url(request)
        if redirect_url:
            return RedirectResponse(redirect_url, status_code=307)
        path = os.path.join(static_dir, "index.html")
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as handle:
                return HTMLResponse(handle.read(), headers={"Cache-Control": "no-store, max-age=0"})
        return HTMLResponse("<h1>海信冰箱 AI 助手</h1>", headers={"Cache-Control": "no-store, max-age=0"})

    @app.get("/api/fridge/auth/status")
    def fridge_auth_status():
        return {"enabled": auth_enabled()}

    @app.post("/api/fridge/auth/login")
    def fridge_auth_login(req: FridgeAuthLoginRequest):
        if not auth_enabled():
            return {"ok": True, "role": "admin"}
        role = role_for_token(req.password)
        if not role:
            raise HTTPException(status_code=401, detail="冰箱助手访问密码不正确。")
        return {"ok": True, "role": role}

    @app.get("/api/fridge/summary")
    def fridge_summary(role: str = Depends(verify_user)):
        return {**store.summary(), "role": role}

    @app.get("/api/fridge/options")
    def fridge_options(role: str = Depends(verify_user)):
        return {**store.options(), "role": role}

    @app.post("/api/fridge/upload/{dataset}")
    async def fridge_upload(dataset: str, file: UploadFile = File(...), role: str = Depends(verify_admin)):
        if dataset not in FRIDGE_DATASETS:
            raise HTTPException(status_code=404, detail="未知数据集。")
        data = await file.read()
        try:
            with route_lock:
                meta = store.save_dataset(dataset, file.filename or f"{dataset}.xlsx", data)
            return {"ok": True, "dataset": dataset, "meta": meta}
        except Exception as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.get("/api/fridge/sessions")
    def fridge_sessions(role: str = Depends(verify_user)):
        return {"sessions": store.load_sessions()}

    @app.post("/api/fridge/sessions")
    def fridge_create_session(req: FridgeSessionCreateRequest, role: str = Depends(verify_user)):
        with route_lock:
            session = store.create_session(req.title)
        return session

    @app.patch("/api/fridge/sessions/{session_id}")
    def fridge_patch_session(session_id: str, req: FridgeSessionPatchRequest, role: str = Depends(verify_user)):
        try:
            with route_lock:
                return store.update_session(session_id, title=req.title, favorite=req.favorite)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.delete("/api/fridge/sessions/{session_id}")
    def fridge_delete_session(session_id: str, role: str = Depends(verify_user)):
        with route_lock:
            store.delete_session(session_id)
        return {"ok": True}

    @app.post("/api/fridge/sessions/{session_id}/messages")
    def fridge_send_message(session_id: str, req: FridgeMessageRequest, role: str = Depends(verify_user)):
        with route_lock:
            sessions = store.load_sessions()
            session = next((item for item in sessions if item.get("id") == session_id), None)
            if not session:
                raise HTTPException(status_code=404, detail="会话不存在。")
            duplicate_message = store.find_duplicate_message(session, req.message, req.request_id, req.model)
            if duplicate_message:
                return {
                    "session": session,
                    "message": duplicate_message,
                    "evidence": duplicate_message.get("sources", {}),
                    "deduplicated": True,
                }
            user_message = {
                "id": uuid.uuid4().hex[:12],
                "role": "user",
                "content": req.message,
                "request_id": req.request_id or uuid.uuid4().hex[:12],
                "model": req.model,
                "created_at": _now_iso(),
            }
            history = list(session.get("messages") or [])
            evidence = store.build_evidence(req.message, selected_model=req.model)
            answer = answer_fridge_question(req.message, history + [user_message], evidence)
            assistant_message = {
                "id": uuid.uuid4().hex[:12],
                "role": "assistant",
                "content": answer,
                "created_at": _now_iso(),
                "sources": {
                    "specs": len(evidence.get("specs") or []),
                    "marketing": len(evidence.get("marketing") or []),
                    "competitors": len(evidence.get("competitors") or []),
                    "documents": len(evidence.get("documents") or []),
                },
            }
            session.setdefault("messages", []).extend([user_message, assistant_message])
            if session.get("title") in {"", "新会话"}:
                session["title"] = req.message[:28]
            session["updated_at"] = _now_iso()
            store.save_sessions(sessions)
        return {"session": session, "message": assistant_message, "evidence": assistant_message["sources"]}

    @app.post("/api/fridge/feedback")
    def fridge_feedback(req: FridgeFeedbackRequest, role: str = Depends(verify_user)):
        with route_lock:
            item = store.append_feedback(req.model_dump())
        return {"ok": True, "feedback": item}

import datetime as dt
import io
import os
import re
import threading
import uuid

import boto3
import pandas as pd
from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import HTMLResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from product_feature_store import ProductFeatureStore, filter_product_features
from storage_adapters import RuntimeStorage


APP_DATA_DIR = os.getenv("APP_DATA_DIR", ".")
os.makedirs(APP_DATA_DIR, exist_ok=True)

STORAGE = RuntimeStorage()
PRODUCT_FEATURE_STORE = ProductFeatureStore(STORAGE)

CACHE_META_KEY = "cache_meta.json"
HTTP_JOBS_KEY = "http_script_jobs.json"
TABLE_HEADER_LINE = "| 结构分段 | 功能点 | 表现手法 | 旁白（英文） | 字幕-显示卖点名及描述（英文） | 特色效果 | 拍摄角度 | 运镜方式 | 竞品链接 | 竞品盖帽 | 音效 | 时长 |"
SYSTEM_PROMPT = f"""##角色
你是“海外爆款内容引擎”，为海信海外电商产品策划推广提供视频脚本生成服务。你需要基于海信的产品卖点，撰写不同类型的视频脚本，以支持导出为 Word 或 Excel 形式的 Markdown 表格输出。

##限制与优化规范
1. 时长精确控制：脚本总时长需尽量贴近用户给定的“期望视频时长(秒)”。表格的“时长”列必须给出确切秒数，并在表格最后一行增加“总时长”统计。
2. 结构模块化与落地：采用步骤拆解式结构，逻辑务实清晰，可交给国内视频团队拍摄执行。
3. 强调交互与对比镜头：表现手法/拍摄角度/运镜方式中要体现 UI 面板、按键特写、操作反馈和使用前后对比。
4. 品牌 Slogan 收尾：最后一段必须是产品静置全景特写 + Hisense Designed to Ease, Crafted to Cheer.
5. 语言规范：旁白（英文）和字幕-显示卖点名及描述（英文）两列必须是纯英文；其余列必须以中文为主，便于国内制作团队执行。
6. 产品卖点必须严格符合用户提供的信息，不可捏造。
7. 竞品链接/竞品盖帽字段保留；没有可用竞品链接时留空，不要编造。
8. 如需 AI 视频生成 Prompt，请放入表现手法/特色效果/运镜方式等中文描述字段中，以英文括号附带。

##格式要求
必须以标准 Markdown 表格形式输出，绝对不要包裹在 ```markdown 或 ``` 代码块中。
表格必须统一使用以下 12 列，并逐字使用该表头：
{TABLE_HEADER_LINE}
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |

##额外输出
表格后必须追加：

整体AI视频生成Prompt（English）:
- 用一段完整英文描述整支视频的统一风格、镜头语言、光影、场景、产品露出和品牌调性。
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
BEDROCK_MODEL_ID = os.getenv("BEDROCK_MODEL_ID", "eu.amazon.nova-pro-v1:0")
BEDROCK_MAX_TOKENS = int(os.getenv("BEDROCK_MAX_TOKENS", "4096"))

app = FastAPI(title="海外爆款内容引擎 API")
job_lock = threading.Lock()

static_dir = os.path.join(os.path.dirname(__file__), "web_frontend")
if os.path.isdir(static_dir):
    app.mount("/static", StaticFiles(directory=static_dir), name="static")


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


def _update_job(job_id, **fields):
    with job_lock:
        jobs = _load_jobs()
        for job in jobs:
            if job.get("id") == job_id:
                job.update(fields)
                job["updated_at"] = dt.datetime.utcnow().isoformat(timespec="seconds") + "Z"
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


def _infer_variant_label(content: str) -> str:
    text = str(content or "")
    if re.search(r"痛点|烦恼|麻烦|困扰|对比|前后", text, flags=re.IGNORECASE):
        return "偏痛点转化"
    if re.search(r"场景|生活|家庭|厨房|日常", text, flags=re.IGNORECASE):
        return "场景化种草"
    if re.search(r"功能|展示|介绍|演示|操作", text, flags=re.IGNORECASE):
        return "功能展示"
    return "通用脚本"


def _build_prompt(req: GenerateRequest, features: list[dict], variant_index: int) -> str:
    feature_lines = "\n".join(
        f"- {item['name']}: {item['tagline']}。{item['description']}" for item in features
    )
    direction = req.video_type[variant_index % len(req.video_type)] if req.video_type else "场景化/生活方式型"
    variant_no = variant_index + 1
    return f"""
请生成【方案{variant_no}】海外电商短视频脚本（只输出这一套，不要输出其他方案标题）。
- 必须先输出一张符合系统要求的 Markdown 表格（12列，行内时长为秒，最后一行为总时长）。
- 表格必须包含并使用如下表头（逐字一致）：
{TABLE_HEADER_LINE}
- 表格后紧接着输出：整体AI视频生成Prompt（English）/ Negative Prompt / Recommended Settings。
- 与其他方案保持明显差异：开场 hook、表现手法、镜头组织至少两处不同。
- 竞品链接与竞品盖帽：当前暂无可用竞品链接，请两列留空，不要编造。
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
""".strip()


def _call_bedrock(prompt: str, temperature=0.7, top_p=0.9) -> str:
    client = boto3.client("bedrock-runtime", region_name=BEDROCK_AWS_REGION)
    response = client.converse(
        modelId=BEDROCK_MODEL_ID,
        system=[{"text": SYSTEM_PROMPT}],
        messages=[{"role": "user", "content": [{"text": prompt}]}],
        inferenceConfig={
            "maxTokens": BEDROCK_MAX_TOKENS,
            "temperature": temperature,
            "topP": top_p,
        },
    )
    return response["output"]["message"]["content"][0]["text"]


def _build_excel_bytes(job: dict) -> bytes:
    rows = []
    request = job.get("request", {})
    for variant in job.get("variants", []):
        rows.append(
            {
                "方案": variant.get("name", ""),
                "产品品类": request.get("category", ""),
                "产品型号": request.get("model", ""),
                "目标市场": request.get("target_market", ""),
                "发布渠道": request.get("platform", ""),
                "脚本内容": variant.get("content", ""),
            }
        )
    buffer = io.BytesIO()
    with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
        pd.DataFrame(rows).to_excel(writer, index=False, sheet_name="脚本方案")
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
        variants = []
        _update_job(job_id, status="running", progress=8, current_step="已读取产品卖点")
        total = max(1, req.variant_count)
        for i in range(total):
            prompt = _build_prompt(req, features, i)
            _update_job(job_id, progress=int((i / total) * 80) + 10, current_step=f"生成方案 {i + 1}/{total}")
            content = _strip_code_fences(_call_bedrock(prompt))
            if TABLE_HEADER_LINE not in content or "总时长" not in content:
                retry_prompt = prompt + "\n\n补充要求：输出必须完整，不要截断；若篇幅过长请压缩行文但保留完整表格与总时长行。"
                retry_content = _strip_code_fences(_call_bedrock(retry_prompt, temperature=0.3, top_p=0.8))
                if TABLE_HEADER_LINE in retry_content and "总时长" in retry_content:
                    content = retry_content
            variants.append({"name": f"方案{i + 1}", "label": _infer_variant_label(content), "content": content.strip()})
        _update_job(
            job_id,
            status="succeeded",
            progress=100,
            current_step="已完成",
            variants=variants,
            error_message="",
        )
    except Exception as exc:
        _update_job(job_id, status="failed", progress=100, current_step="失败", error_message=str(exc))


@app.get("/", response_class=HTMLResponse)
def index():
    path = os.path.join(static_dir, "index.html")
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as handle:
            return handle.read()
    return "<h1>海外爆款内容引擎 API</h1>"


@app.get("/healthz")
def healthz():
    return {"ok": True}


@app.get("/api/summary")
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


@app.post("/api/upload")
async def upload_product_features(file: UploadFile = File(...)):
    if not file.filename.lower().endswith((".xlsx", ".xls")):
        raise HTTPException(status_code=400, detail="请上传 Excel 文件。")
    data = await file.read()
    try:
        df = pd.read_excel(io.BytesIO(data))
        df_filtered = filter_product_features(df)
        meta = PRODUCT_FEATURE_STORE.save(file.filename, data, df_filtered)
        return {"ok": True, "meta": meta}
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"解析文件失败：{exc}") from exc


@app.get("/api/options")
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


@app.get("/api/features")
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


@app.post("/api/generate")
def generate(req: GenerateRequest):
    job_id = uuid.uuid4().hex[:12]
    job = {
        "id": job_id,
        "created_at": dt.datetime.utcnow().isoformat(timespec="seconds") + "Z",
        "updated_at": dt.datetime.utcnow().isoformat(timespec="seconds") + "Z",
        "status": "pending",
        "progress": 0,
        "current_step": "已提交",
        "request": req.model_dump(),
        "variants": [],
        "error_message": "",
    }
    with job_lock:
        jobs = _load_jobs()
        jobs.insert(0, job)
        _save_jobs(jobs)
    threading.Thread(target=_run_generation, args=(job_id,), daemon=True).start()
    return {"job_id": job_id}


@app.get("/api/jobs")
def jobs():
    return {"jobs": _load_jobs()[:30]}


@app.get("/api/jobs/{job_id}")
def job(job_id: str):
    found = next((item for item in _load_jobs() if item.get("id") == job_id), None)
    if not found:
        raise HTTPException(status_code=404, detail="任务不存在。")
    return found


@app.get("/api/jobs/{job_id}/download")
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

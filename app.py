import streamlit as st
import requests
import json
import os
import pandas as pd
import datetime as dt

try:
    from dotenv import load_dotenv
    load_dotenv()
except ModuleNotFoundError:
    load_dotenv = None

# 缓存文件路径（保存在云端服务器临时目录）
CACHE_FILE_PATH = "cached_product_features.pkl"

COMPETITOR_VIDEO_REFERENCES = {
    "空气炸锅": [
        ("Ninja Air Fryers（产品宣传示例）", "https://www.youtube.com/watch?v=6TryhZRuZBs"),
        ("COSORI（官方频道）", "https://m.youtube.com/c/Cosori/videos"),
        ("Philips Airfryer（设置与使用示例）", "https://m.youtube.com/watch?v=MOjKt3MfXNs"),
        ("Instant Vortex ClearCook（产品演示示例）", "https://m.youtube.com/watch?v=NeN9yBfTVNo"),
    ],
    "微波炉": [
        ("Panasonic Inverter Microwaves（产品视频示例）", "https://m.youtube.com/watch?v=k50Ckg_E4rU"),
        ("LG NeoChef（Smart Inverter 卖点视频示例）", "https://m.youtube.com/watch?v=0RDCJqSF4dY"),
    ],
}

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

def build_reference_links_md(product_category):
    refs = []
    for k, items in COMPETITOR_VIDEO_REFERENCES.items():
        if k in (product_category or ""):
            refs = items
            break
    if not refs:
        refs = COMPETITOR_VIDEO_REFERENCES.get("空气炸锅", [])
    lines = [
        "",
        "---",
        "",
        "竞品优秀宣传视频参考链接（仅供内部学习，不代表推荐/背书）：",
    ]
    for title, url in refs:
        lines.append(f"- {title}：{url}")
    return "\n".join(lines)

def get_api_key():
    try:
        if "MINIMAX_API_KEY" in st.secrets:
            return st.secrets["MINIMAX_API_KEY"]
    except Exception:
        pass
    return os.getenv("MINIMAX_API_KEY", "")

def get_product_data():
    """从本地缓存文件读取数据"""
    if os.path.exists(CACHE_FILE_PATH):
        try:
            return pd.read_pickle(CACHE_FILE_PATH)
        except Exception:
            return pd.DataFrame()
    return pd.DataFrame()

st.set_page_config(page_title="海外电商视频脚本生成器", page_icon="🎬", layout="wide")

st.title("🎬 海外电商视频脚本生成器")
st.markdown("基于《电商产品种草视频知识库》规范，为您自动生成高转化率的短视频脚本。")

# 检查是否已有缓存数据
df_products = get_product_data()

# 数据上传模块
if df_products.empty:
    st.info("👋 欢迎使用！首次使用请上传您的《产品卖点库》Excel 文件。数据仅在当前服务器安全暂存，不会泄露。")
    uploaded_file = st.file_uploader("拖拽或点击上传 Excel 文件", type=["xlsx", "xls"])
    if uploaded_file is not None:
        with st.spinner("正在解析文件..."):
            try:
                df = pd.read_excel(uploaded_file)
                # 过滤英文卖点
                mask = df['language'].str.contains('英语|全球通用版', na=False)
                df_filtered = df[mask].dropna(subset=['Feature Description', 'model', 'Category'])
                
                # 保存为本地缓存文件（Pickle 格式加载更快）
                df_filtered.to_pickle(CACHE_FILE_PATH)
                st.success("✅ 文件解析并安全缓存成功！正在重新加载界面...")
                st.rerun()
            except Exception as e:
                st.error(f"解析文件失败: {e}")
    # 强制停止渲染后面的组件，直到用户上传文件
    st.stop()
else:
    # 允许用户更新题库
    with st.expander("🔄 更新产品卖点库 (目前已加载数据)"):
        uploaded_file = st.file_uploader("如果您有最新的 Excel，可以在此上传覆盖", type=["xlsx", "xls"])
        if uploaded_file is not None:
            with st.spinner("正在更新文件..."):
                try:
                    df = pd.read_excel(uploaded_file)
                    mask = df['language'].str.contains('英语|全球通用版', na=False)
                    df_filtered = df[mask].dropna(subset=['Feature Description', 'model', 'Category'])
                    df_filtered.to_pickle(CACHE_FILE_PATH)
                    st.success("✅ 数据库已更新！")
                    st.rerun()
                except Exception as e:
                    st.error(f"解析文件失败: {e}")

with st.sidebar:
    st.header("⚙️ 基础配置")
    platform = st.selectbox("目标平台", ["TikTok / Reels / Shorts", "Amazon 主图视频", "独立站详情页"])
    target_market = st.selectbox("目标市场", ["北美 (US/CA)", "欧洲 (UK/DE/FR)", "东南亚", "其他"])
    video_type = st.multiselect("视频类型定位 (可多选)", 
                                ["问题解决/痛点挖掘型", "产品展示/功能介绍型", "开箱体验型", "场景化/生活方式型", "测评/对比型"],
                                default=["问题解决/痛点挖掘型", "场景化/生活方式型"])
    
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
        st.session_state["selected_features"] = available_feature_names[:3]
        st.session_state["feature1"] = st.session_state["selected_features"][0] if len(st.session_state["selected_features"]) > 0 else ""
        st.session_state["feature2"] = st.session_state["selected_features"][1] if len(st.session_state["selected_features"]) > 1 else ""
        st.session_state["feature3"] = st.session_state["selected_features"][2] if len(st.session_state["selected_features"]) > 2 else ""

    def _sync_feature_inputs():
        sel = st.session_state.get("selected_features", [])
        st.session_state["feature1"] = sel[0] if len(sel) > 0 else ""
        st.session_state["feature2"] = sel[1] if len(sel) > 1 else ""
        st.session_state["feature3"] = sel[2] if len(sel) > 2 else ""

    st.markdown("**核心卖点（可从该型号卖点库中选择，最多3个）**")
    st.multiselect(
        "从卖点库选择",
        available_feature_names,
        key="selected_features",
        max_selections=3,
        on_change=_sync_feature_inputs,
        help="选择后会自动填充到下方的卖点输入框，您仍可手动微调文案（不要篡改卖点事实）。",
    )

    st.markdown("**核心卖点（用于生成脚本）**")
    st.text_input("卖点 1", key="feature1")
    st.text_input("卖点 2", key="feature2")
    st.text_input("卖点 3", key="feature3")

    feature1 = st.session_state.get("feature1", "")
    feature2 = st.session_state.get("feature2", "")
    feature3 = st.session_state.get("feature3", "")

with col2:
    st.subheader("🎯 营销诉求")
    video_usage = st.selectbox("视频用途", ["站外种草", "站内首推", "内部培训", "其他"])
    expected_duration = st.slider("期望视频时长(秒)", 15, 45, 30, 1)
    project_type = st.selectbox("项目类型(可选)", ["常规上新", "新品上市", "大促活动", "教程培训", "其他"])
    general_audience_mode = st.checkbox("不指定目标受众（通用卖点）", value=False)
    target_audience = st.text_input(
        "目标受众",
        "关注身材管理的年轻上班族/学生",
        disabled=general_audience_mode,
    )
    if general_audience_mode:
        target_audience = ""
    pain_points = st.text_area("用户痛点", "想吃油炸食品但怕胖；聚会时做饭太麻烦")

    with st.expander("📅 节日/热点推荐（可选）", expanded=False):
        publish_date = st.date_input("内容发布日期", value=dt.date.today())
        market_key = _market_key(target_market)
        upcoming_nodes = get_upcoming_nodes(market_key, publish_date, limit=4)
        selected_nodes = st.multiselect("近期开节点（可多选）", upcoming_nodes, default=upcoming_nodes[:2] if upcoming_nodes else [])
        trend_keywords = st.text_input("热点/趋势关键词（可选）", "TikTok趋势: Girl Dinner / Lazy Meals")

    festival_hotspot_parts = []
    if selected_nodes:
        festival_hotspot_parts.append("节日节点: " + " / ".join(selected_nodes))
    if trend_keywords and trend_keywords.strip():
        festival_hotspot_parts.append(trend_keywords.strip())
    festival_hotspot = "；".join(festival_hotspot_parts)

st.markdown("---")

# 定义系统 Prompt
SYSTEM_PROMPT = """##角色 
你是一位专业的视频脚本撰写智能体，为海信海外电商产品策划推广提供服务。你需要基于海信的产品卖点，撰写不同类型（产品展示视频、产品介绍视频、产品操作视频、产品种草视频等）的视频脚本，以支持导出为word或excel形式的Markdown表格输出。 
 
##限制与优化规范
1. **时长精确控制**：脚本总时长需严格控制在 15-45 秒以内，并尽量贴近用户给定的“期望视频时长(秒)”。表格的“时长”列必须给出**确切的秒数**（如：5秒），并在表格最后一行增加“总时长”统计。
2. **结构模块化与落地**：对于产品展示和操作类视频，采用“步骤拆解式”的结构分段（如：开箱检查、安装放置、功能A演示、对比实验等），逻辑务实清晰。
3. **强调交互与对比镜头**：在“画面描述”中，必须包含**UI面板/按键的特写、操作反馈（如LED屏幕显示、滴滴声）**，并尽量设计**使用前后的对比实验镜头**（如：传统解冻 vs 微波炉解冻）以直观展示卖点。
4. **品牌 Slogan 收尾**：脚本的最后一段（总结）必须是固定的格式：产品静置全景特写 + 海信品牌 Slogan（"Hisense Designed to Ease, Crafted to Cheer."）。
5. **语言规范（极其重要）**：
   - 面向海外观众的内容：**【旁白/字幕】列必须完全使用纯英文**（或对应的海外市场语言，绝对不要写中文翻译）。
   - 面向国内制作团队的内容：表格中的**所有其他列（如：结构分段、画面描述、音效、时长等）必须严格使用全中文**进行描述，以便国内的拍摄和剪辑团队能无障碍阅读和执行。
   - 产品卖点：必须严格符合用户提供的信息，不可捏造。
6. **AI Prompt**：仅在“画面描述”列的中文描述之后，附带一段括号包裹的英文 Prompt，专门用于 AI 视频生成（如：[AI Prompt: xxx]）。

## 格式要求
必须以**标准的 Markdown 表格**形式输出，**请直接输出纯文本形式的表格，绝对不要将表格包裹在 ```markdown 或 ``` 代码块中！**
请确保每一行都用 `|` 完整闭合，表格必须统一使用以下 5 列：
| 结构分段 | 画面描述(含AI Prompt) | 旁白/字幕(纯英文) | 音效 | 时长 |
| :--- | :--- | :--- | :--- | :--- |"""

# 在表格后追加整体 AI Prompt 的要求（不要影响表格渲染）
SYSTEM_PROMPT += """

## 额外输出（必须追加在表格之后）
在表格输出完成后，请紧接着追加以下内容（同样不要使用 ``` 代码块）：

整体AI视频生成Prompt（English）:
- 以一段完整英文描述输出，概括整支视频的统一风格、镜头语言、光影、场景、人物（如有）、产品露出与品牌调性，确保与表格分镜一致。
- 必须包含一致性约束：同一产品外观保持一致（颜色/材质/外观），同一厨房/家居风格保持一致，镜头节奏为短视频节奏（15-45秒）。
- 必须包含摄影/画面关键词：4k, cinematic lighting, shallow depth of field, smooth camera movement。
- 必须包含“品牌收尾”要求：最后镜头为产品静置全景特写 + Hisense Designed to Ease, Crafted to Cheer.

Negative Prompt（English，选填）:
- 输出一行即可，例如：no watermark, no subtitles baked into video, no extra logos, no deformed hands, no blurry frames

Recommended Settings（选填）:
- 输出一行即可，例如：16:9 or 9:16, 24fps, 4-6s clips per shot, realistic style
"""

def generate_script_minimax(api_key, user_prompt):
    url = "https://api.minimax.chat/v1/text/chatcompletion_v2"
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}"
    }
    
    payload = {
        "model": "abab6.5s-chat",
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt}
        ],
        "temperature": 0.7,
        "top_p": 0.9,
        "max_tokens": 2048  # 增加 max_tokens 确保长表格不会被截断
    }
    
    try:
        # 添加 verify=False 忽略 SSL 验证，防止在某些代理/公司网络下报错
        response = requests.post(url, headers=headers, json=payload, verify=False)
        response.raise_for_status()
        result = response.json()
        return result["choices"][0]["message"]["content"]
    except Exception as e:
        return f"API 调用失败: {str(e)}\n请检查 API Key 是否正确，或网络是否通畅。"

if st.button("🚀 生成爆款脚本", type="primary", use_container_width=True):
    api_key = get_api_key()
    
    if not api_key:
        st.error("未找到 MiniMax API Key。请在 Streamlit Cloud 的 Secrets 或本地 .env 文件中配置 MINIMAX_API_KEY。")
    else:
        with st.spinner("正在调用大模型生成脚本..."):
            # 构建用户 Prompt
            user_prompt = f"""
            请帮我生成一个海外电商短视频脚本。
            - 目标平台：{platform}
            - 目标市场：{target_market}
            - 建议视频类型：{', '.join(video_type)}
            - 视频用途：{video_usage}
            - 期望视频时长(秒)：{expected_duration}
            - 项目类型：{project_type}
            - 产品品类：{selected_category}
            - 产品型号：{selected_model}
            - 核心卖点：1. {feature1} 2. {feature2} 3. {feature3}
            - 目标受众：{target_audience if target_audience else "通用卖点（不指定具体人群）"}
            - 用户痛点：{pain_points}
            - 内容发布日期：{publish_date if 'publish_date' in locals() else ""}
            - 结合热点：{festival_hotspot}
            """
            
            # 调用 API
            generated_content = generate_script_minimax(api_key, user_prompt)
            
            # 过滤掉模型可能返回的 ```markdown 或 ``` 标记，防止前端渲染为代码块
            if generated_content.startswith("```markdown"):
                generated_content = generated_content[len("```markdown"):].strip()
            elif generated_content.startswith("```"):
                generated_content = generated_content[len("```"):].strip()
            
            if generated_content.endswith("```"):
                generated_content = generated_content[:-len("```")].strip()
            
            st.success("脚本生成成功！")
            st.markdown("### 📝 生成结果预览")
            st.markdown(generated_content + build_reference_links_md(selected_category))

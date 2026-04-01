import streamlit as st
import requests
import json
import os
import pandas as pd

try:
    from dotenv import load_dotenv
    load_dotenv()
except ModuleNotFoundError:
    load_dotenv = None

# 缓存文件路径（保存在云端服务器临时目录）
CACHE_FILE_PATH = "cached_product_features.pkl"

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
    
    # 自动提取该型号的卖点
    model_features = df_products[df_products['model'] == selected_model]
    default_f1 = model_features.iloc[0]['Feature Name'] if len(model_features) > 0 else ""
    default_f2 = model_features.iloc[1]['Feature Name'] if len(model_features) > 1 else ""
    default_f3 = model_features.iloc[2]['Feature Name'] if len(model_features) > 2 else ""

    st.markdown("**核心卖点 (已根据型号自动提取)**")
    feature1 = st.text_input("卖点 1", default_f1)
    feature2 = st.text_input("卖点 2", default_f2)
    feature3 = st.text_input("卖点 3", default_f3)

with col2:
    st.subheader("🎯 营销诉求")
    target_audience = st.text_input("目标受众", "关注身材管理的年轻上班族/学生")
    pain_points = st.text_area("用户痛点", "想吃油炸食品但怕胖；聚会时做饭太麻烦")
    festival_hotspot = st.text_input("结合热点/节日 (可选)", "TikTok趋势: Girl Dinner / Lazy Meals")

st.markdown("---")

# 定义系统 Prompt
SYSTEM_PROMPT = """##角色 
你是一位专业的视频脚本撰写智能体，为海信海外电商产品策划推广提供服务。你需要基于海信的产品卖点，撰写不同类型（产品展示视频、产品介绍视频、产品操作视频、产品种草视频等）的视频脚本，以支持导出为word或excel形式的Markdown表格输出。 
 
##限制与优化规范
1. **时长精确控制**：脚本总时长需严格控制在 15-45 秒以内。表格的“时长”列必须给出**确切的秒数**（如：5秒），并在表格最后一行增加“总时长”统计。
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
            - 产品品类：{selected_category}
            - 产品型号：{selected_model}
            - 核心卖点：1. {feature1} 2. {feature2} 3. {feature3}
            - 目标受众：{target_audience}
            - 用户痛点：{pain_points}
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
            st.markdown(generated_content)

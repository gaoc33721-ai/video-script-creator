import streamlit as st
import requests
import json
import os
import pandas as pd
from dotenv import load_dotenv

# 加载 .env 文件中的环境变量
load_dotenv()

# 数据加载与缓存
@st.cache_data
def load_product_data():
    file_path = r"C:\Users\gaochao8\Downloads\产品卖点库（内容中台导出0126）.xlsx"
    try:
        df = pd.read_excel(file_path)
        # 只保留包含英文或全球通用版的卖点，以供海外视频使用
        mask = df['language'].str.contains('英语|全球通用版', na=False)
        df_filtered = df[mask].dropna(subset=['Feature Description', 'model', 'Category'])
        return df_filtered
    except Exception as e:
        st.error(f"加载产品卖点库失败: {e}")
        return pd.DataFrame()

df_products = load_product_data()

st.set_page_config(page_title="跨境电商视频脚本生成器", page_icon="🎬", layout="wide")

st.title("🎬 跨境电商视频脚本生成器")
st.markdown("基于《电商产品种草视频知识库》规范，为您自动生成高转化率的短视频脚本。")

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
4. **专业转场设计**：必须为每一个分段设计具体的“转场方式”（如：硬切/Hard Cut、镜头遮挡转场/Whip Pan、放大转场/Zoom in、淡入淡出等），确保画面衔接流畅。
5. **品牌 Slogan 收尾**：脚本的最后一段（总结）必须是固定的格式：产品静置全景特写 + 海信品牌 Slogan（"Hisense Designed to Ease, Crafted to Cheer."）。
6. **语言规范（极其重要）**：
   - 面向海外观众的内容：**【旁白/字幕】或【操作指引】列必须完全使用纯英文**（或对应的海外市场语言，绝对不要写中文翻译）。
   - 面向国内制作团队的内容：表格中的**所有其他列（如：结构分段、画面描述、转场方式、视觉提示、音效、时长等）必须严格使用全中文**进行描述，以便国内的拍摄和剪辑团队能无障碍阅读和执行。
   - 产品卖点：必须严格符合用户提供的信息，不可捏造。
7. **AI Prompt**：仅在“画面描述”列的中文描述之后，附带一段括号包裹的英文 Prompt，专门用于 AI 视频生成（如：[AI Prompt: xxx]）。

## 格式要求（根据视频类型动态调整）
必须以**标准的 Markdown 表格**形式输出，**请直接输出纯文本形式的表格，绝对不要将表格包裹在 ```markdown 或 ``` 代码块中！**

**情况 A：如果用户选择的视频类型包含“开箱体验型”或“产品操作视频”**，表格必须精确包含以下 5 列：
| 步骤 | 画面描述(含AI Prompt) | 操作指引(纯英文) | 视觉提示 | 时长 |
| :--- | :--- | :--- | :--- | :--- |
*(注：“步骤”即开箱/操作的具体环节；“操作指引”为屏幕文字或动作说明，需纯英文；“视觉提示”为界面特写或动作特写指示。)*

**情况 B：如果是“功能介绍型”、“产品展示视频”、“种草视频”等其他类型**，表格必须精确包含以下 6 列：
| 结构分段 | 画面描述(含AI Prompt) | 转场方式 | 旁白/字幕(纯英文) | 音效 | 时长 |
| :--- | :--- | :--- | :--- | :--- | :--- |"""

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
    # 从环境变量中获取 API Key
    api_key = os.getenv("MINIMAX_API_KEY")
    
    if not api_key:
        st.error("未找到 MiniMax API Key。请在项目根目录的 .env 文件中配置 MINIMAX_API_KEY。")
    else:
        with st.spinner("正在调用大模型生成脚本..."):
            # 构建用户 Prompt
            user_prompt = f"""
            请帮我生成一个跨境电商短视频脚本。
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

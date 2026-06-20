import streamlit as st
import requests
import json
import os
import sys
import re
import hashlib
import random
import string
from datetime import datetime, timedelta
from supabase import create_client, Client

# ============ Supabase 配置 ============
try:
    SUPABASE_URL = st.secrets["SUPABASE_URL"]
    SUPABASE_KEY = st.secrets["SUPABASE_KEY"]
except (KeyError, FileNotFoundError):
    SUPABASE_URL = os.getenv("SUPABASE_URL", "")
    SUPABASE_KEY = os.getenv("SUPABASE_KEY", "")
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

# ============ 用户认证系统 ============
def hash_password(password):
    return hashlib.sha256(password.encode()).hexdigest()

def generate_token():
    return ''.join(random.choices(string.ascii_letters + string.digits, k=32))

def login_user(username, password):
    response = supabase.table("users").select("*").eq("username", username).execute()
    if response.data:
        user = response.data[0]
        if user["password"] == hash_password(password):
            token = generate_token()
            supabase.table("users").update({"token": token}).eq("username", username).execute()
            st.session_state.login_token = token
            return True
    return False

def register_user(username, password):
    response = supabase.table("users").select("*").eq("username", username).execute()
    if response.data:
        return False
    supabase.table("users").insert({
        "username": username,
        "password": hash_password(password),
        "role": "user"
    }).execute()
    initialize_laws_for_user(username)
    return True

def initialize_laws_for_user(username):
    response = supabase.table("laws").select("*").eq("username", "admin").execute()
    if response.data:
        for law in response.data:
            law["username"] = username
            law["id"] = f"{username}_{law['id']}"
            supabase.table("laws").upsert(law, on_conflict="id").execute()

def check_cookie_login():
    token = st.session_state.get("login_token")
    if not token:
        return False
    response = supabase.table("users").select("*").eq("token", token).execute()
    if response.data:
        user = response.data[0]
        st.session_state.logged_in = True
        st.session_state.username = user["username"]
        return True
    return False

# ============ 页面配置 ============
st.set_page_config(page_title="全维推演工厂", page_icon="⚽", layout="wide")

# ============ 登录/注册界面 ============
if "logged_in" not in st.session_state:
    st.session_state.logged_in = False
    st.session_state.username = ""
if "login_token" not in st.session_state:
    st.session_state.login_token = ""

if not st.session_state.logged_in and st.session_state.login_token:
    check_cookie_login()

if not st.session_state.logged_in:
    st.title("⚽ 全维推演工厂 - 登录")
    
    tab_login, tab_register = st.tabs(["登录", "注册"])
    
    with tab_login:
        username = st.text_input("用户名", key="login_username")
        password = st.text_input("密码", type="password", key="login_password")
        if st.button("登录", use_container_width=True):
            if login_user(username, password):
                st.session_state.logged_in = True
                st.session_state.username = username
                st.success("登录成功！")
                st.rerun()
            else:
                st.error("用户名或密码错误。")
    
    with tab_register:
        new_username = st.text_input("新用户名", key="reg_username")
        new_password = st.text_input("新密码", type="password", key="reg_password")
        confirm_password = st.text_input("确认密码", type="password", key="reg_confirm")
        if st.button("注册", use_container_width=True):
            if new_password != confirm_password:
                st.error("两次密码不一致。")
            elif len(new_username) < 3:
                st.error("用户名至少3个字符。")
            elif len(new_password) < 6:
                st.error("密码至少6个字符。")
            elif register_user(new_username, new_password):
                st.success("注册成功！请登录。")
            else:
                st.error("用户名已存在。")
    
    st.stop()

# ============ 已登录状态 ============
st.sidebar.title(f"👤 {st.session_state.username}")

if st.sidebar.button("🚪 登出", use_container_width=True):
    supabase.table("users").update({"token": None}).eq("username", st.session_state.username).execute()
    st.session_state.logged_in = False
    st.session_state.username = ""
    st.session_state.login_token = ""
    st.session_state.deepseek_key = ""
    st.session_state.tavily_key = ""
    st.rerun()

# ============ 从 Supabase 读取 API Key ============
def load_api_keys(username):
    response = supabase.table("api_keys").select("*").eq("username", username).execute()
    if response.data:
        return response.data[0]
    return {"deepseek_key": "", "tavily_key": ""}

def save_api_keys(username, deepseek_key, tavily_key):
    supabase.table("api_keys").upsert({
        "username": username,
        "deepseek_key": deepseek_key,
        "tavily_key": tavily_key
    }, on_conflict="username").execute()

api_keys = load_api_keys(st.session_state.username)

# ============ 初始化 session_state ============
if "deepseek_key" not in st.session_state:
    st.session_state.deepseek_key = api_keys.get("deepseek_key", "")
if "tavily_key" not in st.session_state:
    st.session_state.tavily_key = api_keys.get("tavily_key", "")
if "search_report" not in st.session_state:
    st.session_state.search_report = ""
if "analysis_report" not in st.session_state:
    st.session_state.analysis_report = ""
if "current_match" not in st.session_state:
    st.session_state.current_match = ""
if "current_record_id" not in st.session_state:
    st.session_state.current_record_id = None
if "current_match_time" not in st.session_state:
    st.session_state.current_match_time = None

# ============ 侧边栏：API Key 设置 ============
st.sidebar.title("⚙️ 设置")

with st.sidebar.expander("🔑 API Key 管理", expanded=True):
    deepseek_key = st.text_input(
        "DeepSeek API Key",
        type="password",
        value=st.session_state.deepseek_key,
        help="在 platform.deepseek.com 获取"
    )
    tavily_key = st.text_input(
        "Tavily API Key（可选）",
        type="password",
        value=st.session_state.tavily_key,
        help="可选。留空则使用系统默认的共享 Key。"
    )
    if st.button("💾 保存 Key", use_container_width=True):
        if deepseek_key.strip():
            st.session_state.deepseek_key = deepseek_key.strip()
            st.session_state.tavily_key = tavily_key.strip()
            save_api_keys(st.session_state.username, deepseek_key.strip(), tavily_key.strip())
            st.success("API Key 已永久保存！")
        else:
            st.warning("请至少输入 DeepSeek API Key。")

    if st.session_state.deepseek_key:
        if st.session_state.tavily_key:
            st.info("✅ 已使用你自己的 Tavily Key")
        else:
            st.info("✅ 已使用系统默认 Tavily Key")
    else:
        st.warning("⚠️ 请先配置 DeepSeek API Key")

# ============ 购买API Token入口（预留插槽） ============
with st.sidebar.expander("💰 购买API Token", expanded=False):
    purchase_url = os.getenv("PURCHASE_API_URL", "")
    if purchase_url:
        st.info("🚀 点击下方按钮购买API Token，无需自行注册。")
        if st.button("前往购买", use_container_width=True):
            st.markdown(f"[点击这里购买]({purchase_url})")
    else:
        st.info("📢 API Token 购买功能暂未开放。")
        st.caption("请先使用你自己的 API Key。")

# ============ 从 session_state 读取密钥 ============
API_KEY = st.session_state.deepseek_key

if st.session_state.tavily_key:
    TAVILY_API_KEY = st.session_state.tavily_key
else:
    try:
        TAVILY_API_KEY = st.secrets["default_tavily_key"]
    except (KeyError, FileNotFoundError):
        TAVILY_API_KEY = ""

# ============ API 配置 ============
URL = "https://api.deepseek.com/v1/chat/completions"

def resource_path(relative_path):
    if hasattr(sys, '_MEIPASS'):
        return os.path.join(sys._MEIPASS, relative_path)
    return os.path.join(os.path.abspath("."), relative_path)

# 推演引擎核心指令：云端从 Secrets 读，本地从文件读
try:
    system_prompt_analysis = st.secrets["analysis_prompt"]
except (KeyError, FileNotFoundError):
    with open(resource_path("prompt_analysis.md"), "r", encoding="utf-8") as f:
        system_prompt_analysis = f.read()

# 搜索和校准提示词仍从文件读取
with open(resource_path("prompt_search.md"), "r", encoding="utf-8") as f:
    system_prompt_search = f.read()
with open(resource_path("prompt_calibrate.md"), "r", encoding="utf-8") as f:
    system_prompt_calibrate = f.read()

# ============ 从 Supabase 加载定律库（多用户版本） ============
def load_laws_from_supabase():
    try:
        response = supabase.table("laws").select("*").or_(
            f"username.eq.{st.session_state.username},username.eq.admin"
        ).execute()
        if response.data:
            return {"laws": response.data}
        return {"laws": []}
    except Exception as e:
        st.error(f"从数据库加载定律失败：{e}")
        return {"laws": []}

def save_law_to_supabase(law):
    try:
        law["username"] = st.session_state.username
        supabase.table("laws").upsert(law, on_conflict="id").execute()
        return True
    except Exception as e:
        st.error(f"保存定律失败：{e}")
        return False

laws_data = load_laws_from_supabase()

def call_deepseek(system_prompt, user_query, enable_search=False, search_mode="pre_match", summarize=True):
    if not API_KEY:
        st.error("请在侧边栏设置 API Key！")
        return ""

    if not enable_search:
        response = requests.post(
            url=URL,
            headers={"Authorization": f"Bearer {API_KEY}", "Content-Type": "application/json"},
            json={
                "model": "deepseek-v4-pro",
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_query}
                ]
            }
        )
        data = response.json()
        if 'error' in data:
            st.error(f"API 错误：{data['error']}")
            return ""
        return data['choices'][0]['message'].get('content', '')

    if not TAVILY_API_KEY:
        st.error("未配置 Tavily API Key，无法联网搜索。")
        return ""

    all_search_results = ""
    if search_mode == "pre_match":
        search_rounds = [
            f"{user_query} 首发阵容 伤病 历史交锋",
            f"{user_query} 赔率 裁判 赛前新闻 教练发言",
            f"{user_query} 出线形势 战术分析 关键球员"
        ]
    else:
        search_rounds = [
            f"{user_query} 最终比分 进球者 进球时间",
            f"{user_query} 赛后技术统计 射门 控球率 角球",
            f"{user_query} 赛后报告 比赛回顾 关键事件 红黄牌"
        ]

    for query in search_rounds:
        try:
            tavily_response = requests.post(
                "https://api.tavily.com/search",
                headers={"Content-Type": "application/json"},
                json={
                    "api_key": TAVILY_API_KEY,
                    "query": query,
                    "search_depth": "advanced",
                    "max_results": 5
                }
            )
            tavily_data = tavily_response.json()
            for item in tavily_data.get("results", []):
                content = item.get('content', '')
                if len(content) > 30:
                    all_search_results += f"- {item.get('title', '')}: {content}\n"
        except Exception as e:
            all_search_results += f"搜索失败：{str(e)}\n"

    if not all_search_results:
        return "搜索未返回有效结果，请稍后重试。"

    if not summarize:
        return all_search_results

    new_user_message = f"""以下是针对你提出的问题进行的多轮深度搜索结果。请仔细阅读，并严格按照你的系统指令中的格式要求，生成一份完整的报告。

搜索结果：
{all_search_results}

请注意：
1. 对所有搜索结果进行综合分析，不要遗漏关键信息。
2. 如果某项信息在搜索结果中确实不存在，请在报告中标注"暂无"。
3. 在涉及到球员状态、战术分析等需要专业知识的部分，可以结合你自身的知识库进行补充。
"""

    response2 = requests.post(
        url=URL,
        headers={"Authorization": f"Bearer {API_KEY}", "Content-Type": "application/json"},
        json={
            "model": "deepseek-v4-pro",
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": new_user_message}
            ]
        }
    )
    data2 = response2.json()
    if 'error' in data2:
        st.error(f"API 错误：{data2['error']}")
        return ""
    return data2['choices'][0]['message'].get('content', '')

# ============ 历史记录管理（Supabase） ============
def save_record(match, search_report, analysis_report):
    record = {
        "username": st.session_state.username,
        "match": match,
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "search_report": search_report,
        "analysis_report": analysis_report,
        "calibration": None
    }
    supabase.table("history").insert(record).execute()

def load_history():
    response = supabase.table("history").select("*").eq("username", st.session_state.username).order("timestamp", desc=True).execute()
    return response.data

def load_record_to_session(record):
    st.session_state.search_report = record["search_report"]
    st.session_state.analysis_report = record["analysis_report"]
    st.session_state.current_match = record["match"]
    st.session_state.current_record_id = record["id"]

def calibrate_record(record):
    search_query = f"请联网搜索 {record['match']} 的赛后完整数据（比分、进球、技术统计、关键事件等）。"
    post_match_data = call_deepseek(
        system_prompt_calibrate, search_query,
        enable_search=True, search_mode="post_match", summarize=False
    )

    if any(kw in post_match_data for kw in [
        "比赛尚未开始", "赛后数据未更新", "未返回有效结果", "搜索失败", "无法找到"
    ]):
        return False, post_match_data, [], []

    all_laws = laws_data["laws"]
    laws_text = json.dumps(all_laws, ensure_ascii=False, indent=2)

    summary_prompt = f"""你是一位专业的足球赛后分析师。请基于以下赛后真实数据、赛前推演报告和当前生效的定律库，进行全面的偏差分析，并提炼新的定律或对现有定律的修改建议。

## 当前生效的定律库
{laws_text}

## 赛后真实数据
{post_match_data}

## 赛前推演报告
{record['analysis_report']}

## 分析要求
1. **准确率评分**：根据推演比分、胜负、进球者等与实际结果的匹配度，给出一个0-100的综合准确率评分。
2. **偏差分析**：对比推演与现实的差距，指出哪些逻辑被验证、哪些被推翻，并分析根本原因。
3. **提炼新定律/补丁**：至少提炼出一条新的定律或补丁。如果无需新增定律，请返回空列表。请严格按照以下JSON格式输出。
```json
[
  {{
    "name": "定律名称",
    "content": "核心逻辑（一句话）",
    "trigger": "触发条件",
    "lambda_effect": "对λ值的修正建议（如：主队进攻λ+0.2）"
  }}
]
```
4. **修改现有定律**：如果发现某条现有定律需要修正，请提出修改建议。如果无需修改，请返回空列表。
```json
[
  {{
    "id": "目标定律的ID",
    "name": "修改后的名称",
    "content": "修改后的核心逻辑",
    "trigger": "修改后的触发条件",
    "lambda_effect": "修改后的λ值修正建议"
  }}
]
```
5. **输出格式**：请先用自然语言完成偏差分析和准确率评分，然后在报告末尾附上这两段JSON代码块（先新定律，后修改建议）。
"""

    calibration_report = call_deepseek(
        system_prompt_analysis,
        summary_prompt,
        enable_search=False
    )

    new_laws = []
    modified_laws = []
    try:
        new_match = re.search(r'```json\s*(.*?)\s*```', calibration_report, re.DOTALL)
        if new_match:
            new_laws = json.loads(new_match.group(1))
            calibration_report = calibration_report[:new_match.start()] + calibration_report[new_match.end():]

        mod_match = re.search(r'```json\s*(.*?)\s*```', calibration_report, re.DOTALL)
        if mod_match:
            modified_laws = json.loads(mod_match.group(1))
    except Exception:
        pass

    supabase.table("history").update({
        "calibration": calibration_report,
        "pending_laws": json.dumps(new_laws) if new_laws else None,
        "pending_modifications": json.dumps(modified_laws) if modified_laws else None
    }).eq("id", record["id"]).execute()

    return True, calibration_report, new_laws, modified_laws

def calibrate_all_uncalibrated():
    history = load_history()
    uncalibrated = [r for r in history if not r.get("calibration")]
    if not uncalibrated:
        return 0, 0

    progress_bar = st.progress(0)
    status_text = st.empty()
    calibrated_count = 0
    skipped_count = 0
    for i, rec in enumerate(uncalibrated):
        status_text.text(f"正在校准：{rec['match']} ({i+1}/{len(uncalibrated)})")
        success, report, _, _ = calibrate_record(rec)
        if success:
            calibrated_count += 1
        else:
            skipped_count += 1
            st.warning(f"⏭️ {rec['match']} 比赛尚未开始或数据未更新，已跳过校准。")
        progress_bar.progress((i + 1) / len(uncalibrated))
    status_text.text("所有未校准记录处理完毕！")
    progress_bar.empty()
    return calibrated_count, skipped_count

def calculate_accuracy():
    history = load_history()
    calibrated = [r for r in history if r.get("calibration")]
    if not calibrated:
        return None, []

    scores = []
    records_with_scores = []
    for rec in calibrated:
        calibration_text = rec["calibration"]
        match = re.search(r'准确率评分[：:]\s*(\d+)', calibration_text)
        if not match:
            match = re.search(r'(\d+)\s*/\s*100', calibration_text)
        if match:
            score = int(match.group(1))
            scores.append(score)
            records_with_scores.append({
                "match": rec["match"],
                "timestamp": rec["timestamp"],
                "score": score
            })

    if not scores:
        return None, []

    average = sum(scores) / len(scores)
    return round(average, 1), records_with_scores

# ============ 主界面 ============
st.title("⚽ 全维推演工厂 V2.6")
match = st.text_input("输入比赛对阵（例如：法国 vs 塞内加尔）", placeholder="输入比赛名称...")

col1, col2, col3 = st.columns(3)

with col1:
    if st.button("🔍 搜集赛前数据", use_container_width=True):
        if match:
            with st.spinner("正在搜集赛前数据..."):
                search_query = f"请为 {match} 搜集赛前关键信息，并严格按照模板格式输出。"
                result = call_deepseek(system_prompt_search, search_query, enable_search=True)
                if result:
                    st.session_state.search_report = result
                    st.session_state.current_match = match
                    # 精确提取开赛时间
                    time_patterns = [
                        r'(?:开赛时间|比赛时间|开始时间|Kick[-\s]?off)[：:\s]*(\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2})',
                        r'(\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2})'
                    ]
                    match_time_str = None
                    for pattern in time_patterns:
                        time_match = re.search(pattern, result, re.IGNORECASE)
                        if time_match:
                            match_time_str = time_match.group(1) if time_match.lastindex else time_match.group(0)
                            break
                    if match_time_str:
                        try:
                            match_time = datetime.strptime(match_time_str, "%Y-%m-%d %H:%M")
                            st.session_state.current_match_time = match_time
                            st.info(f"📅 已记录开赛时间：{match_time.strftime('%Y-%m-%d %H:%M')}")
                        except:
                            st.session_state.current_match_time = None
                    else:
                        st.session_state.current_match_time = None
                        st.info("未找到开赛时间，跳过时间校验。")
        else:
            st.warning("请先输入比赛名称")

with col2:
    if st.button("🧠 开始全维推演", use_container_width=True):
        if match and st.session_state.search_report:
            with st.spinner("正在进行全维推演..."):
                analysis_query = f"请基于以下赛前数据，对 {match} 进行推演。\n\n{st.session_state.search_report}"
                result = call_deepseek(system_prompt_analysis, analysis_query, enable_search=False)
                if result:
                    st.session_state.analysis_report = result
                    save_record(match, st.session_state.search_report, st.session_state.analysis_report)
                    st.success("推演记录已保存至云端数据库。")
        elif not match:
            st.warning("请先输入比赛名称")
        else:
            st.warning("请先搜集赛前数据")

# ============ 赛后校准 ============
st.markdown("---")
st.subheader("📊 赛后校准")
if st.button("🔍 搜集赛后数据并校准", use_container_width=True):
    if st.session_state.analysis_report:
        match_time = st.session_state.get("current_match_time")
        if match_time and datetime.now() < match_time + timedelta(minutes=120):
            st.warning(f"⏳ 比赛尚未结束。预计最早校准时间为 {(match_time + timedelta(minutes=120)).strftime('%H:%M')}，请耐心等待。")
            st.stop()

        with st.spinner("正在搜集赛后完整数据..."):
            response = supabase.table("history").select("*").eq("username", st.session_state.username).eq("match", st.session_state.current_match).order("timestamp", desc=True).limit(1).execute()
            if response.data:
                record = response.data[0]
                success, calibration_report, new_laws, modified_laws = calibrate_record(record)

                if success:
                    st.markdown(calibration_report)
                    st.success("校准报告已保存至云端数据库。")

                    if new_laws:
                        st.markdown("### 📝 待确认的新定律/补丁")
                        selected_new_laws = []
                        for i, law in enumerate(new_laws):
                            col_check, col_law = st.columns([1, 10])
                            with col_check:
                                if st.checkbox("", key=f"new_law_{i}", value=True):
                                    selected_new_laws.append(law)
                            with col_law:
                                st.write(f"**{law.get('name', '新定律')}**")
                                st.write(f"· {law.get('content', '')}")
                                st.write(f"· 触发条件：{law.get('trigger', '')}")
                                st.write(f"· λ值修正：{law.get('lambda_effect', '')}")

                        if st.button("✅ 确认添加新定律"):
                            for i, law in enumerate(selected_new_laws):
                                law["id"] = f"new_{datetime.now().strftime('%Y%m%d%H%M%S')}_{i}"
                                law["status"] = "active"
                                save_law_to_supabase(law)
                            laws_data = load_laws_from_supabase()
                            st.success(f"已添加 {len(selected_new_laws)} 条新定律！")
                            st.rerun()

                    if modified_laws:
                        st.markdown("### ✏️ 待确认的修改建议")
                        selected_modifications = []
                        for i, mod in enumerate(modified_laws):
                            col_check, col_law = st.columns([1, 10])
                            with col_check:
                                if st.checkbox("", key=f"mod_law_{i}", value=True):
                                    selected_modifications.append(mod)
                            with col_law:
                                st.write(f"**修改 ID: {mod.get('id', '未知')}**")
                                st.write(f"· 新名称：{mod.get('name', '')}")
                                st.write(f"· 新逻辑：{mod.get('content', '')}")
                                st.write(f"· 新触发条件：{mod.get('trigger', '')}")
                                st.write(f"· 新λ值修正：{mod.get('lambda_effect', '')}")

                        if st.button("✅ 确认应用修改"):
                            for mod in selected_modifications:
                                for law in laws_data["laws"]:
                                    if str(law["id"]) == str(mod["id"]):
                                        law["name"] = mod["name"]
                                        law["content"] = mod["content"]
                                        law["trigger_condition"] = mod.get("trigger", mod.get("trigger_condition", ""))
                                        law["lambda_effect"] = mod.get("lambda_effect", "")
                                        save_law_to_supabase(law)
                                        break
                            laws_data = load_laws_from_supabase()
                            st.success(f"已应用 {len(selected_modifications)} 条修改！")
                            st.rerun()

                    if not new_laws and not modified_laws:
                        st.info("本次校准未提炼出新定律或修改建议。")

                    st.session_state.search_report = record["search_report"]
                    st.session_state.analysis_report = record["analysis_report"]
                else:
                    st.warning(calibration_report)
            else:
                st.warning("未找到对应的推演记录。请先进行推演并保存记录。")
    elif not st.session_state.analysis_report:
        st.warning("请先进行推演")
    else:
        st.warning("请先进行推演并保存记录")

# ============ 定理卡牌控制台 ============
st.markdown("---")
st.subheader("⚙️ 定理卡牌控制台")

with st.expander("查看/管理所有定律", expanded=False):
    st.info("💡 开关按钮用于控制该定律是否参与赛前推演。关闭后，模型将不会调用该定律。所有修改会自动保存到云端数据库。")

    if laws_data["laws"]:
        for i, law in enumerate(laws_data["laws"]):
            col1, col2 = st.columns([1, 10])
            with col1:
                current_status = law.get("status", "active") == "active"
                new_status = st.toggle(
                    "生效" if current_status else "暂停",
                    value=current_status,
                    key=f"law_toggle_{law['id']}",
                    help="开关控制该定律是否参与推演"
                )
                if new_status != current_status:
                    law["status"] = "active" if new_status else "inactive"
                    save_law_to_supabase(law)
                    st.rerun()

            with col2:
                status_emoji = "🟢" if law.get("status", "active") == "active" else "🔴"
                st.markdown(f"**{status_emoji} {law['name']}**")
                st.markdown(f"· {law['content']}")
                st.markdown(f"· 触发条件：{law.get('trigger_condition', '暂无')}")
                st.markdown(f"· λ值修正：{law.get('lambda_effect', '暂无')}")
            st.divider()
    else:
        st.warning("定律库为空，请先添加定律。")

# ============ 准确率报告面板 ============
st.markdown("---")
st.subheader("📊 准确率报告")

with st.expander("查看推演准确率统计", expanded=True):
    average_score, scored_records = calculate_accuracy()

    if average_score is None:
        st.info("暂无已校准记录，无法计算准确率。请先完成至少一次赛后校准。")
    else:
        col_avg, col_count = st.columns(2)
        with col_avg:
            st.metric("综合平均准确率", f"{average_score} / 100")
        with col_count:
            st.metric("已校准场次", len(scored_records))

        st.markdown("### 按日期查看")
        selected_date = st.date_input("选择日期", value=None)

        if selected_date:
            filtered = [r for r in scored_records if r["timestamp"].startswith(str(selected_date))]
            if filtered:
                date_avg = round(sum(r["score"] for r in filtered) / len(filtered), 1)
                st.metric(f"{selected_date} 准确率", f"{date_avg} / 100")
                for r in filtered:
                    st.write(f"- {r['match']}: {r['score']}分")
            else:
                st.info("该日期无已校准记录。")

        st.markdown("### 最近校准记录")
        for r in scored_records[:5]:
            st.write(f"- {r['match']} ({r['timestamp']}): {r['score']}分")

# ============ 历史记录 ============
st.markdown("---")
st.subheader("📚 历史推演记录")

if st.button("🔧 一键校准所有未校准记录", use_container_width=True):
    cali_count, skip_count = calibrate_all_uncalibrated()
    if cali_count == 0 and skip_count == 0:
        st.info("所有记录都已被校准，暂无未校准记录。")
    else:
        msg = f"成功校准 {cali_count} 条记录。"
        if skip_count > 0:
            msg += f" 跳过 {skip_count} 条未开始比赛。"
        st.success(msg + " 页面将自动刷新。")
        st.rerun()

history = load_history()
if not history:
    st.info("暂无推演记录。")
else:
    for i, rec in enumerate(history):
        title = f"{rec['match']} | {rec['timestamp']}"
        if rec.get("calibration"):
            title += " | 🟢 已校准"
        else:
            title += " | ⚪ 未校准"
        with st.expander(title, expanded=False):
            if st.button(f"📂 加载此记录", key=f"load_{i}"):
                load_record_to_session(rec)
                st.success(f"已加载 {rec['match']} 的推演记录，可以查看或校准。")

            if st.button(f"🗑️ 删除此记录", key=f"delete_{i}"):
                supabase.table("history").delete().eq("id", rec["id"]).execute()
                st.success(f"已删除 {rec['match']} 的记录。")
                st.rerun()

            if rec.get("calibration"):
                if st.button(f"🗑️ 删除校准数据", key=f"clear_cal_{i}"):
                    supabase.table("history").update({
                        "calibration": None,
                        "pending_laws": None,
                        "pending_modifications": None
                    }).eq("id", rec["id"]).execute()
                    st.success(f"{rec['match']} 的校准数据已清空。")
                    st.rerun()

            if not rec.get("calibration"):
                if st.button(f"🔍 校准此记录", key=f"calibrate_{i}"):
                    with st.spinner(f"正在校准 {rec['match']}..."):
                        success, report, new_laws, modified_laws = calibrate_record(rec)
                        if success:
                            st.success(f"{rec['match']} 校准成功！")
                            st.markdown(report)

                            if new_laws:
                                st.markdown("### 📝 待确认的新定律/补丁")
                                selected_new_laws = []
                                for j, law in enumerate(new_laws):
                                    col_check, col_law = st.columns([1, 10])
                                    with col_check:
                                        if st.checkbox("", key=f"hist_new_law_{i}_{j}", value=True):
                                            selected_new_laws.append(law)
                                    with col_law:
                                        st.write(f"**{law.get('name', '新定律')}**")
                                        st.write(f"· {law.get('content', '')}")
                                        st.write(f"· 触发条件：{law.get('trigger', '')}")
                                        st.write(f"· λ值修正：{law.get('lambda_effect', '')}")
                                if st.button("✅ 确认添加新定律", key=f"confirm_new_{i}"):
                                    for j, law in enumerate(selected_new_laws):
                                        law["id"] = f"new_{datetime.now().strftime('%Y%m%d%H%M%S')}_{i}_{j}"
                                        law["status"] = "active"
                                        save_law_to_supabase(law)
                                    laws_data = load_laws_from_supabase()
                                    st.success(f"已添加 {len(selected_new_laws)} 条新定律！")
                                    st.rerun()

                            if modified_laws:
                                st.markdown("### ✏️ 待确认的修改建议")
                                selected_modifications = []
                                for j, mod in enumerate(modified_laws):
                                    col_check, col_law = st.columns([1, 10])
                                    with col_check:
                                        if st.checkbox("", key=f"hist_mod_law_{i}_{j}", value=True):
                                            selected_modifications.append(mod)
                                    with col_law:
                                        st.write(f"**修改 ID: {mod.get('id', '未知')}**")
                                        st.write(f"· 新名称：{mod.get('name', '')}")
                                        st.write(f"· 新逻辑：{mod.get('content', '')}")
                                        st.write(f"· 新触发条件：{mod.get('trigger', '')}")
                                        st.write(f"· 新λ值修正：{mod.get('lambda_effect', '')}")
                                if st.button("✅ 确认应用修改", key=f"confirm_mod_{i}"):
                                    for mod in selected_modifications:
                                        for law in laws_data["laws"]:
                                            if str(law["id"]) == str(mod["id"]):
                                                law["name"] = mod["name"]
                                                law["content"] = mod["content"]
                                                law["trigger_condition"] = mod.get("trigger", mod.get("trigger_condition", ""))
                                                law["lambda_effect"] = mod.get("lambda_effect", "")
                                                save_law_to_supabase(law)
                                                break
                                    laws_data = load_laws_from_supabase()
                                    st.success(f"已应用 {len(selected_modifications)} 条修改！")
                                    st.rerun()

                            if not new_laws and not modified_laws:
                                st.info("本次校准未提炼出新定律或修改建议。")
                            st.rerun()
                        else:
                            st.warning(report)

            st.markdown("### 📡 赛前数据")
            st.markdown(rec["search_report"])
            st.markdown("### 🧠 推演报告")
            st.markdown(rec["analysis_report"])
            if rec.get("calibration"):
                st.markdown("### 📊 校准报告")
                st.markdown(rec["calibration"])
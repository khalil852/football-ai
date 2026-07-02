import streamlit as st
import requests
import json
import os
import re
import math
import hashlib
import random
import string
from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from typing import Dict, Tuple
from supabase import create_client, Client

# =============================================================================
# 所有敏感配置均从 st.secrets 读取。
# 本地开发：放在 .streamlit/secrets.toml（已在 .gitignore，不会上传 GitHub）
# Streamlit Cloud：在 App Settings → Secrets 中配置 TOML 格式
#
# 需要在 secrets 中手动填写的字段：
#   SUPABASE_URL          = "https://xxx.supabase.co"
#   SUPABASE_KEY           = "sb_secret_xxx"          # service_role key
#   default_deepseek_key   = "sk-xxx"                 # UP 主的共享 DeepSeek Key
#   default_tavily_key     = "tvly-xxx"               # UP 主的共享 Tavily Key（可选）
#   Tavily API Key 用于联网搜索数据
#   analysis_prompt        = '''多行文本'''            # 推演 AI 的 system prompt
# =============================================================================

def _secret(key, default=""):
    """统一读取 st.secrets，不存在的 key 静默返回 default"""
    try:
        return st.secrets[key]
    except (KeyError, FileNotFoundError):
        return default

SUPABASE_URL = _secret("SUPABASE_URL")
SUPABASE_KEY = _secret("SUPABASE_KEY")
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

DEFAULT_DEEPSEEK_KEY = _secret("default_deepseek_key")
TAVILY_API_KEY = _secret("default_tavily_key")
TAVILY_URL = "https://api.tavily.com/search"

# ============ System Prompts（优先 secrets，回退本地文件）============
def _load_prompt(secret_name, filename):
    val = _secret(secret_name)
    if val:
        return val
    # 文件仅在本地开发或已上传 GitHub 时可用
    path = os.path.join(os.path.dirname(__file__), filename)
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            return f.read()
    # 兜底：硬编码的空 prompt，避免 crash
    return f"# {secret_name} 未配置，请在 st.secrets 中设置"

system_prompt_search = _load_prompt("search_prompt", "prompt_search.md")
system_prompt_calibrate = _load_prompt("calibrate_prompt", "prompt_calibrate.md")

# ============ 用户认证系统（URL参数持久化版本） ============
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
            st.session_state["auth_token"] = token
            st.session_state["user_id"] = user["id"]
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

def restore_login():
    if st.session_state.get("logged_in"):
        return True
    token = st.query_params.get("auth_token")
    if not token:
        return False
    response = supabase.table("users").select("*").eq("token", token).execute()
    if response.data:
        user = response.data[0]
        st.session_state.logged_in = True
        st.session_state.username = user["username"]
        st.session_state.auth_token = token
        st.session_state.user_id = user["id"]
        return True
    else:
        st.query_params.clear()
        return False

def logout():
    supabase.table("users").update({"token": None}).eq("username", st.session_state.username).execute()
    st.session_state.logged_in = False
    st.session_state.username = ""
    st.session_state.auth_token = ""
    st.session_state.user_id = None
    st.session_state.deepseek_key = ""
    st.query_params.clear()
    st.rerun()

# ============ 页面配置 ============
st.set_page_config(page_title="全维推演工厂", page_icon="⚽", layout="wide")

st.markdown("""
<style>
    .score-card {
        text-align: center; padding: 1.5em; margin-bottom: 1em;
        background: var(--secondary-background-color); border-radius: 12px;
    }
    .score-card .teams { font-size: 1.1rem; font-weight: 500; }
    .score-card .score { font-size: 3rem; font-weight: 700; letter-spacing: 4px; }
    .score-card .probs { font-size: 0.9rem; margin-top: 0.5rem; color: gray; }
    .law-grade { font-size: 0.8em; background: #4a8cff; color: white; padding: 2px 8px; border-radius: 4px; font-weight: 700; }
    .law-grade-s { background: #ff6b35; }
    .law-grade-d { background: #6e7681; }
</style>
""", unsafe_allow_html=True)

# ============ 登录/注册界面 ============
if "logged_in" not in st.session_state:
    st.session_state.logged_in = False
    st.session_state.username = ""
    st.session_state.auth_token = ""
    st.session_state.user_id = None

if not st.session_state.logged_in:
    restore_login()

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

# 每次渲染都确保 auth_token 写入浏览器地址栏（URL），
# 这样 F5 刷新后 st.query_params 能读回 token 恢复登录。
token = st.session_state.get("auth_token", "")
if token:
    st.query_params["auth_token"] = token
    st.components.v1.html(f"""
    <script>
    var u = new URL(window.location);
    if (u.searchParams.get('auth_token') !== '{token}') {{
        u.searchParams.set('auth_token', '{token}');
        window.history.replaceState({{}}, '', u);
    }}
    </script>
    """, height=0)

if st.sidebar.button("🚪 登出", use_container_width=True):
    logout()

# ============ 从 Supabase 读取 API Key（如有） ============
def load_api_keys(username):
    response = supabase.table("api_keys").select("*").eq("username", username).execute()
    if response.data:
        return response.data[0]
    return {"deepseek_key": ""}

def save_api_keys(username, deepseek_key):
    supabase.table("api_keys").upsert({
        "username": username,
        "deepseek_key": deepseek_key
    }, on_conflict="username").execute()

api_keys = load_api_keys(st.session_state.username)

# ============ 初始化 session_state ============
if "deepseek_key" not in st.session_state:
    st.session_state.deepseek_key = api_keys.get("deepseek_key", "")
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

with st.sidebar.expander("🔑 API Key 管理（可选）", expanded=True):
    st.info("💡 如果你有自己的 DeepSeek API Key，可以填在这里。留空则自动使用 UP 主的共享 Key。")
    deepseek_key = st.text_input(
        "你的 DeepSeek API Key",
        type="password",
        value=st.session_state.deepseek_key,
        help="在 platform.deepseek.com 获取。留空则使用 UP 主的 Key。"
    )
    if st.button("💾 保存我的 Key", use_container_width=True):
        if deepseek_key.strip():
            st.session_state.deepseek_key = deepseek_key.strip()
            save_api_keys(st.session_state.username, deepseek_key.strip())
            st.success("你的 API Key 已保存！")
        else:
            st.warning("如果你不想用自己的 Key，请直接留空，系统会使用 UP 主的共享 Key。")

# 显示当前使用的 Key 状态
if st.session_state.deepseek_key:
    st.sidebar.success("✅ 正在使用你自己的 API Key")
else:
    st.sidebar.info("ℹ️ 正在使用 UP 主的共享 API Key")

# ---- 管理工具：定律库兼容性升级 ----
def _upgrade_single_law(law: dict) -> dict:
    """用 AI 将一条定律的 lambda_effect 转换为 modifier_map"""
    prompt = (
        "将以下足球定律的 lambda_effect 文本转换为乘性修正因子 JSON。\n"
        "规则:\n"
        "- 取值为乘性因子，1.0=无影响, 0.70-0.85=严重削弱, 0.86-0.95=轻微削弱, 1.05-1.15=轻微增强, 1.16-1.30=显著增强\n"
        "- 如果原文是加法修正(如 λ-0.3)，假设 λ≈1.5，转为乘性: factor=(1.5+delta)/1.5\n"
        "- key 用英文 snake_case\n"
        "- 仅输出 JSON，不要任何解释\n"
        f"\n定律: {law.get('name','')}\n"
        f"内容: {law.get('content','')}\n"
        f"触发条件: {law.get('trigger','')}\n"
        f"λ效果: {law.get('lambda_effect','')}\n"
    )
    try:
        resp = requests.post(
            url=URL,
            headers={"Authorization": f"Bearer {API_KEY}", "Content-Type": "application/json"},
            json={"model": "deepseek-chat", "messages": [{"role":"user","content": prompt}],
                  "max_tokens": 200, "temperature": 0.0},
            timeout=15
        )
        data = resp.json()
        content = data["choices"][0]["message"].get("content", "")
        m = re.search(r'\{[\s\S]*\}', content)
        if m:
            return json.loads(m.group())
    except Exception:
        pass
    return {}


def _upgrade_all_laws():
    """批量升级定律库兼容性：为每条定律添加 modifier_map 字段"""
    try:
        resp = supabase.table("laws").select("*").or_(
            f"username.eq.{st.session_state.username},username.eq.admin"
        ).execute()
        laws = resp.data or []
        upgraded = 0
        progress = st.progress(0)
        for i, law in enumerate(laws):
            progress.progress((i + 1) / len(laws))
            # 跳过已有 modifier_map 的
            if law.get("modifier_map"):
                continue
            mm = _upgrade_single_law(law)
            if mm:
                supabase.table("laws").update({"modifier_map": mm}).eq("id", law["id"]).execute()
                upgraded += 1
        progress.empty()
        return upgraded
    except Exception as e:
        st.error(f"升级失败: {e}")
        return -1


st.sidebar.markdown("---")
st.sidebar.markdown("### 🎚️ 定律激进程度")

if "law_aggressiveness" not in st.session_state:
    st.session_state["law_aggressiveness"] = 1.0

law_aggressiveness = st.sidebar.slider(
    "修正因子强度",
    min_value=0.5, max_value=1.5, value=1.0, step=0.05,
    help="1.0 = 标准 | <1.0 = 偏保守（因子趋近 1.0）| >1.0 = 偏激进（因子放大）"
)
st.session_state["law_aggressiveness"] = law_aggressiveness
if law_aggressiveness != 1.0:
    label = "保守" if law_aggressiveness < 1.0 else "激进"
    st.sidebar.caption(f"当前: {label}模式 (×{law_aggressiveness:.2f})")

# ---- 管理工具 ----
def _reset_user_laws():
    """删除当前用户所有定律，从 admin 模板重建"""
    try:
        supabase.table("laws").delete().eq("username", st.session_state.username).execute()
        initialize_laws_for_user(st.session_state.username)
        return True
    except Exception as e:
        st.error(f"重置失败: {e}")
        return False


with st.sidebar.expander("🛠️ 管理工具", expanded=False):
    if st.button("🔄 一键升级定律库", use_container_width=True,
                 help="为每条定律添加 modifier_map 字段，兼容新数学引擎"):
        with st.spinner("正在升级定律库..."):
            count = _upgrade_all_laws()
            if count >= 0:
                st.success(f"已为 {count} 条定律添加 modifier_map。")
                st.info("定律库现已兼容新数学引擎。")
                st.rerun()

    st.markdown("---")
    if st.button("🔁 重置我的定律库", use_container_width=True,
                 help="清除你的所有私人定律，从管理员模板重新复制一份"):
        st.warning("你确定要重置吗？你的私人定律和修改将丢失，恢复为 admin 的默认定律。")
        if st.button("✅ 确认重置", key="confirm_reset_my_laws", use_container_width=True):
            if _reset_user_laws():
                st.success("已重置。")
                st.rerun()

# 用户自定义 Key 优先，否则用 UP 主共享 Key
API_KEY = st.session_state.deepseek_key or DEFAULT_DEEPSEEK_KEY
URL = "https://api.deepseek.com/v1/chat/completions"

# ============ 购买API Token入口（预留插槽） ============
with st.sidebar.expander("💰 购买API Token", expanded=False):
    purchase_url = os.getenv("PURCHASE_API_URL", "")
    if purchase_url:
        st.info("🚀 点击下方按钮购买API Token，无需自行注册。")
        if st.button("前往购买", use_container_width=True):
            st.markdown(f"[点击这里购买]({purchase_url})")
    else:
        st.info("📢 API Token 购买功能暂未开放。")

# ============ 从 Supabase 加载定律库 ============
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

def delete_law_from_supabase(law_id):
    try:
        supabase.table("laws").delete().eq("id", law_id).execute()
        return True
    except Exception as e:
        st.error(f"删除定律失败：{e}")
        return False

laws_data = load_laws_from_supabase()

# ============ 时间提取工具函数 ============
def extract_match_time(report_text):
    """从报告中提取开赛时间。支持 YYYY-MM-DD HH:MM, ISO 8601, 带时区后缀"""
    patterns = [
        r'(?:开赛时间|比赛时间|开始时间|Kick[-\s]?off)[：:\s]*(\d{4}[-/]\d{2}[-/]\d{2}[\sT]\d{2}:\d{2}(?:[:]\d{2})?(?:[Z]|[+-]\d{2}[:]?\d{2})?)',
        r'(\d{4}[-/]\d{2}[-/]\d{2}[\sT]\d{2}:\d{2}(?:[:]\d{2})?(?:[Z]|[+-]\d{2}[:]?\d{2})?)',
    ]
    for pattern in patterns:
        m = re.search(pattern, report_text, re.IGNORECASE)
        if m:
            return m.group(1) if m.lastindex else m.group(0)
    return None


def parse_match_time(match_time_str):
    """解析时间字符串为本地时间 datetime。支持多种格式和时区后缀"""
    if not match_time_str:
        return None
    # 标准化
    s = match_time_str.strip()
    # 处理 "Z" 后缀 → +00:00
    has_tz = bool(re.search(r'[Zz]$|[+-]\d{2}[:]?\d{2}$', s))
    if re.search(r'[Zz]$', s):
        s = s[:-1] + "+00:00"
    # 处理没有冒号的时区 (+0800 → +08:00)
    s = re.sub(r'([+-]\d{2})(\d{2})$', r'\1:\2', s)

    # 分离时区和基本时间
    tz_offset = None
    tz_m = re.search(r'([+-]\d{2}:\d{2})$', s)
    if tz_m:
        s = s[:tz_m.start()]
        tz_offset = tz_m.group(1)

    for fmt in [
        "%Y-%m-%d %H:%M",
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%dT%H:%M",
        "%Y-%m-%dT%H:%M:%S",
        "%Y/%m/%d %H:%M",
        "%Y/%m/%d %H:%M:%S",
    ]:
        try:
            dt = datetime.strptime(s, fmt)
            if tz_offset:
                sign = 1 if tz_offset[0] == '+' else -1
                h, m = int(tz_offset[1:3]), int(tz_offset[4:6])
                dt = dt - sign * timedelta(hours=h, minutes=m)  # → UTC
                dt = dt + timedelta(hours=8)  # → 北京时间 (UTC+8)
            return dt
        except:
            continue
    return None


def _now_beijing():
    """返回北京时间（UTC+8）的 datetime。Streamlit Cloud 服务器在 UTC，需要显式 +8"""
    return datetime.utcnow() + timedelta(hours=8)


def get_match_status(match_time_str):
    if not match_time_str:
        return "未知", "⚪"
    match_time = parse_match_time(match_time_str)
    if not match_time:
        return "未知", "⚪"
    now = _now_beijing()
    if now < match_time:
        return "未开赛", "🔵"
    # 比赛时间 + 150 分钟（含加时+点球+赛后 delay）
    elif now < match_time + timedelta(minutes=150):
        return "进行中", "🟡"
    else:
        return "已结束", "🟢"


def can_calibrate(match_time_str):
    """判断是否可以校准。返回 (bool, str)"""
    now = _now_beijing()

    if not match_time_str:
        return False, "⚠️ 未找到开赛时间，无法自动判断。请确认比赛已结束后再手动校准。"

    match_time = parse_match_time(match_time_str)
    if not match_time:
        return False, f"⚠️ 无法解析开赛时间 ({match_time_str})，请确认比赛已结束后再手动校准。"

    earliest_end = match_time + timedelta(minutes=150)

    if now < match_time:
        return False, f"⏳ 比赛尚未开始 ({match_time.strftime('%Y-%m-%d %H:%M')}，北京时间)，请等待开赛后再校准。"
    elif now < earliest_end:
        return False, f"⏳ 比赛仍在进行中 (预计最早 {earliest_end.strftime('%Y-%m-%d %H:%M')} 北京时间结束)，请耐心等待。"
    else:
        return True, f"✅ 比赛已结束，可以校准。"

# ============ 模型分配策略（基于 4x3x2 全模型压力测试）============
# deepseek-chat:     最快轻量模型 (5.2s search, 5.6s analysis), 成本 $0.00012
# deepseek-v4-pro/max: 深度推理，战术推演专用 (33s, 但对复杂战术分析更可靠)
# deepseek-v4-flash:   校准回退 (chat 在 JSON 输出任务上质量不稳定时降级)
MODEL_SEARCH     = {"model": "deepseek-chat"}
MODEL_ANALYSIS   = {"model": "deepseek-v4-pro", "reasoning_effort": "max"}
MODEL_CALIBRATE  = {"model": "deepseek-chat"}
MODEL_EXTRACT    = {"model": "deepseek-chat"}       # _extract_params — 简单提取 λ 和赔率


def _deepseek_chat(system_prompt, user_content, model=None):
    """调用 DeepSeek API，返回响应文本或空字符串。
    model: {"model": "...", "reasoning_effort": "max"|"high"|"low"|None}
    """
    cfg = model or MODEL_SEARCH
    payload = {
        "model": cfg["model"],
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_content}
        ]
    }
    if "reasoning_effort" in cfg:
        payload["reasoning_effort"] = cfg["reasoning_effort"]
        if cfg["reasoning_effort"] == "max":
            payload["max_tokens"] = 6000  # 推理 ~1000-2200t + 输出 ~500t
        elif cfg["reasoning_effort"] == "high":
            payload["max_tokens"] = 4096
        else:
            payload["max_tokens"] = 4096

    response = requests.post(
        url=URL,
        headers={"Authorization": f"Bearer {API_KEY}", "Content-Type": "application/json"},
        json=payload,
        timeout=120 if payload.get("reasoning_effort") == "max" else 90
    )
    data = response.json()
    if 'error' in data:
        st.error(f"API 错误：{data['error']}")
        return ""
    return data['choices'][0]['message'].get('content', '')


# =============================================================================
# 规则引擎 — 确定性定律执行器（纯 Python，不调 LLM）
# 从 Supabase 读取结构化的定律卡片，逐条匹配搜索报告，应用修正因子
# =============================================================================

_RULE_CATEGORIES = {
    "attack": "attack", "defense": "defense", "tactical": "tactical",
    "coach_intent": "coach_intent", "scenario": "scenario", "confidence": "confidence",
}


def _load_rules():
    """从 Supabase 加载当前用户的 active 定律"""
    try:
        resp = st.session_state.get("username")
        if not resp:
            return []
        data = supabase.table("laws").select("*").eq("username", st.session_state.username).eq("status", "active").execute()
        return data.data or []
    except Exception:
        return []


def _run_rules(search_report: str, rules: list, lam_modifiers: dict) -> dict:
    """确定性规则引擎：逐条检查关键词，命中则应用修正。
    rules: [{"category":"attack","trigger_keywords":["伤缺"],"effect_value":0.85,"effect_target":"attack"},...]
    lam_modifiers: {"attack":1.0,"defense":1.0,"tactical":1.0,"coach_intent":1.0,"scenario":1.0,"confidence":1.0}
    返回: {"merged":{...}, "triggered":[...]}
    """
    report_lower = (search_report or "").lower()
    triggered = []
    merged = dict(lam_modifiers)

    for rule in rules:
        keywords = rule.get("trigger_keywords") or []
        if not keywords:
            continue
        # 任意一个关键词命中即触发
        hit = any(kw.lower() in report_lower for kw in keywords if kw)
        if not hit:
            continue

        triggerd_name = rule.get("name", "unknown")
        target = rule.get("effect_target", "attack")
        etype = rule.get("effect_type", "multiply")
        evalue = rule.get("effect_value", 1.0)

        # 目标可以是 attack/defense/tactical/coach_intent/scenario/confidence
        # 也可以是任意自定义键（放入 _extra）
        if target in _RULE_CATEGORIES:
            if etype == "multiply":
                merged[target] *= evalue
            elif etype == "override":
                merged[target] = evalue
        else:
            # 自定义目标：暂存入 extra
            if "_extra" not in merged:
                merged["_extra"] = {}
            merged["_extra"][target] = evalue

        triggered.append(rule.get("name", rule.get("id", "?")))

    return {"merged": merged, "triggered": triggered}


def _lookup_coach(team_name: str) -> dict:
    """查教练库，返回教练数据 dict。表不存在时静默返回空。"""
    if not team_name:
        return {}
    try:
        data = supabase.table("coaches").select("*").eq("team", team_name).execute()
        if data.data:
            return data.data[0]
    except Exception:
        pass  # 表不存在时静默跳过
    return {}


# =============================================================================
# 数学引擎 — 纯 Python 计算，替代 LLM 数值推理
# - 双变量泊松：修复两队进球独立性假设
# - 乘性 λ 修正：替代无数学依据的加法模型
# - 负二项 + 过度离散参数 φ：修复方差=均值约束
# - overround 归一化：修复赔率期望值计算
# - 动态贝叶斯更新：时间/强度自适应 α
# =============================================================================

@dataclass
class LambdaModifiers:
    """乘性修正因子，1.0 = 无修正"""
    attack:       float = 1.0
    defense:      float = 1.0
    tactical:     float = 1.0
    coach_intent: float = 1.0
    scenario:     float = 1.0
    home_adv:     float = 1.08
    confidence:   float = 1.0   # 模型置信度调节（0.7-1.0），用于 "不可预测" 类定律
    _extra:       dict = field(default_factory=dict)  # 用户自定义定律的任意维度

    @classmethod
    def from_merged(cls, merged_modifiers: dict, home_adv: bool = True):
        """从 AI 输出的 merged_modifiers 动态构造。兼容任意用户定律。"""
        known = {"attack", "defense", "tactical", "coach_intent", "scenario", "confidence"}
        kwargs = {"home_adv": 1.08 if home_adv else 1.0}
        extra = {}
        for k, v in merged_modifiers.items():
            try:
                v = float(v)
                v = max(0.7, min(1.3, v))  # 拒绝荒谬值：修正因子合理范围 [0.7, 1.3]
            except (TypeError, ValueError):
                continue
            if k in known:
                kwargs[k] = v
            elif k == "home_adv":
                kwargs["home_adv"] = v
            else:
                extra[k] = v
        return cls(**kwargs, _extra=extra)

    def apply(self, lam: float, is_home: bool = False) -> float:
        f = self.attack * self.defense * self.tactical * self.coach_intent * self.scenario
        if is_home:
            f *= self.home_adv
        for v in self._extra.values():
            f *= v
        return max(0.05, lam * f)


@dataclass
class MatchPrediction:
    home_team: str = ""
    away_team: str = ""
    lam_h: float = 0.0
    lam_a: float = 0.0
    lam_c: float = 0.02
    phi: float = 0.15
    home_win: float = 0.0
    draw: float = 0.0
    away_win: float = 0.0
    exp_h: float = 0.0
    exp_a: float = 0.0
    top_scores: list = field(default_factory=list)
    confidence: float = 0.0
    locked_h: int = 0
    locked_a: int = 0
    # 淘汰赛专有：加时+点球后的晋级概率
    is_knockout: bool = False
    home_advance: float = 0.0   # 主队最终晋级概率
    away_advance: float = 0.0   # 客队最终晋级概率
    extra_time_pct: float = 0.0  # 进入加时的概率
    penalties_pct: float = 0.0   # 进入点球的概率
    et_score_h: int = 0          # 加时赛主队进球
    et_score_a: int = 0          # 加时赛客队进球
    pen_score_h: int = 0         # 点球主队进球
    pen_score_a: int = 0         # 点球客队进球


@dataclass
class CalibrationResult:
    accuracy_score: float = 0.0
    score_match: bool = False
    result_match: bool = False
    goal_deviation: float = 0.0
    overround: float = 0.0


# ---- 双变量泊松 ----
def _bivariate_poisson(lam_h: float, lam_a: float, lam_c: float,
                       max_g: int = 6) -> Dict[Tuple[int, int], float]:
    probs, total = {}, 0.0
    for h in range(max_g + 1):
        for a in range(max_g + 1):
            k_max = min(h, a)
            s = sum((lam_h ** (h - k) / math.factorial(h - k)) *
                    (lam_a ** (a - k) / math.factorial(a - k)) *
                    (lam_c ** k / math.factorial(k)) for k in range(k_max + 1))
            p = math.exp(-(lam_h + lam_a + lam_c)) * s
            probs[(h, a)] = p
            total += p
    return {k: v / total for k, v in probs.items()} if total else probs


# ---- 负二项概率（过度离散）----
def _neg_binom_p(lam: float, k: int, phi: float = 0.15) -> float:
    sigma_sq = lam + phi * lam * lam
    r = lam * lam / max(0.001, sigma_sq - lam)
    p_s = lam / sigma_sq
    return (math.gamma(r + k) / (math.gamma(r) * math.factorial(k)) *
            (1 - p_s) ** r * p_s ** k)


# ---- 赔率 overround 归一化 ----
def _implied_probs(odds_h: float, odds_d: float, odds_a: float) -> Tuple[float, float, float, float]:
    raw = [1.0 / odds_h, 1.0 / odds_d, 1.0 / odds_a]
    overround = sum(raw)
    return raw[0] / overround, raw[1] / overround, raw[2] / overround, overround


# ---- 调参层（含所有启发式补丁）----
def _adjust_lambda(lh: float, la: float, is_knockout: bool = False) -> Tuple[float, float]:
    """调整 λ 值。所有防平局/阈值修正集中在此层，不污染底层数学。"""
    if not is_knockout:
        diff = lh - la
        if diff < 0.08:
            add = 0.18
            if lh > la: lh += add; la = max(0.7, la - add * 0.3)
            else: la += add; lh = max(0.7, lh - add * 0.3)
        elif diff < 0.45:
            add = 0.18
            lh += add; la = max(0.7, la - add * 0.6)
    return lh, la


# ---- 底层：标准泊松（不可变，零补丁）----
def _std_poisson(lam_h: float, lam_a: float, max_g: int = 8) -> dict:
    """纯数学泊松联乘。输出 { (h,a): prob }，不入任何启发式。"""
    probs = {}
    for h in range(max_g + 1):
        ph = math.exp(-lam_h) * lam_h**h / math.factorial(h)
        for a in range(max_g + 1):
            pa = math.exp(-lam_a) * lam_a**a / math.factorial(a)
            probs[(h, a)] = ph * pa
    total = sum(probs.values())
    return {k: v / total for k, v in probs.items()} if total > 0 else probs


# ---- 底层：双变量泊松 + 过度离散（淘汰赛专用）----
def _full_model(lh: float, la: float, lam_c: float = 0.01, phi: float = 0.20, max_g: int = 8) -> dict:
    probs = _bivariate_poisson(lh, la, lam_c, max_g)
    if phi > 0.01:
        adj, total = {}, 0.0
        for (h, a), p in probs.items():
            nb_h = max(1e-10, _neg_binom_p(lh, h, phi))
            nb_a = max(1e-10, _neg_binom_p(la, a, phi))
            po_h = max(1e-10, math.exp(-lh) * lh**h / math.factorial(h))
            po_a = max(1e-10, math.exp(-la) * la**a / math.factorial(a))
            adj[(h, a)] = p * (nb_h / po_h) ** 0.5 * (nb_a / po_a) ** 0.5
            total += adj[(h, a)]
        if total > 0:
            return {k: v / total for k, v in adj.items()}
    return probs


# ---- 核心推演（上层调参 + 底层数学，分层清晰）----
def predict_match(home: str, away: str, lam_h0: float, lam_a0: float,
                  mod: LambdaModifiers, odds: Tuple[float, float, float] = None,
                  lam_c: float = 0.01, phi: float = 0.20, max_g: int = 8,
                  is_knockout: bool = False) -> MatchPrediction:
    lh = mod.apply(lam_h0, is_home=True)
    la = mod.apply(lam_a0, is_home=False)
    # 标准独立泊松，零补丁
    probs = {}
    for h in range(max_g + 1):
        ph = math.exp(-lh) * lh**h / math.factorial(h)
        for a in range(max_g + 1):
            pa = math.exp(-la) * la**a / math.factorial(a)
            probs[(h, a)] = ph * pa
    total = sum(probs.values())
    if total > 0:
        probs = {k: v / total for k, v in probs.items()}

    hw = dw = aw = eh = ea = 0.0
    for (h, a), p in probs.items():
        eh += h * p; ea += a * p
        if h > a:      hw += p
        elif h == a:   dw += p
        else:          aw += p

    top = sorted(probs.items(), key=lambda x: x[1], reverse=True)[:5]
    locked_h, locked_a = top[0][0] if top else (round(eh), round(ea))

    conf = 1.0
    if odds:
        imp_h, imp_d, imp_a, _ = _implied_probs(*odds)
        conf = max(0.0, 1.0 - 0.5 * (abs(hw - imp_h) + abs(dw - imp_d) + abs(aw - imp_a)))

    return MatchPrediction(home_team=home, away_team=away,
                           lam_h=lh, lam_a=la, lam_c=lam_c, phi=phi,
                           home_win=hw, draw=dw, away_win=aw,
                           exp_h=eh, exp_a=ea, top_scores=top, confidence=conf,
                           locked_h=locked_h, locked_a=locked_a,
                           is_knockout=is_knockout)

# ---- 赛后校准 ----
def calibrate_math(pred: MatchPrediction, actual_h: int, actual_a: int,
                   is_knockout: bool = False, actual_home_adv: bool = None,
                   is_et_or_pens: bool = False) -> CalibrationResult:
    pred_h = pred.locked_h
    pred_a = pred.locked_a

    # 淘汰赛且推演平局：比分命中按 90 分钟判，额外算晋级命中
    score_match = (pred_h == actual_h and pred_a == actual_a)
    pred_r = "home" if pred.home_win > max(pred.draw, pred.away_win) else \
             "draw" if pred.draw > max(pred.home_win, pred.away_win) else "away"
    actual_r = "home" if actual_h > actual_a else "draw" if actual_h == actual_a else "away"
    result_match = (pred_r == actual_r)

    # 淘汰赛特殊计分：如果推演平局且 90 分钟确实平局，即使 actual 比分不同（ET 进球），
    # 只扣偏差分，不扣"胜负预测错误"的 25 分
    knockout_adv_bonus = 0
    if is_knockout and pred.is_knockout and pred.locked_h == pred.locked_a:
        if actual_h == actual_a and actual_home_adv is not None:
            # 90 分钟确实平局 → 晋级预测准确度加分
            pred_home_adv = pred.home_advance > 0.5
            if pred_home_adv == actual_home_adv:
                knockout_adv_bonus = 15  # 晋级预测命中，+15 分

    deviation = abs(pred_h - actual_h) + abs(pred_a - actual_a)
    score = max(0, min(100, 100 - deviation * 15 - (0 if result_match else 25) +
                   knockout_adv_bonus - (0 if pred.confidence >= 0.5 else 10)))
    return CalibrationResult(accuracy_score=round(score, 1),
                             score_match=score_match, result_match=result_match,
                             goal_deviation=round(deviation, 2))

def _run_law_engine(search_report: str, laws: list, match_query: str = "") -> dict:
    """运行规则引擎: 确定性关键词匹配 + 教练库查表。"""
    modifiers = {"attack": 1.0, "defense": 1.0, "tactical": 1.0,
                 "coach_intent": 1.0, "scenario": 1.0, "confidence": 1.0}
    triggered = []

    # Tier 1: 查教练库（队名中→英映射，从用户输入的 match_query 取队名）
    _TEAM_EN = {
        "挪威":"Norway","法国":"France","巴西":"Brazil","阿根廷":"Argentina",
        "英格兰":"England","德国":"Germany","西班牙":"Spain","葡萄牙":"Portugal",
        "荷兰":"Netherlands","比利时":"Belgium","克罗地亚":"Croatia","乌拉圭":"Uruguay",
        "墨西哥":"Mexico","美国":"USA","加拿大":"Canada","塞内加尔":"Senegal",
        "日本":"Japan","韩国":"South Korea","澳大利亚":"Australia","伊朗":"Iran",
        "卡塔尔":"Qatar","沙特":"Saudi Arabia","加纳":"Ghana","突尼斯":"Tunisia",
        "埃及":"Egypt","瑞典":"Sweden","瑞士":"Switzerland","丹麦":"Denmark",
        "土耳其":"Turkey","捷克":"Czechia","苏格兰":"Scotland","科特迪瓦":"Ivory Coast",
        "南非":"South Africa","海地":"Haiti","巴拿马":"Panama","新西兰":"New Zealand",
        "哥伦比亚":"Colombia","厄瓜多尔":"Ecuador","巴拉圭":"Paraguay","奥地利":"Austria",
        "摩洛哥":"Morocco","阿尔及利亚":"Algeria","波黑":"Bosnia-Herzegovina",
        "刚果":"DR Congo","佛得角":"Cape Verde","乌兹别克":"Uzbekistan",
        "伊拉克":"Iraq","库拉索":"Curacao","约旦":"Jordan",
    }
    # 优先用 match_query 的 vs 格式提取队名，回退到搜索报告
    team1, team2 = _parse_teams(match_query) if match_query else (None, None)
    if not team1:
        team1, team2 = _parse_teams(search_report)
    for team_cn, is_home in [(team1, True), (team2, False)]:
        if not team_cn: continue
        team_en = _TEAM_EN.get(team_cn, team_cn)
        coach = _lookup_coach(team_en)
        if coach:
            if is_home:
                modifiers["coach_intent"] *= float(coach.get("aggression", 1.0))
            modifiers["confidence"] *= float(coach.get("confidence", 1.0))

    # Tier 2: 确定性规则引擎
    active_rules = [l for l in laws if l.get("status", "active") == "active" and l.get("trigger_keywords")]
    if active_rules and search_report:
        result = _run_rules(search_report, active_rules, modifiers)
        modifiers = result["merged"]
        triggered = result["triggered"]

    # 用户激进程度
    aggro = st.session_state.get("law_aggressiveness", 1.0)
    if aggro != 1.0:
        for k in ("attack", "defense", "tactical", "coach_intent", "scenario", "confidence"):
            modifiers[k] = 1.0 + (modifiers[k] - 1.0) * aggro

    if triggered:
        st.info(f"📋 触发 {len(triggered)} 条定律: {', '.join(triggered[:5])}")
    st.session_state["last_triggered_laws"] = triggered

    return {"merged_modifiers": modifiers, "triggered": triggered}


# ---- 回退模式 prompt（定律库加载失败时使用）----
_PARAM_EXTRACT_PROMPT = (
    "你是一个数据提取器。从以下赛前数据报告中提取结构化参数，仅输出 JSON。\n"
    "字段: home_team, away_team, lam_h_initial, lam_a_initial, "
    "attack, defense, tactical, coach_intent, scenario, phi, lam_c, "
    "home_adv, odds_h, odds_d, odds_a"
)

def _extract_params(search_report: str, laws: list = None, match_query: str = "") -> dict:
    """提取参数：规则引擎（确定性）+ LLM（λ/赔率）"""
    params = {}

    # Tier 1: 规则引擎 + 教练库（零 LLM 成本）
    rule_result = _run_law_engine(search_report, laws or [], match_query)
    rule_merged = rule_result.get("merged_modifiers", {})
    params["merged_modifiers"] = rule_merged
    params["triggered"] = rule_result.get("triggered", [])

    # Tier 2: LLM 提取 λ 和赔率（deepseek-chat，便宜）
    try:
        prompt = (
            "从赛前数据报告中提取两队名和λ初始值。仅输出JSON:\n"
            '{"home_team":"","away_team":"","lam_h_initial":1.5,"lam_a_initial":1.2,'
            '"odds_h":null,"odds_d":null,"odds_a":null}\n'
            "λ必须在0.8-3.5之间。报告：\n" + search_report[:6000]
        )
        payload = {"max_tokens": 300, "temperature": 0.0}
        payload.update(MODEL_EXTRACT)
        payload.update({"messages": [{"role": "user", "content": prompt}]})
        resp = requests.post(url=URL, headers={"Authorization": f"Bearer {API_KEY}", "Content-Type": "application/json"}, json=payload, timeout=20)
        data = resp.json()
        if "error" not in data:
            content = data["choices"][0]["message"].get("content", "")
            m = re.search(r'\{[\s\S]*\}', content)
            if m:
                llm = json.loads(m.group())
                params.update(llm)
    except Exception:
        pass

    return params


# ---- 推演报告 prompt（教练意图 L1-L5 必须是第一步）----
_ANALYSIS_FROM_JSON_PROMPT = (
    "将 Python 数学引擎计算结果格式化为推演报告。\n"
    "**硬性规则**\n"
    "- 锁定比分必须原样引用 JSON 的「锁定比分」字段，一字不改\n"
    "- 期望进球、胜负概率也直接引用 JSON 数值\n"
    "- 不要写「我认为」「分析师判断」等主观表述\n"
    "- 报告结构如下（总分不超过 400 字）:\n"
    "\n"
    "### 🎯 推演比分\n"
    "**(球队A)X:X(球队B)**  90分钟常规时间\n"
    "例: (法国)1:1(塞内加尔)\n"
    "\n"
    "[淘汰赛推演平局时, 在下方增加一行:]\n"
    "**(球队A)X(X):(X)X(球队B)**  (含加时/点球)\n"
    "例: (法国)1(3):(4)1(塞内加尔)\n"
    "注: 括号内为点球比分, 4-3\n"
    "\n"
    "## 教练意图评级\n"
    "| 球队 | 评级 | 依据 |\n"
    "|------|------|------|\n"
    "| 主队 | L1-L5 | 基于赛前发言和首发阵容, 用1句话写明理由 |\n"
    "| 客队 | L1-L5 | 同上 |\n\n"
    "L1=极度保守/轮换替补 | L2=谨慎防守反击 | L3=均衡 | "
    "L4=主动进攻 | L5=全力压上/生死战\n"
    "⚠️ 必须从赛前数据报告中的「教练发言」和「首发阵容」找到依据, 找不到则标 L3\n"
    "\n"
    "### ⏱ 关键时间窗口\n"
    "[基于双方教练意图和战术体系, 推演最可能进球的时间段]\n"
    "\n"
    "### 概率\n"
    "主胜 X% | 平 X% | 客胜 X% | 置信度 X%\n"
    "\n"
    "[如果是淘汰赛，增加一行:]\n"
    "### 🏆 晋级概率\n"
    "主队晋级 X% | 客队晋级 X% | 加时概率 X% | 点球概率 X%\n"
    "\n"
    "### 修正摘要\n"
    "[1-2句话说明触发了哪些修正因子及其影响]\n"
    "\n"
    "<details><summary>📊 完整数据</summary>\n"
    "修正因子: [key=value 列表]\n"
    "比分概率: [top5 列表]\n"
    "λ主: X.XX | λ客: X.XX\n"
    "</details>"
)


# ---- 校准报告 prompt（同上）----
_CALIBRATE_FROM_JSON_PROMPT = (
    "将校准结果格式化为报告。\n"
    "**硬性规则**\n"
    "- 评分直接引用，一字不改\n"
    "- 结构:\n"
    "  ### 准确率: XX/100\n"
    "  <small>比分命中: Y/N | 胜负命中: Y/N | 偏差: X.X球</small>\n"
    "  ### 差异\n"
    "  ✅ [被验证的逻辑, 每条 ≤1 句]\n"
    "  ⚠️ [被推翻的逻辑, 每条 ≤1 句, 含根因]\n"
    "  <details><summary>📎 定律与术语</summary>\n"
    "  新定律 JSON + 修改建议 JSON\n"
    "  </details>\n"
    "- 总字数 ≤300"
)

# （旧版 _CALIBRATE_FROM_JSON_PROMPT 已移除，统一使用上方精简版）


# ============ 工具函数：从查询提取两队名 ============
def _parse_teams(query):
    """从查询中提取两个队名，返回 (team1, team2) 或 (None, None)"""
    for sep in [r'\s+vs\s+', r'\s+v\s+', r'\s+对阵\s+', r'\s+对\s+']:
        parts = re.split(sep, query, flags=re.IGNORECASE)
        if len(parts) == 2:
            left = [w for w in parts[0].split() if re.search(r'[一-鿿]|[a-zA-Z]', w)]
            righ = [w for w in parts[1].split() if re.search(r'[一-鿿]|[a-zA-Z]', w)]
            if left and righ:
                t1, t2 = left[-1], righ[0]
                if t1 != t2:
                    return t1, t2
    return None, None


# ============ Tavily 搜索 ============
@st.cache_data(ttl=3600, show_spinner=False)
def _cached_search(query):
    """缓存搜索结果，相同 query 1 小时内不重复搜索"""
    return _tavily_search(query)


def _tavily_search(query):
    """Tavily 搜索，返回格式化结果"""
    try:
        if not TAVILY_API_KEY:
            return "搜索未返回有效结果。"
        resp = requests.post(
            TAVILY_URL,
            headers={"Content-Type": "application/json"},
            json={"api_key": TAVILY_API_KEY, "query": query, "search_depth": "basic", "max_results": 5},
            timeout=15
        )
        results = []
        for item in resp.json().get("results", []):
            c = item.get("content", "")
            if len(c) > 10:
                results.append(f"- {item.get('title', '')}: {c}")
        return "\n".join(results) if results else "搜索未返回有效结果。"
    except Exception:
        return "搜索未返回有效结果。"


def _search_with_tavily(system_prompt, user_query, search_mode="pre_match", model=None):
    """百度搜索 + DeepSeek 汇总"""
    # extract clean match name
    search_target = user_query
    for sep in [r'\s+vs\s+', r'\s+v\s+', r'\s+对阵\s+', r'\s+对\s+']:
        parts = re.split(sep, user_query, flags=re.IGNORECASE)
        if len(parts) == 2:
            left = [w for w in parts[0].split() if re.search(r'[一-鿿]|[a-zA-Z]', w)]
            righ = [w for w in parts[1].split() if re.search(r'[一-鿿]|[a-zA-Z]', w)]
            if left and righ:
                search_target = f"{left[-1]} vs {righ[0]}"
                break

    if search_mode == "pre_match":
        search_rounds = [
            f"{search_target} 2026世界杯 阵容 伤病 首发预测",
            f"{search_target} 赔率 裁判 赛前新闻 教练赛前发言",
            f"{search_target} 历史交锋 出线形势 战术分析 关键球员",
        ]
    else:
        search_rounds = [
            f"{search_target} 最终比分 进球者 进球时间 2026",
            f"{search_target} 赛后技术统计 射门 控球率 角球",
            f"{search_target} 赛后报告 比赛回顾 关键事件 红黄牌",
        ]

    all_results = ""
    with ThreadPoolExecutor(max_workers=len(search_rounds)) as ex:
        futures = [ex.submit(_cached_search, q) for q in search_rounds]
        for f in as_completed(futures):
            r = f.result()
            if r and "搜索失败" not in r:
                all_results += r + "\n"

    if not all_results:
        return ""

    override = ""
    if search_mode == "post_match":
        override = (
            "\n\n**【硬性规则】这是一场已经结束的正式比赛。即使搜索数据不够完整，"
            "你必须基于你训练数据中对这场比赛的记忆，给出最终比分、进球者和关键事件。"
            "严禁输出「比赛尚未开始」或「数据未更新」。如果完全找不到数据，标注「该场比赛数据暂无记录」。**"
        )

    return _deepseek_chat(
        system_prompt,
        f"""以下是多轮搜索结果，请综合分析并严格按照模板格式输出报告。

{user_query}

搜索结果：
{all_results}
{override}

注意：所有结果必须基于以上搜索数据。不确定的信息标注"暂无"。"""
    ,
        model=model)


# ============ 统一搜索入口 ============

def call_deepseek(system_prompt, user_query, enable_search=False, search_mode="pre_match", model=None):
    """调用 DeepSeek API。enable_search=True 时 Tavily 搜索 + 汇总"""
    if not API_KEY:
        st.error("API Key 未配置，请联系 UP 主。")
        return ""
    if not enable_search:
        return _deepseek_chat(system_prompt, user_query, model=model)
    result = _search_with_tavily(system_prompt, user_query, search_mode, model=model)
    if result:
        return result
    return _deepseek_chat(
        system_prompt,
        "[数据源不可用] 利用训练数据回答，不确定标注暂无。\n\n" + user_query,
        model=model
    )


def save_record(match, search_report, analysis_report):
    match_time = extract_match_time(search_report)
    is_training = st.session_state.get("training_mode", False)
    pred = st.session_state.get("math_prediction")
    record = {
        "username": st.session_state.username,
        "match": match,
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "search_report": search_report,
        "analysis_report": analysis_report,
        "calibration": None,
        "match_time": match_time,
        "training_mode": is_training,
        "predicted_score": f"{pred.locked_h}-{pred.locked_a}" if pred else None,
    }
    try:
        supabase.table("history").insert(record).execute()
    except Exception:
        # training_mode 列可能不存在（旧数据库），去掉后重试
        record.pop("training_mode", None)
        supabase.table("history").insert(record).execute()

def load_history():
    response = supabase.table("history").select("*").eq("username", st.session_state.username).order("timestamp", desc=True).execute()
    return response.data

def load_record_to_session(record):
    st.session_state.search_report = record["search_report"]
    st.session_state.analysis_report = record["analysis_report"]
    st.session_state.current_match = record["match"]
    st.session_state.current_record_id = record["id"]
    st.session_state.current_match_time = record.get("match_time")
    st.session_state.training_mode = record.get("training_mode", False)
    st.session_state.math_prediction = None  # 历史记录无 math 对象，校准时仅用 LLM 模式

def calibrate_record(record, max_attempts=3):
    # ---- 第一步：获取赛后数据 ----
    # 从搜索报告中提取对阵和比赛信息，作为赛后搜索的上下文
    search_report = record.get("search_report", "")
    match_name = record.get("match", "比赛")
    # 从搜索报告中提取球队英文名（如果有），让搜索更精准
    lines = search_report[:2000].strip().split("\n")
    context_lines = [l.strip() for l in lines if l.strip() and len(l.strip()) > 10][:5]
    match_context = f"对阵: {match_name}\n" + ("\n".join(context_lines) if context_lines else "")

    # 中文→英文队名映射（校准时需要搜英文才能命中）
    _CALIB_TEAMS = {
        "法国":"France","德国":"Germany","巴西":"Brazil","阿根廷":"Argentina",
        "英格兰":"England","西班牙":"Spain","葡萄牙":"Portugal","荷兰":"Netherlands",
        "比利时":"Belgium","克罗地亚":"Croatia","乌拉圭":"Uruguay","墨西哥":"Mexico",
        "美国":"USA","加拿大":"Canada","塞内加尔":"Senegal","摩洛哥":"Morocco",
        "日本":"Japan","韩国":"South Korea","澳大利亚":"Australia","伊朗":"Iran",
        "卡塔尔":"Qatar","沙特":"Saudi Arabia","加纳":"Ghana","突尼斯":"Tunisia",
        "埃及":"Egypt","阿尔及利亚":"Algeria","哥伦比亚":"Colombia","厄瓜多尔":"Ecuador",
        "巴拉圭":"Paraguay","瑞典":"Sweden","挪威":"Norway","瑞士":"Switzerland",
        "奥地利":"Austria","土耳其":"Turkey","捷克":"Czechia","苏格兰":"Scotland",
        "科特迪瓦":"Ivory Coast","南非":"South Africa","海地":"Haiti","巴拿马":"Panama",
        "刚果":"DR Congo","佛得角":"Cape Verde","乌兹别克":"Uzbekistan",
        "约旦":"Jordan","伊拉克":"Iraq","新西兰":"New Zealand","库拉索":"Curacao","波黑":"Bosnia",
    }
    for attempt in range(max_attempts):
        # 提取中英文队名 → 统一转英文
        teams = _parse_teams(match_name)
        if teams and teams[0] and teams[1]:
            en_h = _CALIB_TEAMS.get(teams[0], teams[0])
            en_a = _CALIB_TEAMS.get(teams[1], teams[1])
            clean_target = f"{en_h} vs {en_a}"
        else:
            clean_target = match_name
        search_rounds = [
            f'"{clean_target}" final score result goalscorers match report 2026',
            f'"{clean_target}" match statistics possession shots cards',
            f"{clean_target} 最终比分 进球者 赛后技术统计 2026",
        ]
        raw_search = ""
        for q in search_rounds:
            r = _cached_search(q)
            if r and "搜索未返回有效结果" not in r and "搜索失败" not in r:
                raw_search += r + "\n"

        if not raw_search:
            if attempt < max_attempts - 1:
                continue
            # 最后一次：模型知识兜底
            post_match_data = _deepseek_chat(
                system_prompt_calibrate,
                f"**【硬性规则】{match_name} 是已结束的比赛。直接给出最终比分、进球者、关键事件。**",
                model=MODEL_CALIBRATE,
            )
            if "比赛尚未开始" in post_match_data:
                return False, post_match_data, [], []
        else:
            post_match_data = _deepseek_chat(
                system_prompt_calibrate,
                f"以下是为 {clean_target} 搜索到的赛后数据，请汇总成报告。\n\n{raw_search}\n\n{match_context}",
                model=MODEL_CALIBRATE,
            )
        if not any(kw in post_match_data for kw in [
            "比赛尚未开始", "赛后数据未更新", "未返回有效结果", "搜索失败", "无法找到"
        ]):
            break
        if attempt == max_attempts - 1:
            post_match_data = _deepseek_chat(
                system_prompt_calibrate,
                f"**【硬性规则】{match_name} 是已结束的比赛。赛前数据：\n{match_context}\n"
                f"直接给出最终比分、进球者、关键事件。**",
                model=MODEL_CALIBRATE,
            )
            if "比赛尚未开始" in post_match_data:
                return False, post_match_data, [], []
        else:
            continue
        break

    # ---- 第二步：从赛后数据提取实际比分 ----
    # 直接让 LLM 提取比分（比正则更可靠）
    is_extra_time = bool(re.search(r'extra.time|aet|加时|after.extra|ET\b', post_match_data, re.IGNORECASE))
    is_penalty_match = bool(re.search(r'penal|pen\b|shootout|点球', post_match_data.lower()))

    actual_h = actual_a = None
    try:
        score_prompt = (
            "从赛后报告中提取90分钟常规时间的最终比分。仅输出JSON: {\"actual_h\":2,\"actual_a\":1}\n"
            "报告：\n" + post_match_data[:4000]
        )
        score_resp = requests.post(URL,
            headers={"Authorization": f"Bearer {API_KEY}", "Content-Type": "application/json"},
            json={"model": "deepseek-chat", "messages": [{"role":"user","content":score_prompt}],
                  "max_tokens": 100, "temperature": 0.0},
            timeout=15
        )
        if "error" not in score_resp.json():
            score_text = score_resp.json()["choices"][0]["message"].get("content", "")
            sm = re.search(r'\{[\s\S]*\}', score_text)
            if sm:
                sp = json.loads(sm.group())
                actual_h = sp.get("actual_h")
                actual_a = sp.get("actual_a")
    except Exception:
        pass

    # 回退：正则
    if actual_h is None or actual_a is None:
        clean = re.sub(r'\([^)]*\d+[^)]*\)', '', post_match_data)
        scores = re.findall(r'(\d+)\s*[-:]\s*(\d+)', clean)
        actual_h, actual_a = (int(scores[0][0]), int(scores[0][1])) if scores else (0, 0)

    cal_h, cal_a = actual_h, actual_a
    if cal_h is None or cal_a is None:
        cal_h, cal_a = 0, 0

    # ---- 第三步：Python 数学引擎校准 ----
    math_cal = None
    pred = st.session_state.get("math_prediction")
    # 从历史记录恢复推演比分
    if not pred and record.get("predicted_score"):
        pred_parts = record["predicted_score"].split("-")
        if len(pred_parts) == 2:
            ph, pa = int(pred_parts[0]), int(pred_parts[1])
            # 根据推演比分推断胜负偏向
            if ph > pa:      hw, dw, aw = 0.6, 0.24, 0.16
            elif ph == pa:   hw, dw, aw = 0.30, 0.45, 0.25
            else:            hw, dw, aw = 0.16, 0.24, 0.6
            pred = MatchPrediction(locked_h=ph, locked_a=pa,
                                   home_win=hw, draw=dw, away_win=aw, is_knockout=False)
    if pred and cal_h is not None and cal_a is not None:
        # 淘汰赛：判断实际晋级方
        detected_adv = None
        if pred.is_knockout:
            if is_extra_time or is_penalty_match:
                # 有加时/点球 → 90 分钟平局，从文本判断谁晋级
                detected_adv = bool(re.search(
                    rf'{re.escape(pred.home_team)}.{{0,20}}(?:win|advance|晋级|through|qualif)',
                    post_match_data, re.IGNORECASE))
                if not detected_adv and not re.search(
                    rf'{re.escape(pred.away_team)}.{{0,20}}(?:win|advance|晋级|through|qualif)',
                    post_match_data, re.IGNORECASE):
                    detected_adv = None  # 无法判断
            else:
                detected_adv = cal_h > cal_a
        math_cal = calibrate_math(pred, int(cal_h), int(cal_a),
                                  is_knockout=pred.is_knockout,
                                  actual_home_adv=detected_adv,
                                  is_et_or_pens=(is_extra_time or is_penalty_match))

    # ---- 第四步：LLM 生成校准报告 + 定律提炼 ----
    all_laws = laws_data["laws"]
    laws_text = json.dumps(all_laws, ensure_ascii=False, indent=2)

    math_block = ""
    if math_cal:
        math_block = (
            f"\n\n【Python 数学引擎校准结果】\n"
            f"准确率评分: {math_cal.accuracy_score}/100\n"
            f"推演比分(90分钟): {pred.locked_h} - {pred.locked_a} → 实际90分钟: {cal_h} - {cal_a}\n"
            f"进球偏差: {math_cal.goal_deviation}球\n"
            f"比分命中: {'是' if math_cal.score_match else '否'} | "
            f"胜负命中: {'是' if math_cal.result_match else '否'}\n"
        )
        if pred.is_knockout:
            # 淘汰赛额外校验：晋级预测是否准确
            pen_msg = " (含点球)" if is_penalty_match else ""
            # 实际晋级方：优先用文本检测（step 3），回退到比分判断
            actual_home_adv = detected_adv if detected_adv is not None else (cal_h > cal_a)
            if pred.locked_h == pred.locked_a:
                # 推演为平局 → 走过加时/点球推演
                pred_home_adv = pred.home_advance > 0.5
                adv_hit = "是" if actual_home_adv == pred_home_adv else "否"
                et_draw = (pred.et_score_h == pred.et_score_a)
                fw = pred.home_team if pred.home_advance > 0.5 else pred.away_team
                chain = f"90分 {pred.locked_h}-{pred.locked_a} → 加时 {pred.et_score_h}-{pred.et_score_a}"
                if et_draw:
                    chain += f"(平) → 点球 {pred.pen_score_h}-{pred.pen_score_a}"
                chain += f" → {fw}晋级"
                math_block += (
                    f"【淘汰赛】推演链: {chain}\n"
                    f"  实际晋级: {actual_home_adv}{pen_msg} | 晋级预测命中: {adv_hit}\n"
                )
            else:
                # 推演 90 分钟分出胜负 → 无加时推演
                pred_winner = pred.home_team if pred.locked_h > pred.locked_a else pred.away_team
                adv_hit = "是" if (pred.locked_h > pred.locked_a) == actual_home_adv else "否"
                math_block += (
                    f"【淘汰赛】推演 90 分钟决胜 ({pred_winner} 胜) → 无加时\n"
                    f"  实际晋级: {actual_home_adv}{pen_msg} | 胜负预测命中: {adv_hit}\n"
                )

    summary_prompt = f"""你是赛后分析师。进行偏差分析并提炼新定律。

## 规则
1. 赛后数据为准，严禁编造比分
2. 不确定标「暂无」

## 赛后数据
{post_match_data[:6000]}

## 赛前推演
推演比分: {f'{pred.locked_h}-{pred.locked_a}' if pred else '暂无数学推演'}
{record['analysis_report'][:4000]}
{math_block}

## 定律库
{laws_text}

## 输出
报告结构:
### 准确率: {math_cal.accuracy_score if math_cal else '?'}/100
<small>推演 {f'{pred.locked_h}-{pred.locked_a}' if pred else '暂无'} | 实际 {cal_h}-{cal_a} | 偏差 {math_cal.goal_deviation if math_cal else '?'}球</small>

### 差异
✅ [被验证的逻辑, 每条≤1句]
⚠️ [被推翻的逻辑, 每条≤1句]

<details><summary>📎 定律更新</summary>
新定律 JSON:
```json
[{{"name": "...", "content": "...", "trigger": "...", "lambda_effect": "..."}}]
```
修改建议 JSON:
```json
[{{"id": "...", "name": "...", "content": "...", "trigger": "...", "lambda_effect": "..."}}]
```
</details>
总字数≤300。"""

    calibration_report = _deepseek_chat(
        _CALIBRATE_FROM_JSON_PROMPT, summary_prompt,
        model=MODEL_CALIBRATE
    )

    # ---- 第五步：解析定律草案 ----
    new_laws, modified_laws = [], []
    try:
        all_json_blocks = list(re.finditer(r'```json\s*(.*?)\s*```', calibration_report, re.DOTALL))
        for block in all_json_blocks:
            block_text = block.group(1)
            parsed = json.loads(block_text)
            if isinstance(parsed, list):
                for item in parsed:
                    if isinstance(item, dict):
                        if 'id' in item and 'name' in item:
                            modified_laws.append(item)
                        elif 'name' in item and 'content' in item:
                            new_laws.append(item)
    except Exception:
        pass

    supabase.table("history").update({
        "calibration": calibration_report,
        "pending_laws": json.dumps(new_laws) if new_laws else None,
        "pending_modifications": json.dumps(modified_laws) if modified_laws else None
    }).eq("id", record["id"]).execute()

    # ---- 第六步：更新触发的定律的准确率 ----
    triggered_names = st.session_state.get("last_triggered_laws", [])
    if triggered_names and math_cal:
        # 判断推演是否命中胜负或比分
        prediction_correct = math_cal.result_match or math_cal.score_match
        for tname in triggered_names:
            try:
                # 用 supabase 查该用户下的定律名
                hits = supabase.table("laws").select("id,triggers_count,correct_count")\
                    .eq("username", st.session_state.username)\
                    .eq("name", tname)\
                    .execute()
                if hits.data:
                    law = hits.data[0]
                    new_trig = (law.get("triggers_count") or 0) + 1
                    new_cor = (law.get("correct_count") or 0) + (1 if prediction_correct else 0)
                    supabase.table("laws").update({
                        "triggers_count": new_trig,
                        "correct_count": new_cor
                    }).eq("id", law["id"]).execute()
            except Exception:
                pass

    return True, calibration_report, new_laws, modified_laws

def calibrate_all_uncalibrated():
    history = load_history()
    calibrated_count = 0
    skipped_count = 0
    progress_bar = st.progress(0)
    status_text = st.empty()
    
    uncalibrated = [r for r in history if not r.get("calibration")]
    if not uncalibrated:
        status_text.text("没有未校准的记录。")
        progress_bar.empty()
        return 0, 0

    for i, rec in enumerate(uncalibrated):
        status_text.text(f"正在校准：{rec['match']} ({i+1}/{len(uncalibrated)})")
        can_cal, msg = can_calibrate(rec.get("match_time"))
        if msg.startswith("⏳"):
            st.warning(f"⏭️ {rec['match']}：{msg}")
            skipped_count += 1
        else:
            success, report, _, _ = calibrate_record(rec)
            if success:
                calibrated_count += 1
            else:
                skipped_count += 1
                st.warning(f"⏭️ {rec['match']}：{report}")
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

# ============ 加载待处理定理草案 ============
def load_pending_drafts():
    new_laws_drafts = []
    modified_laws_drafts = []
    history = load_history()
    for rec in history:
        if rec.get("calibration") and rec.get("pending_laws"):
            try:
                laws = json.loads(rec["pending_laws"])
                for law in laws:
                    law["source_match"] = rec["match"]
                    law["source_id"] = rec["id"]
                new_laws_drafts.extend(laws)
            except:
                pass
        if rec.get("calibration") and rec.get("pending_modifications"):
            try:
                mods = json.loads(rec["pending_modifications"])
                for mod in mods:
                    mod["source_match"] = rec["match"]
                    mod["source_id"] = rec["id"]
                modified_laws_drafts.extend(mods)
            except:
                pass
    return new_laws_drafts, modified_laws_drafts

def apply_new_law(law, record_id):
    clean_law = {
        "id": law["id"],
        "name": law["name"],
        "content": law["content"],
        "trigger_condition": law.get("trigger", ""),
        "lambda_effect": law.get("lambda_effect", ""),
        "status": law.get("status", "active"),
        "username": st.session_state.username
    }
    supabase.table("laws").delete().eq("id", clean_law["id"]).execute()
    supabase.table("laws").insert(clean_law).execute()
    
    response = supabase.table("history").select("pending_laws").eq("id", record_id).execute()
    if response.data:
        pending = json.loads(response.data[0]["pending_laws"]) if response.data[0]["pending_laws"] else []
        updated_pending = [l for l in pending if l.get("name") != law.get("name")]
        supabase.table("history").update({"pending_laws": json.dumps(updated_pending) if updated_pending else None}).eq("id", record_id).execute()

def apply_modified_law(mod, record_id):
    supabase.table("laws").update({
        "name": mod["name"],
        "content": mod["content"],
        "trigger_condition": mod.get("trigger", ""),
        "lambda_effect": mod.get("lambda_effect", "")
    }).eq("id", mod["id"]).execute()
    response = supabase.table("history").select("pending_modifications").eq("id", record_id).execute()
    if response.data:
        pending = json.loads(response.data[0]["pending_modifications"]) if response.data[0]["pending_modifications"] else []
        updated_pending = [m for m in pending if m.get("id") != mod.get("id")]
        supabase.table("history").update({"pending_modifications": json.dumps(updated_pending) if updated_pending else None}).eq("id", record_id).execute()

def ignore_new_law(law, record_id):
    response = supabase.table("history").select("pending_laws").eq("id", record_id).execute()
    if response.data:
        pending = json.loads(response.data[0]["pending_laws"]) if response.data[0]["pending_laws"] else []
        updated_pending = [l for l in pending if l.get("name") != law.get("name")]
        supabase.table("history").update({"pending_laws": json.dumps(updated_pending) if updated_pending else None}).eq("id", record_id).execute()

def ignore_modified_law(mod, record_id):
    response = supabase.table("history").select("pending_modifications").eq("id", record_id).execute()
    if response.data:
        pending = json.loads(response.data[0]["pending_modifications"]) if response.data[0]["pending_modifications"] else []
        updated_pending = [m for m in pending if m.get("id") != mod.get("id")]
        supabase.table("history").update({"pending_modifications": json.dumps(updated_pending) if updated_pending else None}).eq("id", record_id).execute()

# ============ 主界面 ============
st.title("⚽ 全维推演工厂 V2.6")
match = st.text_input("输入比赛对阵（例如：法国 vs 塞内加尔）", placeholder="法国 vs 塞内加尔")

# 训练模式：用于已结束的历史比赛，跳过时间锁，可直接校准
training_mode = st.checkbox(
    "历史比赛模式",
    value=False,
    help="开启后，跳过赛前时间检查，赛后校准按钮始终可用。适用于对已结束的比赛进行推演训练。"
)
is_knockout = st.checkbox(
    "淘汰赛模式",
    value=False,
    help="开启后，除常规时间比分外，额外推演加时赛和点球大战的概率。"
)
if "is_knockout" not in st.session_state:
    st.session_state.is_knockout = False
st.session_state.is_knockout = is_knockout

training_match_date = None
if training_mode:
    training_match_date = st.date_input(
        "比赛日期（必填）",
        value=None,
        help="请确认比赛日期在过去。",
        max_value=_now_beijing().date()
    )
    if training_match_date is None:
        st.warning("请选择比赛日期后再继续。")
        st.stop()

if "training_mode" not in st.session_state:
    st.session_state.training_mode = False
st.session_state.training_mode = training_mode

# 一键推演（两步连跑）
if st.button("⚡ 一键推演", use_container_width=True, type="primary",
              help="自动完成搜索+推演，适合快速查看预测", key="one_click_predict"):
    if match:
        _st = st.empty()
        _st.info("⏳ 正在搜索并汇总数据...（预计 20-40s）")
        try:
            sq = f"请为 {match} 搜集赛前关键信息，并严格按照模板格式输出。" if not training_mode else \
                 f"请为 {match} 搜集比赛的赛前关键信息（首发阵容、伤病、赔率、历史交锋、教练发言、出线形势），并严格按照模板格式输出。请注意：这是一场已经结束的比赛，但请只搜集「赛前」信息，不要包含最终比分或赛后数据。"
            search_result = call_deepseek(system_prompt_search, sq, enable_search=True, search_mode="pre_match", model=MODEL_SEARCH)
            if not search_result:
                _st.empty(); st.warning("搜索未返回结果。"); st.stop()
            st.session_state.search_report = search_result
            st.session_state.current_match = match
            et = extract_match_time(search_result)
            if et: st.session_state.current_match_time = et
            elif training_mode: st.session_state.current_match_time = "2000-01-01 00:00"
        except Exception as e:
            _st.empty(); st.error(f"搜索出错: {str(e)[:80]}"); st.stop()

        _st.info("🧮 正在提取参数并运行数学引擎...（预计 20-30s）")
        try:
            params = _extract_params(search_result, laws_data.get("laws",[]), match)
            def ok(key, d=1.0):
                try: v = float(params.get(key,d))
                except: v = d
                return max(0.7,min(1.3,v)) if key not in ("lam_h_initial","lam_a_initial","odds_h","odds_d","odds_a") else v
            merged = params.get("merged_modifiers",{})
            mod = LambdaModifiers.from_merged(merged, home_adv=params.get("home_adv",False)) if merged else LambdaModifiers(attack=ok("attack"),defense=ok("defense"),tactical=ok("tactical"),coach_intent=ok("coach_intent"),scenario=ok("scenario"),home_adv=1.0)
            odds = None
            try:
                oh,od,oa = float(params.get("odds_h",0)),float(params.get("odds_d",0)),float(params.get("odds_a",0))
                if oh and od and oa: odds=(oh,od,oa)
            except: pass
            h0 = max(0.8,min(4.0,ok("lam_h_initial",1.5)))
            # 按用户输入的顺序当主/客队（世界杯无主客场）
            user_teams = _parse_teams(match)
            if user_teams:
                home_team, away_team = user_teams[0], user_teams[1]
            else:
                home_team = params.get("home_team", "")
                away_team = params.get("away_team", "")

            a0 = max(0.8,min(4.0,ok("lam_a_initial",1.2)))
            pred = predict_match(home=home_team, away=away_team,
                               lam_h0=h0, lam_a0=a0, mod=mod, odds=odds,
                               lam_c=ok("lam_c",0.01), phi=ok("phi",0.20), is_knockout=is_knockout)
            st.session_state.math_prediction=pred; pred.confidence*=mod.confidence
            mi = {k:getattr(mod,k) for k in ("attack","defense","tactical","coach_intent","scenario","home_adv","confidence")}; mi.update(mod._extra)
            ef = {}
            if is_knockout:
                # 有加时数据才展示淘汰赛信息（无数据说明 knockout 模式没有实际生效）
                if pred.extra_time_pct > 0 or pred.et_score_h != 0 or pred.et_score_a != 0:
                    ef["比赛类型"]="淘汰赛"
                    ef["90分钟"]=f"{pred.locked_h}-{pred.locked_a}"
                    ef["加时赛比分"]=f"{pred.et_score_h}-{pred.et_score_a}"
                    if pred.et_score_h==pred.et_score_a: ef["点球比分"]=f"{pred.pen_score_h}-{pred.pen_score_a}"
                    ef["主队晋级概率"]=f"{pred.home_advance*100:.1f}%"; ef["客队晋级概率"]=f"{pred.away_advance*100:.1f}%"
            mj=json.dumps({"锁定比分":f"{pred.locked_h}-{pred.locked_a}","主队":pred.home_team,"客队":pred.away_team,"主队λ":round(pred.lam_h,2),"客队λ":round(pred.lam_a,2),"期望进球":f"{pred.exp_h:.2f}-{pred.exp_a:.2f}","主胜":f"{pred.home_win*100:.1f}%","平局":f"{pred.draw*100:.1f}%","客胜":f"{pred.away_win*100:.1f}%","比分概率":[f"{h}-{a}({p*100:.1f}%)" for (h,a),p in pred.top_scores[:5]],"模型置信度":f"{pred.confidence*100:.0f}%","定律修正因子":mi,**ef},ensure_ascii=False)
            aq = f"赛前数据报告:\n{search_result[:8000]}\n\n数学模型计算结果:\n{mj}\n\n**【教练意图评级要求】**\n从赛前数据报告中的「教练发言」和「首发阵容」揣摩双方教练的进攻意图，按 L1-L5 评级。必须写明评级依据。\n\n**【最高优先级】报告中必须使用以上「锁定比分」字段的值作为最终推演比分。**\n"
        except Exception as e:
            st.error(f"推演出错: {str(e)[:80]}"); st.stop()

        _st.info("📝 正在生成推演报告...（预计 30-50s）")
        try:
            result = _deepseek_chat(_ANALYSIS_FROM_JSON_PROMPT, aq, model=MODEL_ANALYSIS)
            if not result:
                _st.info("🔄 备用模型重试...")
                result = _deepseek_chat(_ANALYSIS_FROM_JSON_PROMPT, aq, model=MODEL_SEARCH)
            if result:
                st.session_state.analysis_report = result
                st.session_state.math_json = mj; save_record(match, search_result, result)
                _st.empty(); st.success("✅ 推演完成！"); st.rerun()
            else:
                _st.empty(); st.error("推演报告生成失败，请重试。")
        except Exception as e:
            _st.empty(); st.error(f"推演出错: {str(e)[:80]}")
    else:
        st.warning("请先输入比赛名称")

# ============ 实时结果显示区域 ============
# 比分卡片优先展示（放在最上面）
if st.session_state.get("math_json"):
    mj_raw = st.session_state.math_json
    if isinstance(mj_raw, str):
        try: mj = json.loads(mj_raw)
        except: mj = {}
    else:
        mj = mj_raw
    score = mj.get("锁定比分", "?-?")
    home = mj.get("主队", "?")
    away = mj.get("客队", "?")
    hw = mj.get("主胜", "?")
    dr = mj.get("平局", "?")
    aw = mj.get("客胜", "?")
    conf = mj.get("模型置信度", "?")
    # 格式化: (法国)1:1(塞内加尔)
    score_compact = score.replace("-", ":")
    display_score = f"({home}){score_compact}({away})"
    et_score = mj.get("加时赛比分", "")
    pen_score = mj.get("点球比分", "")
    if pen_score:
        pen_compact = pen_score.replace("-", ":")
        display_score += f" 加时/点球 ({home}){pen_compact}({away})"
    st.markdown(f"""
    <div class="score-card">
        <div class="score">{display_score}</div>
        <div class="probs">{hw} | {dr} | {aw} &nbsp; 置信度 {conf}</div>
    </div>
    """, unsafe_allow_html=True)

if st.session_state.search_report:
    with st.expander("📡 信息雷达：赛前数据报告", expanded=False):
        st.markdown(st.session_state.search_report)

if st.session_state.analysis_report:
    with st.expander("🧠 推演引擎：全维推演报告", expanded=False):
        st.markdown(st.session_state.analysis_report)

# ============ 赛后校准 ============
st.markdown("---")
st.subheader("📊 赛后校准")
if st.button("🔍 搜集赛后数据并校准", use_container_width=True):
    if st.session_state.analysis_report:
        match_time = st.session_state.get("current_match_time")
        can_cal, msg = can_calibrate(match_time)

        if not training_mode:
            if not can_cal:
                st.warning(msg)
                if msg.startswith("⏳"):
                    st.stop()
        else:
            st.info("训练模式：已跳过时间校验。")

        with st.spinner("正在搜集赛后完整数据，请耐心等待..."):
            response = supabase.table("history").select("*").eq("username", st.session_state.username).eq("match", st.session_state.current_match).order("timestamp", desc=True).limit(1).execute()
            if response.data:
                record = response.data[0]
                success, calibration_report, new_laws, modified_laws = calibrate_record(record)

                if success:
                    st.success(msg if can_cal else "校准完成")
                    st.markdown(calibration_report)
                    if new_laws or modified_laws:
                        st.success(f"✅ 校准完成！已提炼出 {len(new_laws) if new_laws else 0} 条新定律草案和 {len(modified_laws) if modified_laws else 0} 条修改建议，请在下方定理控制台的【待处理的建议】中审核。")
                    else:
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

new_drafts, modified_drafts = load_pending_drafts()

if new_drafts or modified_drafts:
    st.markdown("### 📝 待处理的建议")
    st.info("以下是从校准中提炼出的定理草案，请逐一审核。")
    
    if new_drafts:
        st.markdown("#### 新定律草案")
        for i, law in enumerate(new_drafts):
            with st.container():
                st.markdown(f"**📌 {law.get('name', '新定律')}** — {law.get('lambda_effect', '')}")
                col_apply, col_ignore = st.columns(2)
                with col_apply:
                    if st.button(f"✅ 应用", key=f"apply_new_{i}"):
                        law["id"] = f"draft_{datetime.now().strftime('%Y%m%d%H%M%S')}_{i}"
                        law["status"] = "active"
                        apply_new_law(law, law["source_id"])
                        st.success("已应用新定律！")
                        st.rerun()
                with col_ignore:
                    if st.button(f"❌ 忽略", key=f"ignore_new_{i}"):
                        ignore_new_law(law, law["source_id"])
                        st.success("已忽略。")
                        st.rerun()
                st.divider()
    
    if modified_drafts:
        st.markdown("#### 修改建议")
        for i, mod in enumerate(modified_drafts):
            with st.container():
                st.markdown(f"**✏️ {mod.get('name', '修改')}** — {mod.get('lambda_effect', '')}")
                col_apply, col_ignore = st.columns(2)
                with col_apply:
                    if st.button(f"✅ 应用", key=f"apply_mod_{i}"):
                        apply_modified_law(mod, mod["source_id"])
                        st.success("已应用修改！")
                        st.rerun()
                with col_ignore:
                    if st.button(f"❌ 忽略", key=f"ignore_mod_{i}"):
                        ignore_modified_law(mod, mod["source_id"])
                        st.success("已忽略。")
                        st.rerun()
                st.divider()

# 现有定律
with st.expander("查看/管理所有定律", expanded=False):
    st.info("💡 开关按钮用于控制该定律是否参与赛前推演。关闭后，模型将不会调用该定律。所有修改会自动保存到云端数据库。")

    if laws_data["laws"]:
        # 计算评分并排序
        scored_laws = []
        for l in laws_data["laws"]:
            tc = l.get("triggers_count") or 0
            cc = l.get("correct_count") or 0
            acc = cc / tc if tc > 0 else 0
            # 评分 = 准确率 × log₂(使用次数+2)，既保正确率也保样本量
            score = round(acc * (math.log2(tc + 2) / math.log2(20)), 2) if tc > 0 else 0
            scored_laws.append((score, l))
        scored_laws.sort(key=lambda x: x[0], reverse=True)

        for rank_i, (score, law) in enumerate(scored_laws):
            # S/A/B/C/D 评级
            if score >= 0.30: grade = "S"
            elif score >= 0.20: grade = "A"
            elif score >= 0.10: grade = "B"
            elif score >= 0.05: grade = "C"
            else: grade = "D"
            extra_class = "law-grade-s" if grade == "S" else "law-grade-d" if grade == "D" else ""
            rank_badge = f"**<span class='law-grade {extra_class}'>{grade}</span>**"

            col1, col2, col_delete = st.columns([1, 9, 1])
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
                # 精简名：取 name 的前半部分（"核心缺阵 ≠ 进攻归零" → "核心缺阵"）
                short_name = law["name"].split("：")[0].split(":")[0].split("补丁")[0].strip()
                st.markdown(f"{rank_badge} **{status_emoji} {short_name}** {law.get('lambda_effect', '')}", unsafe_allow_html=True)
                # 显示使用准确率
                tc = law.get("triggers_count") or 0
                cc = law.get("correct_count") or 0
                if tc > 0:
                    acc_pct = round(cc / tc * 100)
                    star = "⭐" + ("🌟" if acc_pct >= 70 else "")
                    st.markdown(f"{star} 使用 {tc} 次 · 准确率 **{acc_pct}%** · 评分 **{score:.2f}**")
                else:
                    st.markdown(f"· 使用 {tc} 次")
            
            with col_delete:
                if st.button("🗑️", key=f"delete_law_{law['id']}", help="删除此定律"):
                    if delete_law_from_supabase(law["id"]):
                        st.toast(f"已删除: {law['name']}", icon="🗑️")
                        st.rerun()
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
        col_avg, col_count, col_trend = st.columns(3)
        with col_avg:
            st.metric("综合平均准确率", f"{average_score} / 100")
        with col_count:
            st.metric("已校准场次", len(scored_records))
        with col_trend:
            latest_5 = [r["score"] for r in scored_records[:5]]
            trend = "↑" if len(latest_5) >= 2 and latest_5[0] >= latest_5[-1] else "↓"
            st.metric("近期趋势", f"{trend} {sum(latest_5)/len(latest_5):.0f}/100" if latest_5 else "—")

        # ---- 每日准确率折线图 ----
        from collections import defaultdict
        daily = defaultdict(list)
        for r in scored_records:
            day = r["timestamp"][:10]
            daily[day].append(r["score"])
        # 按日期排序
        sorted_days = sorted(daily.items())
        if len(sorted_days) >= 2:
            import pandas as pd
            chart_data = pd.DataFrame(
                {"日期": day, "准确率": round(sum(scores) / len(scores), 1)}
                for day, scores in sorted_days
            ).set_index("日期")
            st.line_chart(chart_data, height=200)
        else:
            st.caption("需要至少2天的记录才能生成趋势图。")

        with st.expander("按日期查看", expanded=False):
            selected_date = st.date_input("选择日期", value=None)
            if selected_date:
                sd = str(selected_date)
                filtered = [r for r in scored_records if r["timestamp"].startswith(sd)]
                if filtered:
                    date_avg = round(sum(r["score"] for r in filtered) / len(filtered), 1)
                    st.metric(f"{sd} 准确率", f"{date_avg} / 100")
                    for r in filtered:
                        st.write(f"- {r['match']}: {r['score']}分")
                else:
                    st.info("该日期无已校准记录。")

# ============ 历史记录 ============
st.markdown("---")
st.subheader("📚 历史推演记录")

if st.button("🔧 一键校准所有未校准记录", use_container_width=True):
    with st.spinner("正在批量校准..."):
        cali_count, skip_count = calibrate_all_uncalibrated()
        if cali_count == 0 and skip_count == 0:
            st.info("所有记录都已被校准。")
        else:
            msg = f"成功校准 {cali_count} 条记录。"
            if skip_count > 0:
                msg += f" 跳过 {skip_count} 条（未到可校准时间或校准失败）。"
            st.success(msg)
            st.rerun()

history = load_history()
if not history:
    st.info("暂无推演记录。")
else:
    for i, rec in enumerate(history):
        match_time = rec.get("match_time")
        status_text, status_icon = get_match_status(match_time)
        title = f"{status_icon} {rec['match']} | {rec['timestamp']} | {status_text}"
        if rec.get("training_mode"):
            title += " | 🎓 训练"
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
                is_training = rec.get("training_mode", False)
                can_cal, cal_msg = can_calibrate(match_time)
                if can_cal or is_training:
                    if can_cal:
                        st.success(cal_msg)
                    else:
                        st.info("训练模式：已跳过时间校验。")
                    if st.button(f"🔍 校准此记录", key=f"calibrate_{i}"):
                        with st.spinner(f"正在校准 {rec['match']}..."):
                            success, report, _, _ = calibrate_record(rec)
                            if success:
                                st.success(f"{rec['match']} 校准成功！定理草案已存入待处理队列，请在定理控制台查看。")
                                st.markdown(report)
                                st.rerun()
                            else:
                                st.warning(report)
                elif cal_msg.startswith("⏳"):
                    st.warning(cal_msg)
                else:
                    st.warning(cal_msg + " 如已确认比赛结束可尝试。")
                    if st.button(f"🔍 强制校准此记录", key=f"calibrate_{i}"):
                        with st.spinner(f"正在校准 {rec['match']}..."):
                            success, report, _, _ = calibrate_record(rec)
                            if success:
                                st.success(f"{rec['match']} 校准成功！")
                                st.markdown(report)
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

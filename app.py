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
#   football_api_key       = "xxx"                    # API-Football Key（可选，rapidapi 免费）
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
FOOTBALL_API_KEY = _secret("football_api_key")

FOOTBALL_URL = "https://v3.football.api-sports.io"
FOOTBALL_HEADERS = {"x-apisports-key": FOOTBALL_API_KEY}
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


def get_match_status(match_time_str):
    if not match_time_str:
        return "未知", "⚪"
    match_time = parse_match_time(match_time_str)
    if not match_time:
        return "未知", "⚪"
    now = datetime.now()
    if now < match_time:
        return "未开赛", "🔵"
    # 比赛时间 + 150 分钟（含加时+点球+赛后 delay）
    elif now < match_time + timedelta(minutes=150):
        return "进行中", "🟡"
    else:
        return "已结束", "🟢"


def can_calibrate(match_time_str):
    """判断是否可以校准。返回 (bool, str)"""
    now = datetime.now()

    if not match_time_str:
        # 无时间信息时，给一个较宽松的兜底：
        # 如果相关报告存在超过 6 小时，允许校准
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
MODEL_LAW_PARAMS = {"model": "deepseek-chat"}       # _laws_to_modifiers 专用
MODEL_EXTRACT    = {"model": "deepseek-chat"}       # _extract_params 回退专用


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
        timeout=60
    )
    data = response.json()
    if 'error' in data:
        st.error(f"API 错误：{data['error']}")
        return ""
    return data['choices'][0]['message'].get('content', '')


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
            if k in known:
                kwargs[k] = v
            elif k == "home_adv":
                kwargs["home_adv"] = v
            else:
                # 用户自定义维度——放入 _extra，apply 时乘进去
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
    lam_c: float = 0.05
    phi: float = 0.15
    home_win: float = 0.0
    draw: float = 0.0
    away_win: float = 0.0
    exp_h: float = 0.0
    exp_a: float = 0.0
    top_scores: list = field(default_factory=list)
    confidence: float = 0.0


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


# ---- 核心推演 ----
def predict_match(home: str, away: str, lam_h0: float, lam_a0: float,
                  mod: LambdaModifiers, odds: Tuple[float, float, float] = None,
                  lam_c: float = 0.05, phi: float = 0.15, max_g: int = 6) -> MatchPrediction:
    lh = mod.apply(lam_h0, is_home=True)
    la = mod.apply(lam_a0, is_home=False)
    probs = _bivariate_poisson(lh, la, lam_c, max_g)

    hw = dw = aw = eh = ea = 0.0
    for (h, a), p in probs.items():
        eh += h * p; ea += a * p
        if h > a:      hw += p
        elif h == a:   dw += p
        else:          aw += p

    top = sorted(probs.items(), key=lambda x: x[1], reverse=True)[:5]

    conf = 1.0
    if odds:
        imp_h, imp_d, imp_a, _ = _implied_probs(*odds)
        conf = max(0.0, 1.0 - 0.5 * (abs(hw - imp_h) + abs(dw - imp_d) + abs(aw - imp_a)))

    return MatchPrediction(home_team=home, away_team=away,
                           lam_h=lh, lam_a=la, lam_c=lam_c, phi=phi,
                           home_win=hw, draw=dw, away_win=aw,
                           exp_h=eh, exp_a=ea, top_scores=top, confidence=conf)


# ---- 赛后校准 ----
def calibrate_math(pred: MatchPrediction, actual_h: int, actual_a: int) -> CalibrationResult:
    score_match = (round(pred.exp_h) == actual_h and round(pred.exp_a) == actual_a)
    pred_r = "home" if pred.home_win > max(pred.draw, pred.away_win) else \
             "draw" if pred.draw > max(pred.home_win, pred.away_win) else "away"
    actual_r = "home" if actual_h > actual_a else "draw" if actual_h == actual_a else "away"
    result_match = (pred_r == actual_r)
    deviation = abs(pred.exp_h - actual_h) + abs(pred.exp_a - actual_a)
    score = max(0, min(100, 100 - deviation * 15 - (0 if result_match else 25) -
                       (0 if pred.confidence >= 0.5 else 10)))
    return CalibrationResult(accuracy_score=round(score, 1),
                             score_match=score_match, result_match=result_match,
                             goal_deviation=round(deviation, 2))


# ---- 定律库驱动的参数提取 ----
_LAW_WEIGHT_PROMPT = """你是一个足球量化分析师。你的任务是逐条检查"全维推演定律库"中的每一条定律是否被本场比赛触发，并对触发的定律给出精确的乘性修正权重。

## 工作流程
1. 阅读赛前数据报告，理解比赛背景
2. 逐条阅读定律库中的定律，判断其 trigger 是否被满足
3. 对每条触发的定律，输出精确的乘性修正因子和影响的变量名

## 关键规则
- 逐条处理，不要跳过任何定律。不触发的定律也要出现在输出中（applies=false）
- 如果定律的 lambda_effect 有数值区间（如 "进攻λ -0.3 至 -0.5"），根据情境取精确值
- 如果定律的 lambda_effect 只有定性描述（如 "需用数学模型修正"、"置信度大幅降低"），你需要自己推断出合理的数值
- 如果定律影响多个维度（如 "领先后退守" 同时影响 attack 和 defense），在 modifiers 中列出所有影响的维度
- 你可以自由定义 modifier 的 key 名（attack, defense, coach_intent, confidence, tempo, psychology 等）。key 名用英文 snake_case
- 无影响的维度不需要出现在 modifiers 中
- 加法修正转乘性：factor = (λ + delta) / λ。例：λ=1.5, delta=-0.4 → factor = 1.1/1.5 ≈ 0.73
- 权重区间参考：0.70-0.85 严重削弱 | 0.86-0.95 轻微削弱 | 1.0 无影响 | 1.05-1.15 轻微增强 | 1.16-1.30 显著增强

## 输出
仅输出一个 JSON 对象，不要任何解释:
{
  "home_team": "主队名",
  "away_team": "客队名",
  "lam_h_initial": 1.5,
  "lam_a_initial": 1.3,
  "phi": 0.15,
  "lam_c": 0.05,
  "home_adv": true,
  "odds_h": 1.65,
  "odds_d": 3.80,
  "odds_a": 5.50,
  "law_effects": [
    {
      "law_id": "定律的id或name",
      "law_name": "定律名称",
      "applies": true,
      "modifiers": {"attack": 0.85},
      "reason": "为什么触发/为什么不触发,一句话"
    }
  ],
  "merged_modifiers": {"attack": 0.85, "coach_intent": 1.08, "confidence": 0.9},
  "summary": "一句话总结本场最关键的一条定律及其影响"
}
"""


def _laws_to_modifiers(search_report: str, laws: list) -> dict:
    """将定律库应用到具体比赛，输出精确乘性修正因子。
    若 LLM 调用失败，回退为基础提取模式。"""
    if not search_report:
        return {}

    active_laws = [l for l in laws if l.get("status", "active") == "active"]
    if not active_laws:
        active_laws = laws

    laws_json = json.dumps(active_laws, ensure_ascii=False, indent=2)

    try:
        payload = {"max_tokens": 1200, "temperature": 0.0}
        payload.update(MODEL_LAW_PARAMS)
        payload.update({
            "messages": [
                {"role": "system", "content": _LAW_WEIGHT_PROMPT},
                {"role": "user",
                 "content": f"## 赛前数据报告\n{search_report[:6000]}\n\n## 定律库\n{laws_json}"}
            ],
        })
        resp = requests.post(
            url=URL,
            headers={"Authorization": f"Bearer {API_KEY}", "Content-Type": "application/json"},
            json=payload,
            timeout=30
        )
        data = resp.json()
        if "error" in data:
            return {}
        content = data["choices"][0]["message"].get("content", "")
        m = re.search(r'\{[\s\S]*\}', content)
        if m:
            result = json.loads(m.group())
            # 展示触发的定律
            law_effects = result.get("law_effects", [])
            triggered = [f'{e["law_name"]}' for e in law_effects if e.get("applies")]
            if triggered:
                st.info(f"📋 触发 {len(triggered)}/{len(active_laws)} 条定律: {', '.join(triggered[:5])}")
            return result
    except Exception:
        pass
    return {}


# ---- 回退模式 prompt（定律库加载失败时使用）----
_PARAM_EXTRACT_PROMPT = (
    "你是一个数据提取器。从以下赛前数据报告中提取结构化参数，仅输出 JSON。\n"
    "字段: home_team, away_team, lam_h_initial, lam_a_initial, "
    "attack, defense, tactical, coach_intent, scenario, phi, lam_c, "
    "home_adv, odds_h, odds_d, odds_a"
)

def _extract_params(search_report: str, laws: list = None) -> dict:
    """从搜索报告提取结构化参数 — 优先使用定律驱动模式"""
    if laws and len(laws) > 0:
        result = _laws_to_modifiers(search_report, laws)
        if result and "home_team" in result:
            return result
    # 回退：纯 AI 猜测
    try:
        payload = {"max_tokens": 800, "temperature": 0.0}
        payload.update(MODEL_EXTRACT)
        payload.update({
            "messages": [
                {"role": "system", "content": _PARAM_EXTRACT_PROMPT},
                {"role": "user", "content": search_report[:8000]}
            ],
        })
        resp = requests.post(
            url=URL,
            headers={"Authorization": f"Bearer {API_KEY}", "Content-Type": "application/json"},
            json=payload,
            timeout=30
        )
        data = resp.json()
        if "error" in data:
            return {}
        content = data["choices"][0]["message"].get("content", "")
        m = re.search(r'\{[\s\S]*\}', content)
        if m:
            return json.loads(m.group())
    except Exception:
        pass
    return {}


# ---- 结构化推演结果转报告 ----
_ANALYSIS_FROM_JSON_PROMPT = (
    "你是一位专业的足球推演分析师。以下是 Python 数学引擎计算出的结构化推演结果。"
    "请基于这些数据，生成一份完整的全维推演报告。\n\n"
    "报告要求:\n"
    "1. 列出关键修正因子及其依据\n"
    "2. 展示比分概率分布\n"
    "3. 给出最可能比分和胜负预测\n"
    "4. 标注模型置信度\n"
    "5. 如果提供了赔率数据，说明市场与模型的分歧"
)


# ---- 结构化校准结果转报告 ----
_CALIBRATE_FROM_JSON_PROMPT = (
    "你是一位专业的足球赛后分析师。以下是 Python 数学引擎计算出的校准结果。"
    "请基于这些数据，生成一份完整的赛后校准报告，包含:\n"
    "1. 准确率评分\n"
    "2. 偏差分析 (验证/推翻的逻辑)\n"
    "3. 新定律/补丁建议 (JSON 格式)\n"
    "4. 现有定律修改建议 (JSON 格式)\n\n"
    "当前生效的定律库作为参考附在下方。"
)


# ============ API-Football 数据获取 ============
def _parse_teams(query):
    """从查询中提取两个队名，返回 (team1, team2) 或 (None, None)"""
    for sep in [r'\s+vs\s+', r'\s+v\s+', r'\s+对阵\s+', r'\s+对\s+']:
        parts = re.split(sep, query, flags=re.IGNORECASE)
        if len(parts) == 2:
            # 取分隔符左边最后一个词（中文或英文词）
            left_words = [w for w in parts[0].split() if re.search(r'[一-鿿]|[a-zA-Z]', w)]
            righ_words = [w for w in parts[1].split() if re.search(r'[一-鿿]|[a-zA-Z]', w)]
            if left_words and righ_words:
                t1, t2 = left_words[-1], righ_words[0]
                if t1 != t2:
                    return t1, t2
    return None, None


# 中英文队名映射（API-Football 只能英文搜索）
_TEAM_NAME_MAP = {
    "法国": "France", "德国": "Germany", "巴西": "Brazil", "阿根廷": "Argentina",
    "英格兰": "England", "西班牙": "Spain", "葡萄牙": "Portugal", "荷兰": "Netherlands",
    "意大利": "Italy", "比利时": "Belgium", "克罗地亚": "Croatia", "乌拉圭": "Uruguay",
    "塞内加尔": "Senegal", "摩洛哥": "Morocco", "日本": "Japan", "韩国": "South Korea",
    "澳大利亚": "Australia", "伊朗": "Iran", "沙特": "Saudi Arabia", "卡塔尔": "Qatar",
    "墨西哥": "Mexico", "美国": "USA", "加拿大": "Canada", "哥斯达黎加": "Costa Rica",
    "加纳": "Ghana", "喀麦隆": "Cameroon", "尼日利亚": "Nigeria", "突尼斯": "Tunisia",
    "埃及": "Egypt", "哥伦比亚": "Colombia", "智利": "Chile", "秘鲁": "Peru",
    "巴拉圭": "Paraguay", "厄瓜多尔": "Ecuador", "丹麦": "Denmark", "瑞典": "Sweden",
    "挪威": "Norway", "波兰": "Poland", "瑞士": "Switzerland", "奥地利": "Austria",
    "塞尔维亚": "Serbia", "乌克兰": "Ukraine", "土耳其": "Turkey", "捷克": "Czech",
    "俄罗斯": "Russia", "威尔士": "Wales", "苏格兰": "Scotland", "希腊": "Greece",
    "科特迪瓦": "Ivory Coast", "海地": "Haiti", "巴拿马": "Panama", "牙买加": "Jamaica",
}


def _search_team(team_name):
    """搜索球队 ID，返回 (team_id, team_name) 或 (None, None)。
    先中→英映射，再尝试 API 搜索。"""
    if not FOOTBALL_API_KEY:
        return None, None
    # 中→英
    search_name = _TEAM_NAME_MAP.get(team_name, team_name)
    try:
        resp = requests.get(
            f"{FOOTBALL_URL}/teams",
            params={"search": search_name},
            headers=FOOTBALL_HEADERS,
            timeout=10
        )
        data = resp.json()
        for t in data.get("response", []):
            return t["team"]["id"], t["team"]["name"]
        # 第一次搜不到，尝试 country 字段
        resp2 = requests.get(
            f"{FOOTBALL_URL}/teams",
            params={"country": search_name},
            headers=FOOTBALL_HEADERS,
            timeout=10
        )
        for t in resp2.json().get("response", []):
            return t["team"]["id"], t["team"]["name"]
    except Exception:
        pass
    return None, None


def _try_football_api(match_query, search_mode):
    """从 API-Football 获取结构化数据，成功返回格式化文本，失败返回空字符串"""
    if not FOOTBALL_API_KEY:
        return ""

    t1_name, t2_name = _parse_teams(match_query)
    if not t1_name or not t2_name:
        return ""

    t1_id, t1_full = _search_team(t1_name)
    t2_id, t2_full = _search_team(t2_name)
    if not t1_id or not t2_id:
        return ""

    try:
        # 跨赛季搜索（免费版无 2026 数据，回退到 2024-2022）
        fixture = None
        for season in [2026, 2025, 2024, 2022]:
            resp = requests.get(
                f"{FOOTBALL_URL}/fixtures",
                params={"team": t1_id, "season": season},
                headers=FOOTBALL_HEADERS,
                timeout=10
            )
            data = resp.json()
            for f in data.get("response", []):
                h_id = f["teams"]["home"]["id"]
                a_id = f["teams"]["away"]["id"]
                if (h_id == t1_id and a_id == t2_id) or (h_id == t2_id and a_id == t1_id):
                    fixture = f
                    break
            if fixture:
                break

        if not fixture:
            return ""

        fx = fixture["fixture"]
        teams = fixture["teams"]
        league = fixture["league"]
        lines = []

        # 基本信息
        lines.append(f"赛事: {league.get('name', '未知')} | {league.get('round', '')}")
        lines.append(f"对阵: {teams['home']['name']} vs {teams['away']['name']}")
        kickoff = fx.get("date", "")
        if kickoff:
            lines.append(f"开赛时间: {kickoff.replace('T', ' ').replace('+00:00', '')}")
        venue = fixture.get("fixture", {}).get("venue", {})
        if venue.get("name"):
            lines.append(f"场地: {venue.get('name', '')}, {venue.get('city', '')}")

        # 赔率
        odds_resp = requests.get(
            f"{FOOTBALL_URL}/odds",
            params={"fixture": fx["id"]},
            headers=FOOTBALL_HEADERS,
            timeout=10
        )
        odds_data = odds_resp.json()
        for book in odds_data.get("response", [])[:2]:
            for b in book.get("bookmakers", [])[:2]:
                lines.append(f"赔率 ({b['name']}):")
                for bet in b.get("bets", [])[:3]:
                    vals = " / ".join(
                        f"{v['value']}({v['odd']})" for v in bet.get("values", [])[:3]
                    )
                    lines.append(f"  {bet['name']}: {vals}")

        # 阵容
        lineup_resp = requests.get(
            f"{FOOTBALL_URL}/fixtures/lineups",
            params={"fixture": fx["id"]},
            headers=FOOTBALL_HEADERS,
            timeout=10
        )
        for lu in lineup_resp.json().get("response", []):
            side = lu.get("team", {}).get("name", "")
            formation = lu.get("formation", "")
            lines.append(f"\n{side} 阵型 {formation}:")
            for p in lu.get("startXI", [])[:11]:
                name = p.get("player", {}).get("name", "")
                number = p.get("player", {}).get("number", "")
                pos = p.get("player", {}).get("pos", "")
                lines.append(f"  {number} {name} ({pos})")
            subs = [p.get("player", {}).get("name", "") for p in lu.get("substitutes", [])[:7]]
            if subs:
                lines.append(f"  替补: {', '.join(subs)}")

        # 伤病
        for tid, tname in [(t1_id, t1_full), (t2_id, t2_full)]:
            inj_resp = requests.get(
                f"{FOOTBALL_URL}/injuries",
                params={"team": tid, "season": 2026},
                headers=FOOTBALL_HEADERS,
                timeout=10
            )
            injuries = inj_resp.json().get("response", [])
            if injuries:
                lines.append(f"\n{tname or tid} 伤病:")
                for inj in injuries[:8]:
                    p = inj.get("player", {})
                    lines.append(
                        f"  {p.get('name', '')} — {inj.get('fixture', {}).get('reason', '未知')}"
                    )

        # 历史交锋
        h2h_resp = requests.get(
            f"{FOOTBALL_URL}/fixtures/headtohead",
            params={"h2h": f"{t1_id}-{t2_id}"},
            headers=FOOTBALL_HEADERS,
            timeout=10
        )
        h2h_list = h2h_resp.json().get("response", [])[:5]
        if h2h_list:
            lines.append("\n历史交锋:")
            for h in h2h_list:
                ht = h["teams"]["home"]["name"]
                at = h["teams"]["away"]["name"]
                hg = h["goals"]["home"]
                ag = h["goals"]["away"]
                d = h["fixture"]["date"][:10]
                lines.append(f"  {d} {ht} {hg}-{ag} {at}")

        # 积分榜
        standings_resp = requests.get(
            f"{FOOTBALL_URL}/standings",
            params={"league": league.get("id"), "season": 2026},
            headers=FOOTBALL_HEADERS,
            timeout=10
        )
        for grp in standings_resp.json().get("response", [{}])[0].get("league", {}).get("standings", []):
            for row in grp:
                if row.get("team", {}).get("id") in (t1_id, t2_id):
                    lines.append(
                        f"  积分榜 {row['team']['name']}: "
                        f"排名{row.get('rank','?')} | "
                        f"赛{row.get('all',{}).get('played','?')} "
                        f"胜{row.get('all',{}).get('win','?')} "
                        f"平{row.get('all',{}).get('draw','?')} "
                        f"负{row.get('all',{}).get('lose','?')} | "
                        f"进球{row.get('all',{}).get('goals',{}).get('for','?')} "
                        f"失球{row.get('all',{}).get('goals',{}).get('against','?')} "
                    )

        # 赛后数据
        if search_mode == "post_match":
            events_resp = requests.get(
                f"{FOOTBALL_URL}/fixtures/events",
                params={"fixture": fx["id"]},
                headers=FOOTBALL_HEADERS,
                timeout=10
            )
            events = events_resp.json().get("response", [])
            if events:
                lines.append(f"\n比赛事件:")
                for ev in events:
                    t = ev.get("time", {}).get("elapsed", "?")
                    p = ev.get("player", {}).get("name", "")
                    tp = ev.get("type", "")
                    detail = ev.get("detail", "")
                    side = ev.get("team", {}).get("name", "")
                    extra = ev.get("comments", "") or ""
                    match tp:
                        case "Goal":
                            lines.append(f"  {t}' ⚽ {p} ({side}) — {detail} {extra}")
                        case "Card":
                            lines.append(f"  {t}' 🟨 {p} ({side}) — {detail}")
                        case "subst":
                            a = ev.get("assist", {}).get("name", "")
                            lines.append(f"  {t}' 🔄 {a} → {p} ({side})")

            stats_resp = requests.get(
                f"{FOOTBALL_URL}/fixtures/statistics",
                params={"fixture": fx["id"]},
                headers=FOOTBALL_HEADERS,
                timeout=10
            )
            for team_stats in stats_resp.json().get("response", []):
                tname = team_stats.get("team", {}).get("name", "")
                lines.append(f"\n{tname} 技术统计:")
                for s in team_stats.get("statistics", []):
                    v = s.get("value", "")
                    if v is not None:
                        lines.append(f"  {s.get('type','')}: {v}")

        lines.insert(0, "[数据来源: API-Football]")
        return "\n".join(lines)

    except Exception:
        return ""


# ============ Tavily 搜索（回退方案）============
def _tavily_search(query):
    """Tavily 搜索，返回格式化结果"""
    try:
        resp = requests.post(
            TAVILY_URL,
            headers={"Content-Type": "application/json"},
            json={
                "api_key": TAVILY_API_KEY,
                "query": query,
                "search_depth": "basic",
                "max_results": 5
            },
            timeout=15
        )
        data = resp.json()
        results = []
        for item in data.get("results", []):
            content = item.get("content", "")
            if len(content) > 20:
                results.append(f"- {item.get('title', '')}: {content}")
        return "\n".join(results) if results else "搜索未返回有效结果。"
    except Exception as e:
        return f"搜索失败：{str(e)}"


def _search_with_tavily(system_prompt, user_query, search_mode="pre_match", model=None):
    """直接 Tavily 搜索 + DeepSeek 汇总，不依赖 tool-calling"""
    if not TAVILY_API_KEY:
        return ""

    # extract clean match name for search keywords
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
            f"{search_target} 首发阵容 伤病 历史交锋",
            f"{search_target} 赔率 裁判 赛前新闻 教练发言",
        ]
    else:
        search_rounds = [
            f'"{search_target}" final score result goalscorers',
            f"{search_target} 最終比分 进球者 赛后技术统计",
            f'"{search_target}" match report statistics',
        ]

    all_results = ""
    for q in search_rounds:
        r = _tavily_search(q)
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
    """调用 DeepSeek API。
    enable_search=True 时：API-Football → Tavily 二级回退。
    search_mode: "pre_match" 或 "post_match"
    model: 覆盖默认模型选择
    """
    if not API_KEY:
        st.error("API Key 未配置，请联系 UP 主。")
        return ""

    if not enable_search:
        return _deepseek_chat(system_prompt, user_query, model=model)

    # Tier 1: API-Football
    football_data = _try_football_api(user_query, search_mode)
    if football_data:
        return _deepseek_chat(
            system_prompt,
            f"{user_query}\n\n[实时数据]\n{football_data}",
            model=model
        )

    # Tier 2: Tavily
    result = _search_with_tavily(system_prompt, user_query, search_mode, model=model)
    if result:
        return result

    # Tier 3: 纯模型知识
    return _deepseek_chat(
        system_prompt,
        "[数据源均不可用] 请利用你的训练数据回答以下问题。不确定处标注\"基于历史数据推测\"。\n\n" + user_query,
        model=model
    )

# ============ 历史记录管理 ============
def save_record(match, search_report, analysis_report):
    match_time = extract_match_time(search_report)
    is_training = st.session_state.get("training_mode", False)
    record = {
        "username": st.session_state.username,
        "match": match,
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "search_report": search_report,
        "analysis_report": analysis_report,
        "calibration": None,
        "match_time": match_time,
        "training_mode": is_training,
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
    for attempt in range(max_attempts):
        search_query = f"请联网搜索 {record['match']} 的赛后完整数据（比分、进球、技术统计、关键事件等）。"
        post_match_data = call_deepseek(
            system_prompt_calibrate, search_query,
            enable_search=True, search_mode="post_match", model=MODEL_CALIBRATE
        )
        if not any(kw in post_match_data for kw in [
            "比赛尚未开始", "赛后数据未更新", "未返回有效结果", "搜索失败", "无法找到"
        ]):
            break
        if attempt == max_attempts - 1:
            post_match_data = _deepseek_chat(
                system_prompt_calibrate,
                f"**【硬性规则】{record['match']} 是已结束的比赛。直接给出比分、进球者、关键事件。**",
                model=MODEL_CALIBRATE,
            )
            if "比赛尚未开始" in post_match_data:
                return False, post_match_data, [], []
        else:
            continue
        break

    # ---- 第二步：从赛后数据提取实际比分 ----
    post_params = _extract_params(post_match_data)
    actual_h = post_params.get("actual_h") or post_params.get("predicted_h")
    actual_a = post_params.get("actual_a") or post_params.get("predicted_a")
    if actual_h is None or actual_a is None:
        scores = re.findall(r'(\d+)\s*[-:]\s*(\d+)', post_match_data)
        if scores:
            actual_h, actual_a = int(scores[0][0]), int(scores[0][1])
        else:
            actual_h, actual_a = 0, 0

    # ---- 第三步：Python 数学引擎校准 ----
    math_cal = None
    pred = st.session_state.get("math_prediction")
    if pred and actual_h is not None and actual_a is not None:
        math_cal = calibrate_math(pred, int(actual_h), int(actual_a))

    # ---- 第四步：LLM 生成校准报告 + 定律提炼 ----
    all_laws = laws_data["laws"]
    laws_text = json.dumps(all_laws, ensure_ascii=False, indent=2)

    math_block = ""
    if math_cal:
        math_block = (
            f"\n\n【Python 数学引擎校准结果】\n"
            f"准确率评分: {math_cal.accuracy_score}/100\n"
            f"推演期望: {pred.exp_h:.2f} - {pred.exp_a:.2f} → 实际: {actual_h} - {actual_a}\n"
            f"进球偏差: {math_cal.goal_deviation:.1f}球\n"
            f"比分命中: {'是' if math_cal.score_match else '否'} | "
            f"胜负命中: {'是' if math_cal.result_match else '否'}\n"
        )

    summary_prompt = f"""你是一位专业的足球赛后分析师。请进行偏差分析并提炼新定律。

## ⚠️ 核心规则
1. 严格基于赛后真实数据分析，严禁编造。
2. 不确定信息标注"暂无"。
3. 所有比分与赛后数据完全一致。

## 赛后真实数据
{post_match_data[:6000]}

## 赛前推演报告
{record['analysis_report'][:6000]}
{math_block}

## 当前生效的定律库
{laws_text}

## 输出要求
先输出 JSON 摘要，再输出自然语言分析，最后附新定律和修改建议。

JSON摘要:
```json
{{
  "final_score": "{actual_h}-{actual_a}",
  "accuracy_score": {math_cal.accuracy_score if math_cal else "0-100 的整数"},
  "key_events": []
}}
```

新定律 (JSON 列表):
```json
[{{"name": "...", "content": "...", "trigger": "...", "lambda_effect": "..."}}]
```

修改建议 (JSON 列表):
```json
[{{"id": "...", "name": "...", "content": "...", "trigger": "...", "lambda_effect": "..."}}]
```
"""

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
match = st.text_input("输入比赛对阵（例如：法国 vs 塞内加尔）", placeholder="输入比赛名称...")

# 训练模式：用于已结束的历史比赛，跳过时间锁，可直接校准
training_mode = st.checkbox(
    "历史比赛模式",
    value=False,
    help="开启后，跳过赛前时间检查，赛后校准按钮始终可用。适用于对已结束的比赛进行推演训练。"
)
training_match_date = None
if training_mode:
    training_match_date = st.date_input(
        "比赛日期（必填）",
        value=None,
        help="请确认比赛日期在过去。",
        max_value=datetime.now().date()
    )
    if training_match_date is None:
        st.warning("请选择比赛日期后再继续。")
        st.stop()

if "training_mode" not in st.session_state:
    st.session_state.training_mode = False
st.session_state.training_mode = training_mode

col1, col2 = st.columns(2)

with col1:
    btn_label = "🔍 搜集赛前数据" if not training_mode else "🔍 搜集历史数据"
    if st.button(btn_label, use_container_width=True):
        if match:
            with st.spinner("正在搜索并汇总数据，请耐心等待..."):
                if training_mode:
                    search_query = f"请为 {match} 搜集比赛的完整信息（包括最终比分、进球者、关键事件、首发阵容等），并严格按照模板格式输出。请注意这是一场已经结束的比赛。"
                else:
                    search_query = f"请为 {match} 搜集赛前关键信息，并严格按照模板格式输出。"
                result = call_deepseek(system_prompt_search, search_query, enable_search=True, search_mode="pre_match", model=MODEL_SEARCH)
                if result:
                    st.session_state.search_report = result
                    st.session_state.current_match = match
                    extracted_time = extract_match_time(result)
                    st.session_state.current_match_time = extracted_time
                    if extracted_time:
                        st.success(f"✅ 开赛时间：{extracted_time}")
                    else:
                        if training_mode:
                            st.session_state.current_match_time = "2000-01-01 00:00"  # 远古时间，保证 can_calibrate 通过
                            st.info("ℹ️ 训练模式：已跳过时间校验，可直接校准。")
                        else:
                            st.info("ℹ️ 未提取到开赛时间，赛后校准将需要手动确认。")
        else:
            st.warning("请先输入比赛名称")

with col2:
    btn_label2 = "🧠 开始全维推演" if not training_mode else "🧠 训练推演"
    if st.button(btn_label2, use_container_width=True):
        if match and st.session_state.search_report:
            with st.spinner("正在提取参数并运行数学引擎..."):
                # 1. 从搜索结果提取结构化参数
                params = _extract_params(st.session_state.search_report,
                                         laws_data.get("laws", []))
                # 2. 从定律分析结果构建修正因子（兼容任意用户定律）
                merged = params.get("merged_modifiers", {})
                if merged:
                    mod = LambdaModifiers.from_merged(
                        merged,
                        home_adv=params.get("home_adv", False)
                    )
                else:
                    # 回退：旧格式（无 merged_modifiers）
                    mod = LambdaModifiers(
                        attack=params.get("attack", 1.0),
                        defense=params.get("defense", 1.0),
                        tactical=params.get("tactical", 1.0),
                        coach_intent=params.get("coach_intent", 1.0),
                        scenario=params.get("scenario", 1.0),
                        home_adv=1.08 if params.get("home_adv", False) else 1.0,
                    )
                # 3. 数学引擎推演
                odds = None
                if params.get("odds_h") and params.get("odds_d") and params.get("odds_a"):
                    odds = (params["odds_h"], params["odds_d"], params["odds_a"])
                pred = predict_match(
                    home=params.get("home_team", ""),
                    away=params.get("away_team", ""),
                    lam_h0=params.get("lam_h_initial", 1.3),
                    lam_a0=params.get("lam_a_initial", 1.3),
                    mod=mod,
                    odds=odds,
                    lam_c=params.get("lam_c", 0.05),
                    phi=params.get("phi", 0.15),
                )
                st.session_state.math_prediction = pred
                # 定律对置信度的修正
                pred.confidence *= mod.confidence
                st.info(f"数学引擎完成 (λ_h={pred.lam_h:.2f}, λ_a={pred.lam_a:.2f}, "
                        f"置信度 {pred.confidence*100:.0f}%)")

            with st.spinner("正在生成推演报告..."):
                # 4. LLM 基于数学结果生成自然语言报告
                modifier_info = {
                    k: v for k, v in {
                        "attack": mod.attack, "defense": mod.defense,
                        "tactical": mod.tactical, "coach_intent": mod.coach_intent,
                        "scenario": mod.scenario, "home_adv": mod.home_adv,
                        "confidence": mod.confidence,
                    }.items() if v != 1.0
                }
                for k, v in mod._extra.items():
                    modifier_info[k] = v

                math_json = json.dumps({
                    "主队": pred.home_team, "客队": pred.away_team,
                    "主队λ": round(pred.lam_h, 2), "客队λ": round(pred.lam_a, 2),
                    "期望进球": f"{pred.exp_h:.2f} - {pred.exp_a:.2f}",
                    "主胜": f"{pred.home_win*100:.1f}%",
                    "平局": f"{pred.draw*100:.1f}%",
                    "客胜": f"{pred.away_win*100:.1f}%",
                    "最可能比分": [f"{h}-{a} ({p*100:.1f}%)" for (h, a), p in pred.top_scores[:5]],
                    "模型置信度": f"{pred.confidence*100:.0f}%",
                    "定律修正因子": modifier_info,
                }, ensure_ascii=False, indent=2)

                analysis_query = (
                    f"赛前数据报告:\n{st.session_state.search_report[:6000]}\n\n"
                    f"数学模型计算结果 (Python 引擎):\n{math_json}\n\n"
                    f"请基于以上信息，生成完整的全维推演报告。"
                )
                if training_mode:
                    analysis_query = "注意：这是一场已结束的历史比赛，请忽略最终比分，模拟赛前视角。\n" + analysis_query

                result = _deepseek_chat(_ANALYSIS_FROM_JSON_PROMPT, analysis_query,
                                        model=MODEL_ANALYSIS)
                if result:
                    st.session_state.analysis_report = result
                    st.session_state.math_json = math_json
                    save_record(match, st.session_state.search_report, st.session_state.analysis_report)
                    st.success("推演记录已保存至云端数据库。")
        elif not match:
            st.warning("请先输入比赛名称")
        else:
            st.warning("请先搜集赛前数据")

# ============ 实时结果显示区域 ============
if st.session_state.search_report:
    with st.expander("📡 信息雷达：赛前数据报告", expanded=True):
        st.markdown(st.session_state.search_report)

if st.session_state.analysis_report:
    with st.expander("🧠 推演引擎：全维推演报告", expanded=True):
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
                st.markdown(f"**📌 {law.get('name', '新定律')}** (来自: {law.get('source_match', '未知')})")
                st.markdown(f"· {law.get('content', '')}")
                st.markdown(f"· 触发条件：{law.get('trigger', '')}")
                st.markdown(f"· λ值修正：{law.get('lambda_effect', '')}")
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
                st.markdown(f"**✏️ 修改 ID: {mod.get('id', '未知')}** (来自: {mod.get('source_match', '未知')})")
                st.markdown(f"· 新名称：{mod.get('name', '')}")
                st.markdown(f"· 新逻辑：{mod.get('content', '')}")
                st.markdown(f"· 新触发条件：{mod.get('trigger', '')}")
                st.markdown(f"· 新λ值修正：{mod.get('lambda_effect', '')}")
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
        for i, law in enumerate(laws_data["laws"]):
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
                st.markdown(f"**{status_emoji} {law['name']}**")
                st.markdown(f"· {law['content']}")
                st.markdown(f"· 触发条件：{law.get('trigger_condition', '暂无')}")
                st.markdown(f"· λ值修正：{law.get('lambda_effect', '暂无')}")
            
            with col_delete:
                if st.button("🗑️", key=f"delete_law_{law['id']}", help="删除此定律"):
                    warning_msg = f"确定要删除定律“{law['name']}”吗？"
                    if law.get("username") == "admin":
                        warning_msg += " 这是由管理员创建的原始定理，删除后可能影响系统推演质量。"
                    warning_msg += " 此操作不可恢复。"
                    st.warning(warning_msg)
                    if st.button("✅ 确认删除", key=f"confirm_delete_{law['id']}"):
                        if delete_law_from_supabase(law["id"]):
                            st.success("定律已删除。")
                            st.rerun()
                        else:
                            st.error("删除失败，请重试。")
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

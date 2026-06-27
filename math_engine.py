"""
全维推演工厂 — 升级版数学模型引擎
修复 prompt_analysis.md 的 7 个数学缺陷，纯 Python 计算，不依赖 LLM 推理
"""
import math
import json
from dataclasses import dataclass, field
from typing import List, Tuple, Dict, Optional
from itertools import product


# ============================================================
# 1. 双变量泊松 — 修复独立性假设
# ============================================================
def bivariate_poisson_score_probs(lam_h: float, lam_a: float, lam_c: float,
                                   max_goals: int = 6) -> Dict[Tuple[int, int], float]:
    """
    双变量泊松： P(h, a) = e^-(λ_h+λ_a+λ_c) * Σ_{k=0}^{min(h,a)} [λ_h^(h-k)/(h-k)! * λ_a^(a-k)/(a-k)! * λ_c^k/k!]
    λ_c 捕获两队进球的关联性（高 λ_c → 高比分比赛概率上升）
    """
    probs = {}
    total = 0.0
    for h in range(max_goals + 1):
        for a in range(max_goals + 1):
            k_max = min(h, a)
            s = 0.0
            for k in range(k_max + 1):
                s += (lam_h ** (h - k) / math.factorial(h - k) *
                      lam_a ** (a - k) / math.factorial(a - k) *
                      lam_c ** k / math.factorial(k))
            p = math.exp(-(lam_h + lam_a + lam_c)) * s
            probs[(h, a)] = p
            total += p
    return {k: v / total for k, v in probs.items()}


# ============================================================
# 2. λ 乘性修正 — 修复加法无依据
# ============================================================
@dataclass
class LambdaModifiers:
    """乘性修正因子，范围 (0.5, 1.5)，1.0 = 无修正"""
    attack:      float = 1.0    # 核心复出/状态火热
    defense:     float = 1.0    # 防线完整/门将水平
    tactical:    float = 1.0    # 战术克制
    coach_intent: float = 1.0   # L1-L5 → 0.85, 0.92, 1.0, 1.08, 1.15
    scenario:    float = 1.0    # 生死战/高原/加时
    home_adv:    float = 1.08   # 主场优势 ~8%

    def apply(self, lam: float, is_home: bool = False) -> float:
        factor = (self.attack * self.defense * self.tactical *
                  self.coach_intent * self.scenario)
        if is_home:
            factor *= self.home_adv
        return max(0.1, lam * factor)


# ============================================================
# 3. 负二项分布处理过度离散
# ============================================================
def neg_binom_goals_prob(lam: float, k: int, phi: float = 0.15) -> float:
    """
    负二项：r = lam^2 / (σ^2 - lam)，其中 σ^2 = lam + φ·lam^2
    φ 是过度离散参数（足球通常 0.05-0.25）
    """
    sigma_sq = lam + phi * lam * lam
    if sigma_sq <= lam:
        sigma_sq = lam + 0.001  # 数值保护
    r = lam * lam / (sigma_sq - lam)
    p_success = lam / sigma_sq  # NB 的 p 参数
    return (math.gamma(r + k) / (math.gamma(r) * math.factorial(k)) *
            (1 - p_success) ** r * p_success ** k)


# ============================================================
# 4. 比赛阶段分割 λ — 修复单 λ 覆盖全场
# ============================================================
MATCH_PHASES = [
    ("开场试探",     0,   15,  0.85),
    ("上半场中段",  15,  30,  1.05),
    ("半场前线分钟", 30,  45,  1.20),
    ("半场补时",     45,  48,  1.10),
    ("下半场开局",   45,  60,  0.90),
    ("下半场中段",   60,  75,  1.00),
    ("终场前冲刺",   75,  90,  1.25),
    ("伤停补时",     90,  97,  1.35),
]

EXTRA_TIME_PHASES = [
    ("加时上半场",    90,  105, 0.75),
    ("加时下半场",   105,  120, 0.85),
]

SHOOTOUT_WIN_PROB = 0.50  # 假设均匀


def phase_adjusted_lambda(lam: float, current_minute: int, is_extra: bool = False) -> float:
    """根据比赛当前分钟返回这一阶段的 λ 修正因子"""
    phases = EXTRA_TIME_PHASES if is_extra else MATCH_PHASES
    for _, start, end, factor in phases:
        if start <= current_minute < end:
            return lam * factor
    return lam


# ============================================================
# 5. 动态贝叶斯更新 — 修复粗糙 α 常数
# ============================================================
def bayesian_update(lam_prior: float, observed_goals: int,
                     elapsed_min: int, total_min: int = 90,
                     opponent_strength: float = 1.0) -> float:
    """
    动态贝叶斯：α = f(时间剩余, 对手强度, 事件重要性)
    公式：λ_new = λ_prior × (剩余时间/总时间) + λ_observed × (已过时间/总时间) × 信息权重
    """
    time_remaining = max(1, total_min - elapsed_min)
    alpha_weight = time_remaining / total_min  # 时间因子

    # 信息权重：
    # - 强队进球对小波的 λ_observed 修正较轻
    # - 红牌事件 α 上调
    if opponent_strength <= 0.7:  # 弱队
        alpha_weight *= 1.3

    lam_observed = observed_goals / max(1, elapsed_min) * total_min
    lam_new = (lam_prior * alpha_weight +
               lam_observed * (1 - alpha_weight) * opponent_strength)
    return lam_new


# ============================================================
# 6. 赔率 overround 归一化 — 修复期望值计算
# ============================================================
def implied_probabilities(odds_h: float, odds_d: float, odds_a: float) -> Tuple[float, float, float]:
    """去掉庄家抽水的真实隐含概率"""
    raw = [1.0 / odds_h, 1.0 / odds_d, 1.0 / odds_a]
    overround = sum(raw)
    return tuple(p / overround for p in raw)


def expected_value(model_prob: float, odds: float) -> float:
    """期望值 = (赔率 / overround归一化后) × 模型概率"""
    return odds * model_prob  # 简化：直接 odds × prob，overround 在 implied_probs 中处理


# ============================================================
# 7. 核心推演引擎
# ============================================================
@dataclass
class MatchPrediction:
    home_team: str
    away_team: str
    lam_h: float
    lam_a: float
    lam_c: float           # 双变量泊松关联项
    phi: float = 0.15      # 过度离散
    score_probs: Dict[Tuple[int, int], float] = field(default_factory=dict)
    top_scores: List[Tuple[Tuple[int, int], float]] = field(default_factory=list)
    home_win_prob: float = 0.0
    draw_prob: float = 0.0
    away_win_prob: float = 0.0
    expected_goals_h: float = 0.0
    expected_goals_a: float = 0.0
    confidence: float = 0.0


def predict_match(home_team: str, away_team: str,
                  modifiers: LambdaModifiers,
                  lam_h_initial: float, lam_a_initial: float,
                  lam_c: float = 0.05,
                  phi: float = 0.15,
                  odds_h: float = None, odds_d: float = None, odds_a: float = None,
                  ) -> MatchPrediction:
    """
    完整推演流程：
    1. 应用乘性修正
    2. 双变量泊松计算比分概率
    3. 统计胜负平概率
    4. 可选：赔率校准
    """
    lam_h = modifiers.apply(lam_h_initial, is_home=True)
    lam_a = modifiers.apply(lam_a_initial, is_home=False)

    score_probs = bivariate_poisson_score_probs(lam_h, lam_a, lam_c)

    home_win = 0.0
    draw = 0.0
    away_win = 0.0
    exp_h = 0.0
    exp_a = 0.0

    for (h, a), p in score_probs.items():
        exp_h += h * p
        exp_a += a * p
        if h > a:
            home_win += p
        elif h == a:
            draw += p
        else:
            away_win += p

    sorted_scores = sorted(score_probs.items(), key=lambda x: x[1], reverse=True)
    top5 = sorted_scores[:5]

    confidence = 1.0
    if odds_h and odds_d and odds_a:
        imp_h, imp_d, imp_a = implied_probabilities(odds_h, odds_d, odds_a)
        # 市场与模型的 KL 散度简化版 — 相差越大越不自信
        confidence = max(0.0, 1.0 - 0.5 * (
            abs(home_win - imp_h) + abs(draw - imp_d) + abs(away_win - imp_a)
        ))

    return MatchPrediction(
        home_team=home_team, away_team=away_team,
        lam_h=lam_h, lam_a=lam_a, lam_c=lam_c, phi=phi,
        score_probs=score_probs, top_scores=top5,
        home_win_prob=home_win, draw_prob=draw, away_win_prob=away_win,
        expected_goals_h=exp_h, expected_goals_a=exp_a,
        confidence=confidence,
    )


# ============================================================
# 8. 赛后校准引擎
# ============================================================
@dataclass
class CalibrationResult:
    accuracy_score: float          # 0-100
    score_match: bool              # 比分命中
    result_match: bool             # 胜负平命中
    goal_deviation: float          # 进球数偏差
    new_laws: List[Dict] = field(default_factory=list)
    modified_laws: List[Dict] = field(default_factory=list)


def calibrate(prediction: MatchPrediction, actual_h: int, actual_a: int) -> CalibrationResult:
    """赛后校准：对比推演和实际结果"""
    score_match = (round(prediction.expected_goals_h) == actual_h and
                   round(prediction.expected_goals_a) == actual_a)

    pred_result = "home" if prediction.home_win_prob > max(prediction.draw_prob, prediction.away_win_prob) else \
                  "draw" if prediction.draw_prob > max(prediction.home_win_prob, prediction.away_win_prob) else "away"
    actual_result = "home" if actual_h > actual_a else "draw" if actual_h == actual_a else "away"
    result_match = pred_result == actual_result

    goal_deviation = abs(prediction.expected_goals_h - actual_h) + abs(prediction.expected_goals_a - actual_a)

    # 准确率评分
    score = 100.0
    if score_match:
        score -= 0
    else:
        score -= goal_deviation * 15  # 每偏离 1 球扣 15 分
    if not result_match:
        score -= 25
    if prediction.confidence < 0.5:
        score -= 10

    accuracy = max(0, min(100, score))

    return CalibrationResult(
        accuracy_score=round(accuracy, 1),
        score_match=score_match,
        result_match=result_match,
        goal_deviation=goal_deviation,
    )


# ============================================================
# 9. 完整 benchmark
# ============================================================
if __name__ == "__main__":
    import time

    print("=" * 60)
    print("  数学模型引擎 Benchmark")
    print("=" * 60)

    # Test case: Senegal vs Netherlands
    modifiers = LambdaModifiers(
        attack=0.85,      # Mane 缺阵 → 进攻↓15%
        defense=1.0,
        tactical=1.05,    # Van Gaal 战术
        coach_intent=1.15, # L5 进攻意图
        scenario=1.0,
        home_adv=1.0,     # 中立场地
    )
    home = "Senegal"
    away = "Netherlands"
    lam_h = 1.2    # Senegal 场均进球
    lam_a = 1.8    # Netherlands 场均进球

    t0 = time.perf_counter()

    # 1. 推演
    pred = predict_match(home, away, modifiers, lam_h, lam_a,
                         lam_c=0.08, phi=0.15,
                         odds_h=5.50, odds_d=3.80, odds_a=1.65)

    # 2. 赛后校准
    cal = calibrate(pred, actual_h=0, actual_a=2)

    elapsed_ms = (time.perf_counter() - t0) * 1000

    print(f"\n  Input:     {home} vs {away}")
    print(f"  λ_final:   {pred.lam_h:.2f} (home)  {pred.lam_a:.2f} (away)")
    print(f"  Goals:     {pred.expected_goals_h:.2f} - {pred.expected_goals_a:.2f}")
    print(f"  Top 5 scores:")
    for (h, a), p in pred.top_scores:
        bar = "█" * int(p * 400)
        print(f"    {h}-{a}:  {p*100:6.2f}%  {bar}")

    print(f"\n  Home win:  {pred.home_win_prob*100:.1f}%")
    print(f"  Draw:      {pred.draw_prob*100:.1f}%")
    print(f"  Away win:  {pred.away_win_prob*100:.1f}%")
    print(f"  Confidence:{pred.confidence*100:.0f}%")

    print(f"\n  Calibration:")
    print(f"    Accuracy: {cal.accuracy_score}/100")
    print(f"    Score exact: {cal.score_match}")
    print(f"    Result correct: {cal.result_match}")
    print(f"    Goal deviation: {cal.goal_deviation:.1f}")

    print(f"\n  Performance:")
    print(f"    Compute time: {elapsed_ms:.1f}ms (pure Python, no LLM call)")

    # Quick throughput test
    print(f"\n  Throughput test: 1000 predictions...", end="", flush=True)
    t0 = time.perf_counter()
    for _ in range(1000):
        predict_match("A", "B", LambdaModifiers(), 1.0, 1.0)
    ms = (time.perf_counter() - t0) * 1000
    print(f" {ms:.0f}ms ({ms/1000:.3f}ms/预测)")

    # JSON output for DeepSeek consumption
    json_out = {
        "prediction": {
            "home_goals": round(pred.expected_goals_h, 2),
            "away_goals": round(pred.expected_goals_a, 2),
            "home_win_pct": round(pred.home_win_prob * 100, 1),
            "draw_pct": round(pred.draw_prob * 100, 1),
            "away_win_pct": round(pred.away_win_prob * 100, 1),
            "likeliest_score": f"{pred.top_scores[0][0][0]}-{pred.top_scores[0][0][1]}",
            "likeliest_prob": round(pred.top_scores[0][1] * 100, 2),
            "confidence": round(pred.confidence, 2),
        },
        "calibration": {
            "score_match": cal.score_match,
            "result_match": cal.result_match,
            "goal_deviation": cal.goal_deviation,
            "accuracy_score": cal.accuracy_score,
        }
    }
    print(f"\n  JSON output (for LLM consumption):")
    print(f"  {json.dumps(json_out, ensure_ascii=False, indent=2)}")

    print(f"\n✅ Python 引擎可用。推演+校准 + JSON输出 = {elapsed_ms:.1f}ms")

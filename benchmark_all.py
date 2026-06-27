"""
全模型压力测试 — 4 模型 × 3 任务 × 3 轮
"""
import requests, json, re, time, sys, io, os
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

# ---- Config ----
with open(".streamlit/secrets.toml", "r", encoding="utf-8") as f:
    raw = f.read()
clean = re.sub(r"=\s*'''[\s\S]*?'''", "= ''", raw)
s = {}
for line in clean.split("\n"):
    m = re.match(r'^(\w+)\s*=\s*"([^"]*)"\s*(?:#.*)?$', line.strip())
    if m: s[m.group(1)] = m.group(2)
KEY = s["default_deepseek_key"]
URL = "https://api.deepseek.com/v1/chat/completions"

MODELS = [
    ("deepseek-v4-flash", {}, 4096),
    ("deepseek-v4-pro", {}, 4096),
    ("deepseek-v4-pro", {"reasoning_effort": "max"}, 6000),
    ("deepseek-chat", {}, 4096),
    ("deepseek-reasoner", {}, 4096),
]

# ---- Tasks ----
TASKS = {
    "search": {
        "system": "You are a football data analyst. Generate a structured pre-match report with: lineup, injuries, odds, h2h, coach comments.",
        "user": "Pre-match data for Senegal vs Netherlands (World Cup Group Stage):\n- Senegal 4-3-3: Mendy; Sabaly, Koulibaly, Diallo; Gueye, Kouyate, Ndiaye; Sarr, Dia, Diatta\n- Injuries: Sadio Mane OUT\n- Netherlands 3-4-1-2: Noppert; Timber, Van Dijk, Ake; Dumfries, De Jong, Blind; Gakpo; Bergwijn, Memphis\n- Coach Van Gaal: aggressive approach\n- Odds: Netherlands 1.65, Draw 3.80, Senegal 5.50\n\nGenerate a pre-match report.",
        "quality_check": lambda c: len(c) > 300 and any(kw in c.lower() for kw in ["lineup", "odds", "injur"]),
    },
    "analysis": {
        "system": "You are a football analyst. Based on pre-match data, predict: coach intention (L1-L5), tactical factors, final score, goal scorers. Use Poisson model. Output in Chinese, structured format.",
        "user": "Pre-match data for Senegal vs Netherlands:\nSenegal (4-3-3): Mendy; Sabaly, Koulibaly, Diallo; Gueye, Kouyate, Ndiaye; Sarr, Dia, Diatta\nInjuries: Sadio Mane OUT\nNetherlands (3-4-1-2): Noppert; Timber, Van Dijk, Ake; Dumfries, De Jong, Blind; Gakpo; Bergwijn, Memphis\nCoach Van Gaal: aggressive attacking\nOdds: Netherlands 1.65, Draw 3.80, Senegal 5.50\nH2H: First meeting\n\nPredict the match outcome.",
        "quality_check": lambda c: len(c) > 400 and any(kw in c for kw in ["L1", "L2", "L3", "L4", "L5", "比分", "进球", "战术"]),
    },
    "calibration": {
        "system": "You are a post-match calibration AI. Compare prediction with actual result. Output: accuracy score (0-100), deviation analysis, new betting laws in JSON.",
        "user": "Pre-match prediction: Netherlands 2-0 Senegal\nActual result: Netherlands 2-0 Senegal (Gakpo 84', Klaassen 90+9')\nStatistics: NED 55% possession, 10 shots, 3 on target. SEN 6 shots, 1 on target.\n\nAnalyze calibration accuracy and extract new laws as JSON.",
        "quality_check": lambda c: len(c) > 200 and any(kw in c for kw in ["accuracy", "json", "score", "偏差"]),
    },
    "extract_params": {
        "system": _LAW_WEIGHT_PROMPT,
        "user": f"## 赛前数据报告\n{MOCK_SEARCH[:4000]}\n\n## 定律库\n{LAWS_JSON[:2000]}",
        "quality_check": lambda c: "merged_modifiers" in c and "law_effects" in c,
    },
}

# Need mock data for extract_params
MOCK_SEARCH = """
Senegal vs Netherlands — World Cup Group Stage
Senegal Predicted XI (4-3-3): Mendy; Sabaly, Koulibaly, Diallo; Gueye, Kouyate, Ndiaye; Sarr, Dia, Diatta
Major injury: Sadio Mane ruled out (knee)
Netherlands Predicted XI (3-4-1-2): Noppert; Timber, Van Dijk, Ake; Dumfries, De Jong, Blind; Gakpo; Bergwijn, Memphis
Coach Van Gaal: "We are here to win, not participate"
Odds: Netherlands 1.65, Draw 3.80, Senegal 5.50
Head-to-head: First meeting between the two nations
"""

LAWS_JSON = json.dumps([
    {"id":1,"name":"核心缺阵 ≠ 进攻归零","content":"核心缺阵会改变进球方式，而非消灭进球","trigger":"核心球员被确认无法上场","lambda_effect":"进攻λ -0.3 至 -0.5","status":"active"},
    {"id":7,"name":"强队攻坚乏力可预测","content":"面对密集防守，强队久攻不下是常态","trigger":"弱队从开场就摆出铁桶阵","lambda_effect":"强队进攻λ -0.2","status":"active"},
    {"id":8,"name":"早期领先后效率下降","content":"强队早早领先后会主动降速","trigger":"强队进球时间 < 30'","lambda_effect":"强队后续进球λ -0.3","status":"active"},
    {"id":14,"name":"核心伤疑 ≠ 核心缺阵","content":"区分完全缺阵、替补待命、带伤首发","trigger":"赛前核心球员出战成疑","lambda_effect":"根据最终状态进行评估","status":"active"},
], ensure_ascii=False, indent=2)

# Need _LAW_WEIGHT_PROMPT for extract task
_LAW_WEIGHT_PROMPT = """You are a football quantitative analyst. Check each law against the match context. Output JSON with law_effects array and merged_modifiers. For each law: law_id, law_name, applies (bool), modifiers (dict of key->float), reason (string)."""


def run_test(task_name, system_prompt, user_query, quality_check):
    print(f"\n{'='*60}")
    print(f"  {task_name}")
    print(f"{'='*60}")

    best = None
    for model, extra, max_tok in MODELS:
        payload = {"model": model, "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_query},
        ], "max_tokens": max_tok}
        payload.update(extra)

        tag = model.split("-")[-1]
        if extra.get("reasoning_effort"):
            tag += "/max"

        results = []
        for run in range(3):
            start = time.perf_counter()
            try:
                resp = requests.post(URL,
                    headers={"Authorization": f"Bearer {KEY}", "Content-Type": "application/json"},
                    json=payload, timeout=90)
                elapsed = time.perf_counter() - start
                data = resp.json()
                if "error" in data:
                    results.append({"error": data["error"].get("message", "")[:80], "latency": elapsed})
                    continue
                content = data["choices"][0]["message"].get("content", "")
                usage = data.get("usage", {})
                cd = usage.get("completion_tokens_details", {}) or {}
                results.append({
                    "latency": elapsed,
                    "prompt_tokens": usage.get("prompt_tokens", 0),
                    "completion_tokens": usage.get("completion_tokens", 0),
                    "reasoning_tokens": cd.get("reasoning_tokens", 0),
                    "output_len": len(content),
                    "quality": quality_check(content),
                })
            except Exception as e:
                results.append({"error": str(e)[:80], "latency": time.perf_counter() - start})

        # Aggregate
        ok = [r for r in results if "error" not in r]
        if not ok:
            print(f"  [{tag:10s}] ALL 3 FAILED: {results[0].get('error','?')}")
            continue

        avg_lat = sum(r["latency"] for r in ok) / len(ok)
        avg_pt = sum(r["prompt_tokens"] for r in ok) / len(ok)
        avg_ct = sum(r["completion_tokens"] for r in ok) / len(ok)
        avg_rt = sum(r.get("reasoning_tokens", 0) for r in ok) / len(ok)
        avg_out = sum(r["output_len"] for r in ok) / len(ok)
        quality_rate = sum(1 for r in ok if r["quality"]) / len(ok)

        # Cost estimate (per 1M tokens)
        prices = {
            "flash": (0.14, 0.28),
            "pro": (0.27, 1.10),
            "chat": (0.14, 0.28),
            "reasoner": (0.14, 0.28),
        }
        pin, pout = prices.get(tag.split("/")[0], (0.14, 0.28))
        cost = (avg_pt / 1e6 * pin + avg_ct / 1e6 * pout)
        if avg_rt > 0:
            cost += avg_rt / 1e6 * pout  # reasoning tokens bill as output

        print(f"  [{tag:10s}] lat={avg_lat:5.1f}s | tok=p{avg_pt:.0f}/c{avg_ct:.0f}/r{avg_rt:.0f} | "
              f"out={avg_out:.0f}ch | qual={quality_rate*100:.0f}% | ${cost:.5f}")

        # Track best (lowest cost with quality >= 67%)
        if quality_rate >= 0.67:
            if best is None or avg_lat < best["latency"]:
                best = {"model": tag, "latency": avg_lat, "cost": cost, "quality": quality_rate}

    if best:
        print(f"  >>> BEST: {best['model']} ({best['latency']:.1f}s, ${best['cost']:.5f}, qual={best['quality']*100:.0f}%)")
    else:
        print(f"  >>> ALL FAILED quality check")

    return best


if __name__ == "__main__":
    print("=" * 60)
    print("  DeepSeek 全模型压力测试")
    print("  4 models x 3 tasks x 3 runs = 36 API calls")
    print("=" * 60)

    choices = {}
    for task_key, task in TASKS.items():
        result = run_test(task_key, task["system"], task["user"], task["quality_check"])
        choices[task_key] = result

    print(f"\n{'='*60}")
    print(f"  最终推荐")
    print(f"{'='*60}")
    for task, best in choices.items():
        if best:
            print(f"  {task:25s} → {best['model']:10s} ({best['latency']:.1f}s, ${best['cost']:.5f})")
        else:
            print(f"  {task:25s} → FALLBACK TO v4-flash")

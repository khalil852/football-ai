# 全维推演工厂

基于 AI 的足球比赛推演与赛后校准系统。

## 工作流

1. **信息雷达** — 搜集赛前数据（阵容、伤病、赔率、教练发言）
2. **推演引擎** — 基于泊松分布数学模型 + 定律库进行比分推演
3. **赛后校准** — 对比真实结果，抽取新定律，修正模型

## 数据源

| 层级 | 来源 | 说明 |
|------|------|------|
| Tier 1 | API-Football | 结构化足球数据（阵容/事件/统计/赔率） |
| Tier 2 | Tavily Search | AI 驱动的联网搜索（教练发言/新闻） |
| Tier 3 | 模型知识库 | DeepSeek 训练数据 |

## 部署

在 Streamlit Cloud 的 Secrets 中配置：

```toml
SUPABASE_URL = "https://xxx.supabase.co"
SUPABASE_KEY = "sb_secret_xxx"
default_deepseek_key = "sk-xxx"
default_tavily_key = "tvly-xxx"
football_api_key = "xxx"
analysis_prompt = '''...'''
```

详细配置见 `app.py` 顶部注释。

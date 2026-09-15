# 阶段契约

每个阶段的结构固定为：

```
取数(fetch) → data/*.csv 通过 gate → 写 analysis/*.md → 阶段通过
```

分析文件在数据 gate 通过前**不允许**存在 —— `pipeline.py check` 会先算数据校验，
只有全部数据校验为真时才会去检查 `analysis/` 文件是否存在。

run 目录结构：

```
<run-dir>/
  state.json          阶段状态机
  raw/                每个 API 调用的原始响应（可复现的底稿）
  data/               规范化后的 CSV（gate 校验对象）
  analysis/           结论笔记 + gate_<S>.json 判定结果
  out/                最终交付物
```

---

## S0 环境与数据源自检

**数据**：`data/00_source_capability.csv`

| 列 | 说明 |
|---|---|
| source | tikhub / sellersprite / sorftime / apify / web / browser |
| kind | mcp / mcp+rest / cli / tool |
| tier | 1–4，对应降级链层级 |
| status | ok / degraded / error |
| tool_count | MCP 工具数（非 MCP 源为空） |
| notes | 错误信息或说明 |
| captured_at | 采集时间 |

**取数**：`pipeline.py fetch --stage S0`（自动，等价于 `mcp_call.py check`）。

**Gate**：
- 文件存在且列齐
- 至少 1 个 `status in (ok, degraded)` 的一级源（mcp / mcp+rest / cli）

**分析**：`analysis/00_plan.md` —— 由 fetch 自动生成，含数据源状态表、降级链、执行计划。

**典型结果**：apify 常因缺 `APIFY_TOKEN` 显示 `degraded`（可见但不可用），这是正常的。

---

## S1 圈层热词发现

**数据**：`data/01_seed_keywords.csv`

| 列 | 说明 |
|---|---|
| keyword | 热词原文 |
| keyword_type | `identity` / `scene` / `product` / `aesthetic` —— **必须有 identity** |
| platform | tiktok / instagram / pinterest / youtube / google |
| heat_score | 热度分（0–100，相对值，注明口径） |
| heat_cagr_pct | 观测窗口内的复合增长率 % |
| obs_window | 观测窗口，如 `2022-01..2024-12` |
| source_tier | 实际命中的降级层级 |
| captured_at | 采集时间 |
| evidence_url | 可点开的证据链接 |

**取数配方**：
1. 一级 —— tikhub MCP `demo_*` / `hybrid_video_data`；真正的全量数据走 TikHub REST
   （`mcp_call.py rest --source tikhub --path ...`），例如
   `/api/v1/tiktok/web/fetch_general_search?keyword=<kw>`
2. 二级 —— Apify 的 TikTok / Instagram scraper actor
3. 三级 —— WebFetch 抓公开标签页、热词榜、行业博客
4. 四级 —— browser-skill 操作已登录浏览器

**Gate**：
- ≥8 行，列齐
- `keyword_type` 的取值集合里含 `identity`
- 100% 的行带 `source_tier` + `captured_at`

**分析**：`analysis/01_seed_notes.md` —— 说明入口热词是什么、为什么它对应的品类已经红海、
下一步要找的"进化垂类"是什么。

---

## S2 趋势验证（Trend 还是 Fad）

**数据**：
- `data/02_trend_timeseries.csv`：`keyword, period, value, source, source_tier, captured_at`
  （`period` 用 `YYYY-MM`）
- `data/02_trend_summary.csv`：`keyword, n_periods, first_value, last_value, cagr_pct, seasonality_index, trend_verdict, source_tier`

**取数**：`pipeline.py fetch --stage S2`（自动）
调用 SellerSprite `google_trend`（`marketplace` + `monthly: true`），逐词取回月度数，
再本地算 CAGR 与季节性指数。

**判定规则**（写在 `pipeline.py::_trend_summary`）：

| 判定 | 条件 |
|---|---|
| `Trend` | CAGR ≥ 10% **且** 期数 ≥ 24 |
| `Fad` | CAGR < 0 |
| `Unclear` | 其余 |

季节性指数 = `(月均值的极差 / 全期均值) × 100`，用于区分"指数高是因为季节性波动"还是"结构性增长"。

**Gate**：
- 时间序列 ≥48 行、汇总 ≥3 行
- 至少 1 个词判为 `Trend`（注意：**全部判 Fad 也是一个有效结论**，此时应直接出报告说明"不该做"，
  而不是放宽阈值）
- 每个词观测期数 ≥24

**分析**：`analysis/02_trend_notes.md` —— fetch 会自动写；若某些词取不到数据，
必须在笔记里列出并说明走了哪一级降级。

**已知问题**：SellerSprite `google_trend` 对部分长尾多词短语返回空序列
（例如 `pilates girl`、`what i eat in a day`）。这类词需要改走
`sorftime keyword_trend`、WebFetch，或降级到 browser-skill。

---

## S3 场景与子圈层锁定

**数据**：
- `data/03_scene_keywords.csv`：`scene_keyword, parent_keyword, platform, frequency, evidence_type, source_url, source_tier, captured_at`
- `data/03_subcommunity.csv`：`subcommunity, defining_keywords, audience_size_signal, why_picked, source_tier`

`evidence_type` 用 `评论` / `口播` / `标签` / `视频画面` / `长视频标题` 之一 ——
**评论区高频词的价值高于视频正文**，这是本方法论的关键点，必须能区分。

**取数配方**：抓取高互动微网红（粉丝 5,000–50,000）的 20 分钟以上长视频及其全部评论，
提取高频场景词；再用高频词去搜出新的同类创作者，形成标签 → 行为网络 → 下一个标签的遍历。

**Gate**：场景词 ≥10 行；子圈层 ≥1 行且 `why_picked` 非空。

**分析**：`analysis/03_subcommunity_notes.md` —— 说明为什么这个子圈层值得做，
以及被放弃的对照词（例如声量更大但无法收敛出单品的宽泛标签）。

---

## S4 语料池构建与痛点聚类

**数据**：
- `data/04_corpus_index.csv`：`doc_id, platform, creator, followers, url, text_chars, captured_at`
- `data/04_painpoints.csv`：`painpoint_id, painpoint, category, mentions, share_pct, example_quote, evidence_url, source_tier`

`category` 建议用：`生理/动作`、`美学`、`感官/卫生`、`供给缺口`、`质量`、`功能`。

**取数配方**：
1. 定向爬取赞转藏排行前 50 名的高互动微网红完整长视频 + 全部评论
2. 音频转高精度文字稿（TikTok/YouTube 字幕或 Whisper），评论合并进同一文本池
3. 汇编成数十万字原生语料库
4. 让大模型做痛点聚类，扮演"直面消费者的产品策略师" —— 做语义解构 + 词频关联

**Gate**：
- 语料索引 ≥10 篇
- 痛点 ≥3 条
- 提及总数 ≥100（**样本量不够时占比没有意义，这里必须先卡住**）
- Top 痛点占比 ≥20%（达不到说明痛点分散，还不构成可立项的信号）
- 痛点类别 ≥2 种

**分析**：`analysis/04_painpoint_notes.md` —— Top 痛点指向哪个载体、
这个载体为什么适合小单快返、以及美学类痛点是否提供了溢价空间。

---

## S5 电商侧市场验证

**数据**：
- `data/05_market_overview.csv`：`marketplace, category, category_node_id, sample_scope, product_count, monthly_units, monthly_revenue_usd, avg_price, avg_profit_pct, top3_brand_share_pct, top10_brand_share_pct, brand_count, new_product_share_pct, return_rate_pct, search_to_purchase_ratio, source_tool, source_tier, captured_at`
- `data/05_price_band.csv`：`marketplace, category, price_band, product_count, units_share_pct, avg_rating, avg_reviews, source_tool, source_tier`
- `data/05_competitors.csv`：`marketplace, asin, title, brand, seller, price, monthly_units, monthly_revenue_usd, rating, reviews, bsr, listing_date, search_tool, source_tier, captured_at`

**取数**：`pipeline.py fetch --stage S5`（自动，SellerSprite `market_research` + `product_research`）。

**Gate**：
- 三个文件均存在且列齐
- 月销售额 ≥ $1,000,000（低于此说明盘子太小）
- 头部品牌集中度 ≤ 70%（高于此说明新品牌进不去）
- 竞品 ≥10 行

> 阈值可通过 `config.local.json` 的 `gate_defaults` 调整，但调整要在分析笔记里写明原因。

**分析**：`analysis/05_market_notes.md` —— 市场够不够大、有没有被锁死、
价格带结构、以及**哪些数字是第三方估算、不能直接当结论用**。

---

## S6 单品收敛与机会评分

**数据**：`data/06_candidate_scores.csv`

`candidate, trend_score, painpoint_score, market_score, competition_score, economics_score, total_score, verdict, evidence_refs`

- `verdict` ∈ `Go` / `Hold` / `Kill`
- `evidence_refs` **必填**，格式如 `S2:trend(pilates grip socks,+26.9%) S4:P1-P6 S5:market_overview`

**Gate**：≥3 候选；`total_score` 全部可解析为数字；**100% 的行有 `evidence_refs`**。
最后一条是这套流水线的核心：不允许出现无法回溯到原始数据行的评分。

**分析**：`analysis/06_recommendation.md` —— 逐候选给出 Go/Hold/Kill 的理由，
并明确"建议只对哪一个进入打样"。

---

## S7 交付

**取数**：`pipeline.py finalize`（自动调用 `build_excel.py` + `build_report.py`）。

**产出**：
- `out/selection_data.xlsx`
- `out/selection_report.html`

**Gate**：
- 两个文件都存在且非空
- 报告内含结论段落（`结论` 关键词）
- 报告内含 ≥1 个图表（`<svg>`）

S7 不在报告的 gate 表里显示 —— 报告本身就是 S7 的产物，否则每份报告都会报"自己缺失"。

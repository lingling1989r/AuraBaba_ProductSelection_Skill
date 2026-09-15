---
name: social-keyword-selection
description: |
  社媒 × 关键词结合的跨境选品方法论，落成一条带分阶段结果验证的可执行流水线。

  **当以下情况时使用此 Skill**：
  (1) 用户要做「选品」「爆品挖掘」「找新品方向」「选品方法论落地」
  (2) 用户提到「社媒选品」「关键词选品」「圈层选品」「TikTok/Instagram 找机会」
  (3) 用户要「验证一个品类是真趋势还是一阵风」「Trend vs Fad」
  (4) 用户要「跑一套选品数据分析」，并要 HTML 报告 / Excel 数据交付
  (5) 用户提到 tikhub / 卖家精灵 / SellerSprite / Sorftime / Apify 之一，并要做选品分析

  **DO NOT TRIGGER**：纯广告投放优化、纯 Listing 文案撰写、已有明确单品只做站内运营、
  与选品无关的一般数据分析。

  **产出**：分阶段校验通过后，输出一份带结论的 HTML 报告 + 一个承载全量数据的 Excel。
user-invocable: true
allowed-tools: Bash(aura *), Bash(python3 *), Read, Write, Edit, Glob, Grep, WebFetch, WebSearch
---

# 社媒 × 关键词选品流水线

把「不看热销榜、先找圈层」的选品方法论，变成一条**先出数据、再出分析、每阶段有机器可验证 gate** 的流水线。

## 核心方法论（五步选品链）

关键词在这个方法里是**人群身份词**，不是商品搜索词。

| 步骤 | 做什么 | 关键词的角色 |
|---|---|---|
| S1 | 从目标人群的社媒里找 **CAGR 最高的身份热词** | 圈层入口，不是商品词 |
| S2 | 时间序列验证 Trend / Fad | 用趋势数据决定要不要往下走 |
| S3 | 顺着热词进入评论区与微网红口播的**行为网络** | 关键词扩散，收敛出子圈层 |
| S4 | 把真实语言做成语料池，做**痛点聚类** | 词频决定做哪个单品 |
| S5 | 用电商数据做**压力测试** | 验证市场没被锁死 |
| S6 | 合成评分，收敛候选 | 每个分数必须可溯源 |

关键判断：**平台卖爆的必然是红海** —— 所以起点永远不是"什么卖得好"，而是"谁正在变热、她们在用什么词说自己"。

## 两条不可违背的原则

1. **先出数据再分析。** 每个阶段的 `analysis/*.md` 只有在对应的 `data/*.csv` 通过 gate 之后才会被校验。
   `pipeline.py check` 会强制这一点：数据表缺列/行数不够时，分析产物一律不通过。
2. **降级必须留痕。** 每条数据行都要带 `source_tier` 和 `captured_at`。
   报告最后会明确写出哪些数字来自低层级来源 —— 命中的层级越低，结论越要打折。

## 数据源与降级链

一级（结构化、有 schema）：

- **tikhub** — 社媒（TikTok / Instagram / YouTube / 小红书 / 抖音 …）
- **卖家精灵 SellerSprite** — Amazon（45 个工具，市场/关键词/ABA/趋势）
- **Sorftime** — 多平台电商 + 站外（Amazon / TikTok Shop / Shopee / Temu / Walmart / 1688 / Reddit，105 个工具）

降级顺序（每一步都必须按序尝试）：

```
MCP(一级) → Apify(二级) → WebFetch/WebSearch(三级) → 浏览器操作(四级，最后手段)
```

细节与踩坑记录见 [references/data-sources.md](references/data-sources.md) 和
[references/fallback-chain.md](references/fallback-chain.md)。

## 快速开始

```bash
SK=.claude/skills/social-keyword-selection        # 或 skill 在本机的实际路径

# 0. 连通性自检（必做，先确认哪些源真的活着）
python3 $SK/scripts/mcp_call.py check

# 1. 初始化一次选品 run
python3 $SK/scripts/pipeline.py --run-dir ./selection_run init \
  --topic "北美普拉提女孩圈层 防滑袜选品验证" \
  --market US \
  --category "Sports & Outdoors" \
  --product-keyword "pilates grip socks" \
  --seed "that girl" --seed "pilates girl" --seed "reformer pilates"

# 2. 按阶段推进：fetch 能自动取数的，其余按契约补齐后 check
python3 $SK/scripts/pipeline.py --run-dir ./selection_run fetch --stage S0
python3 $SK/scripts/pipeline.py --run-dir ./selection_run check --stage S0
python3 $SK/scripts/pipeline.py --run-dir ./selection_run next      # 下一个未通过的阶段

# 3. 全部通过后出交付物
python3 $SK/scripts/pipeline.py --run-dir ./selection_run check-all
python3 $SK/scripts/pipeline.py --run-dir ./selection_run finalize
```

`finalize` 产出：

```
./selection_run/out/selection_report.html   # 带结论的 HTML 报告
./selection_run/out/selection_data.xlsx     # 全量数据（15 个 sheet）
```

## 阶段总览

| 阶段 | 名称 | 数据产出 | 自动取数 | 关键 gate |
|---|---|---|---|---|
| S0 | 环境与数据源自检 | `00_source_capability.csv` | ✅ | ≥1 个一级源可用 |
| S1 | 圈层热词发现 | `01_seed_keywords.csv` | 半自动 | ≥8 词；含 `identity` 类型；溯源完整 |
| S2 | 趋势验证 | `02_trend_timeseries.csv` + `_summary.csv` | ✅ | ≥3 词；每个 ≥24 期；≥1 个判 Trend |
| S3 | 场景与子圈层锁定 | `03_scene_keywords.csv` + `_subcommunity.csv` | 人工 | ≥10 场景词；≥1 子圈层 |
| S4 | 语料池与痛点聚类 | `04_corpus_index.csv` + `_painpoints.csv` | 人工 | 提及总数 ≥100；Top 痛点占比 ≥20%；≥2 个类别 |
| S5 | 电商侧市场验证 | `05_market_overview/price_band/competitors.csv` | ✅ | 月销售额 ≥$1M；头部集中度 ≤70%；≥10 竞品 |
| S6 | 单品收敛与机会评分 | `06_candidate_scores.csv` | 人工/派生 | ≥3 候选；分数可解析；每行有 `evidence_refs` |
| S7 | 交付 | `out/*.xlsx` + `out/*.html` | ✅ | 两个文件存在；报告含结论与图表 |

每个阶段的完整字段契约、取数配方、失败处理见 [references/stages.md](references/stages.md)。

## 什么时候不该硬跑

- S0 显示所有一级源都不可用 → **停下来告诉用户**，不要用低层级数据硬凑一份看起来完整的报告。
- S2 全部判定为 Fad → 这是**有效结论**（不该做这个品），直接出报告说明，不要为了有产出而放宽阈值。
- Sorftime 配额耗尽（HTTP 200 但正文是「MCP使用次数已达到上限」）→ 视为该源不可用，降级到 SellerSprite。
  `mcp_call.py` 会把这种响应原样落盘，`pipeline.py` 用 `looks_like_quota_error()` 识别。

## 参考文档

- [references/stages.md](references/stages.md) — 每个阶段的字段契约、取数与 gate
- [references/data-sources.md](references/data-sources.md) — 三个 MCP 源的工具清单与踩坑
- [references/fallback-chain.md](references/fallback-chain.md) — 四级降级链的执行与留痕
- [references/verification.md](references/verification.md) — gate 语义、如何加校验、如何判"降级通过"
- [references/report-spec.md](references/report-spec.md) — HTML 报告与 Excel 的产出规范

## 敏感信息

`config.local.json` 存放三个数据源的 endpoint 与密钥，已在 `.gitignore` 中排除 ——
仓库里只有 `config.local.example.json` 模板，首次使用先 `cp` 一份再填自己的 key。
**不要把里面的密钥复制到 SKILL.md、报告、issue 评论或任何会被转发的地方。**
轮换密钥时只改这一个文件。也可以用环境变量 `SKS_CONFIG` 指向另一份配置。

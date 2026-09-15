# social-keyword-selection

把「不看热销榜、先找圈层」的跨境选品方法论，落成一条**先出数据、再出分析、每阶段有机器可验证 gate** 的可执行流水线。

关键词在这套方法里是**人群身份词**，不是商品搜索词 —— 起点不是"什么卖得好"，而是"谁正在变热、她们在用什么词说自己"。

## 五步选品链

| 步骤 | 做什么 | 关键词的角色 |
|---|---|---|
| S1 | 从目标人群的社媒里找 **CAGR 最高的身份热词** | 圈层入口，不是商品词 |
| S2 | 时间序列验证 Trend / Fad | 用趋势数据决定要不要往下走 |
| S3 | 顺着热词进入评论区与微网红口播的**行为网络** | 关键词扩散，收敛出子圈层 |
| S4 | 把真实语言做成语料池，做**痛点聚类** | 词频决定做哪个单品 |
| S5 | 用电商数据做**压力测试** | 验证市场没被锁死 |
| S6 | 合成评分，收敛候选 | 每个分数必须可溯源 |

## 两条不可违背的原则

1. **先出数据再分析。** 每个阶段的 `analysis/*.md` 只有在对应的 `data/*.csv` 通过 gate 之后才会被校验。
2. **降级必须留痕。** 每条数据行都要带 `source_tier` 和 `captured_at`，报告最后会明确写出哪些数字来自低层级来源。

## 八个阶段与 gate

| 阶段 | 数据产出 | 自动取数 | 关键 gate |
|---|---|---|---|
| S0 | 数据源自检 | ✅ | ≥1 个一级源可用 |
| S1 | 圈层热词 | 半自动 | ≥8 词；含 `identity` 类型；溯源完整 |
| S2 | 趋势时间序列 + 汇总 | ✅ | ≥3 词；每个 ≥24 期；≥1 个判 Trend |
| S3 | 场景词 + 子圈层 | 人工 | ≥10 场景词；≥1 子圈层 |
| S4 | 语料索引 + 痛点聚类 | 人工 | 提及总数 ≥100；Top 痛点占比 ≥20%；≥2 个类别 |
| S5 | 市场概览 + 价格带 + 竞品 | ✅ | 月销售额 ≥$1M；头部集中度 ≤70%；≥10 竞品 |
| S6 | 候选评分 | 人工/派生 | ≥3 候选；每行有 `evidence_refs` |
| S7 | 交付 | ✅ | HTML 报告 + Excel 均存在 |

阈值集中在 `config.local.json` 的 `gate_defaults`，可按品类调 —— 但调阈值必须在阶段分析笔记里写明为什么。

## 数据源与降级链

一级（结构化、有 schema）：**tikhub**（社媒）、**卖家精灵 SellerSprite**（Amazon）、**Sorftime**（多平台电商 + 站外）。

```
MCP(一级) → Apify(二级) → WebFetch/WebSearch(三级) → 浏览器操作(四级，最后手段)
```

严格按序尝试，每次降级写进数据行的 `source_tier`。

## 安装

```bash
git clone https://github.com/lingling1989r/AuraBaba_SocialKeyword_Skill.git \
  .claude/skills/social-keyword-selection

cd .claude/skills/social-keyword-selection
cp config.local.example.json config.local.json   # 填入你自己的密钥
pip3 install openpyxl
python3 scripts/mcp_call.py check                # 先自检数据源连通性
```

## 快速开始

```bash
SK=.claude/skills/social-keyword-selection

python3 $SK/scripts/pipeline.py --run-dir ./selection_run init \
  --topic "北美普拉提女孩圈层 防滑袜选品验证" \
  --market US \
  --category "Sports & Outdoors" \
  --product-keyword "pilates grip socks" \
  --seed "that girl" --seed "pilates girl" --seed "reformer pilates"

python3 $SK/scripts/pipeline.py --run-dir ./selection_run fetch --stage S0
python3 $SK/scripts/pipeline.py --run-dir ./selection_run check-all
python3 $SK/scripts/pipeline.py --run-dir ./selection_run finalize
```

`finalize` 产出：

```
./selection_run/out/selection_report.html   # 带结论的 HTML 报告（单文件、可离线打开）
./selection_run/out/selection_data.xlsx     # 全量数据（15 个 sheet）
```

报告和 Excel 都**只读** `data/*.csv`，不做任何独立计算 —— 报告里任何一个数字都能在 Excel 里找到对应行。

`examples/` 下有一份实测产物可以直接看效果。

## 文档

- [SKILL.md](SKILL.md) — 技能主文档
- [references/stages.md](references/stages.md) — 每个阶段的字段契约、取数与 gate
- [references/data-sources.md](references/data-sources.md) — 三个 MCP 源的工具清单与踩坑记录
- [references/fallback-chain.md](references/fallback-chain.md) — 四级降级链的执行与留痕
- [references/verification.md](references/verification.md) — gate 语义、如何加校验、反模式
- [references/report-spec.md](references/report-spec.md) — HTML 报告与 Excel 的产出规范

## 密钥

`config.local.json` 存放数据源 endpoint 与密钥，已在 `.gitignore` 中排除。
仓库里只有 `config.local.example.json` 模板。**不要把真实密钥提交进来。**

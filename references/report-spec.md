# 交付物规范

`pipeline.py finalize` 产出两份文件，缺一不可：

```
out/selection_report.html   # 带结论的报告（人看）
out/selection_data.xlsx     # 全量数据（机器/复核用）
```

## 原则：报告只能引用 Excel 里的数字

两个脚本都**只读** `data/*.csv`，不做任何独立计算来源。
因此报告里出现的数字必然能在 Excel 里找到对应行 —— 这是复核的入口。

不要手工编辑这两份产物。要改结论，改数据表或分析笔记，然后重跑 `finalize`。

---

## HTML 报告（`scripts/build_report.py`）

单文件、内嵌 CSS、无外部依赖、可离线打开与转发。

| 章节 | 内容 |
|---|---|
| 一、结论 | 由数据表算出的结论段落 + KPI 卡片 + 未闭环阶段的缺口清单 |
| 二、分阶段结果验证 | 每个阶段的 gate 判定、通过项数、状态、备注 |
| 三、趋势证据（S1–S2） | 热词表 + 时间序列折线图 + 逐词 Trend/Fad 判定 |
| 四、圈层与痛点证据（S3–S4） | 场景词表 + 子圈层 + 语料规模 + 痛点占比条形图 |
| 五、电商侧市场验证（S5） | 市场概览 + 价格带柱状图 + 竞品样本 |
| 六、单品收敛与机会评分（S6） | 总分条形图 + 带 Go/Hold/Kill 徽章的候选表 |
| 七、数据溯源与局限 | 数据源能力矩阵 + 降级链说明 + 局限清单 |
| 八、下一步 | 模板化建议（复算单元经济 / 小规模投放验证 / 重跑趋势） |

图表是**手写内联 SVG**（`line_chart` / `bar_chart`），不依赖任何 JS 库 ——
这样报告可以离线打开、可以进邮件、可以被任何渲染器解析。

### 结论是怎么生成的

`build()` 里从各表算出 `conclusions` 列表，全部有数据兜底：

- 趋势：从 `02_trend_summary.csv` 数 `trend_verdict == Trend` 的行，取 CAGR 最大者
- 痛点：从 `04_painpoints.csv` 取 `share_pct` 最大的一行
- 子圈层：取 `03_subcommunity.csv` 第一行
- 市场：取 `05_market_overview.csv` 第一行
- 候选：从 `06_candidate_scores.csv` 取 `total_score` 最大者

任何一张表为空，对应的结论就**不生成**，同时在"未闭环的阶段"里列出缺什么 ——
报告宁可少一段，也不会写一句没有数据支撑的话。

### 局限清单（固定输出）

报告第七节固定写出三条以上局限，包括：Google Trends 是相对指数不是绝对搜索量、
电商数据是第三方估算、社媒抽样受可抓取性影响、结论绑定采集时间戳。
这部分**不要删** —— 它是这套流水线对"先出数据再分析"承诺的一部分。

---

## Excel 工作簿（`scripts/build_excel.py`）

15 个 sheet，命名带序号便于按流程阅读：

| sheet | 来源 |
|---|---|
| `00_说明` | run 元信息 + 阶段产出概览 + 读法说明 |
| `01_数据源能力` | `data/00_source_capability.csv` |
| `02_热词发现` | `data/01_seed_keywords.csv` |
| `03_趋势时间序列` | `data/02_trend_timeseries.csv` |
| `04_趋势汇总` | `data/02_trend_summary.csv` |
| `05_场景关键词` | `data/03_scene_keywords.csv` |
| `06_子圈层` | `data/03_subcommunity.csv` |
| `07_语料索引` | `data/04_corpus_index.csv` |
| `08_痛点聚类` | `data/04_painpoints.csv` |
| `09_市场概览` | `data/05_market_overview.csv` |
| `10_价格带` | `data/05_price_band.csv` |
| `11_竞品` | `data/05_competitors.csv` |
| `12_机会评分` | `data/06_candidate_scores.csv` |
| `13_阶段验证` | 各阶段 gate 的逐条结果 |
| `14_溯源` | 每个文件的行数 + 各 `source_tier` 的行数分布 |

未产出的阶段不会缺 sheet，而是在该 sheet 里显式写 `(empty - stage not produced)` ——
**空缺必须看得见**，否则读者会以为这个维度被查过了。

表头冻结、列宽自适应、表头深蓝底白字，便于直接拿去汇报。

---

## 命名与产出位置

默认写到 run 目录的 `out/`。若用户要求交付到别处（项目文档、飞书、网盘），
**复制**过去而不是改脚本的输出路径 —— run 目录要保持自洽，
`gate_S7.json` 校验的也是 run 目录里的那份。

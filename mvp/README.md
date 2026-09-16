# MVP：社媒 × 平台 Trend 选品

一个可以直接点开的本地 Web MVP，把 skill 的方法论跑给人看。它补上 CLI 流水线**故意没有自动化**的三件事：

1. **看得见的下钻** —— S1 不是一份凭记忆敲的词表，而是一次下钻：种子词开一圈同圈层、
   更具体的词，下一级再走一轮人的筛选。机器排序的环会漂（`yoga mat`、`halloween`
   比真正的兄弟词 `pilates socks` 高 2–3 倍），所以**环永不自动入库**。
2. **看得见的趋势获取** —— 每个词按序走降级链（sellersprite → sorftime → 人工粘贴），
   记录每次尝试的层级、耗时、原始响应与失败原因，命中即停。
   每个数字都能点开看到它从哪个 tier 来。
3. **必须由人回答的问题** —— 每个阶段有一个机器不该替你回答的问题。
   没记录判断，阶段就不算走完；**改阈值必须写明理由，否则记录会被拒绝**。

## 跑起来

```bash
cd <skill>/mvp
python3 server.py            # 默认 8787，端口被占就 --port 8791
# 打开 http://127.0.0.1:8787
```

只用标准库，不需要装任何东西（写 Excel 才需要 `openpyxl`，MVP 不用）。

## 三个页签

### 圈层下钻（默认页签）

三个卡片，对应下钻的三个动作：

1. **下钻一圈** —— 选环（`social` 打 tikhub 出身份词 / `ecom` 打 SellerSprite `keyword_miner`
   出商品词）、填父词、选市场。结果是候选表，每行带共现频次或绝对搜索量 + 来源。
   表头挂着**漂移提醒**，因为候选的排序本身就是陷阱。
2. **人的筛选轮** —— 勾词、写筛选理由（**至少 6 个字，否则拒绝写入**）。
   理由是这个阶段唯一的产出：环不自动入库，下一级只能从人选过的词往下走。
3. **下钻历史** —— 每一轮的时间、环、父词、层级、采纳了什么、理由是什么。链可逐级回放。

两个平面**不能混**：`social` 出的是社区自称（`hotpilates`、`pilatesbody`），
`ecom` 出的是人在 Amazon 里敲的词（`pilates socks`、`grip socks`）。
实测 `pilates girl` 在 Amazon 有 56 个月搜索数据、其中 55 个月是 0 —— 身份词
本来就不是搜索词，它走社媒平面验证。

第一次下钻时父词就是种子：面板会把它记进 `state.json`（`social` → `seed_keywords`，
`ecom` → `product_keyword`），这样 level 1 的父词能一路回溯到 level 0。

### 趋势获取

- **数据源状态**：点一下真实探测三个 MCP 的连通性与工具数，不猜。
- **趋势数据获取**：填身份热词（不是商品词）→ 逐个走降级链。
  每个词展示：降级链走位图、请求参数、每次尝试的状态/耗时/原始响应片段、
  解析出的月度序列、手写 SVG 折线图、CAGR / 季节性指数 / Trend-Fad 判定。
- **人工兜底**：一级二级都拿不到时，从公开页粘贴 `2023-01,42` 每行一条或直接粘 JSON，
  落库时 `source_tier` 记成 `web` —— 报告里会如实标注这个数字来自降级。

### 阶段推进与人工判断

左边是 S0–S7 阶段列表与决策记录，右边是当前阶段的：

- **目标**与**降级链**
- **输入契约** —— 上一阶段的产物必须满足哪些列，缺列直接卡住
- **筛选阈值** —— 来自 `config.local.json` 的 `gate_defaults`
- **人判断卡片** —— 见下

| 阶段 | 人的问题 | 交互形态 |
|---|---|---|
| S0 | 有源不可用，降级继续还是停下？ | 二选一 |
| S1 | 要进的是哪一个圈层？ | 从已取到趋势的词里选一个 |
| S2 | Trend / Fad 逐词复核 | 每词「接受机器判定 / 改判 Trend / 改判 Fad / 弃用」 |
| S3 | 这个子圈层值得做吗？ | 选定或手填 |
| S4 | Top 痛点指向哪个载体？ | 手填 |
| S5 | 市场够大、没被锁死吗？ | 继续 / Kill |
| S6 | 只对哪一个候选打样？ | 手填 |
| S7 | 无（脚本产出） | — |

## API

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/bootstrap` | 阶段契约、阈值、数据源状态、趋势缓存、上次取数结果 |
| GET | `/api/health` | 存活探测 |
| POST | `/api/sources/probe` | 真实探测三个 MCP |
| POST | `/api/run/name` | `{topic}` 给这一轮调研命名（记录名，只做标识） |
| POST | `/api/drill` | `{ring, parent, market, count}` 下钻一圈。ring 不合法 / 未命名 / 缺 market / 缺 parent 都返回 400 |
| POST | `/api/drill/accept` | `{ring, parent, market, words[], note}` 采纳这一轮。没勾词或 `note` 短于 6 个字返回 400 —— 下钻环不自动入库 |
| POST | `/api/trend/fetch` | `{keywords:[], market}` 逐词走降级链。调研未命名 / 缺 market / 缺 keywords 都返回 400 —— 不用默认值兜 |
| POST | `/api/trend/manual` | `{keyword, raw, market}` 人工粘贴，记 `tier=web`。`market` 同样必填 |
| POST | `/api/decision` | `{stage, action, target, value, note}` 记录人的判断 |
| POST | `/api/decision/clear` | `{stage}` 撤销 |

## 落盘

```
mvp/run/
  meta.json         本次调研的名称（记录名，用户填）
  state.json        真正的 pipeline run 状态（含两个根：seed_keywords / product_keyword）
  data/             下钻写入的 S1 表 —— 被 pipeline.py 的 gate 直接校验
  analysis/         下钻笔记 + gate 判定结果
  decisions.json    人的判断（含理由与时间戳）
  trend_cache.json  每个词最终命中的序列与判定
  last_trend.json   上一次取数的完整过程（含降级链走位），刷新后仍可回放
  last_drill.json   上一次下钻的完整过程（候选 + 环 + 命中层级）
  drill_log.json    下钻历史：每轮的父词、层级、采纳词与筛选理由
  raw/              每个词的原始 MCP 响应，可复现的底稿
```

下钻不是 MVP 自己的一套数据 —— 面板里的 `/api/drill/accept` 走的是 `drill.py::accept`，
写进 `data/01_seed_keywords.csv`，和 CLI 完全同一条路径。所以面板里选完词之后
可以直接 `python3 ../scripts/pipeline.py --run-dir run check --stage S1` 验。

`mvp/run/` 是运行产物，已在 `.gitignore` 里。

## 这个 MVP 没做什么

诚实说明边界：S3/S4/S6 的候选来自语料抓取与市场分析，MVP 里没有接入，
这几步的决策框是手填 —— 目的是先把**交互形态**、**下钻**和**趋势获取**立起来，
而不是假装语料聚类已经自动化了。

下钻侧的两个源是真实调用（tikhub REST / SellerSprite MCP），**会消耗配额**，
没有做结果缓存：同一个父词钻两次就是两次调用。演示时注意。

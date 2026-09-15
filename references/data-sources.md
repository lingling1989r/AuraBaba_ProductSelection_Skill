# 数据源清单与踩坑记录

所有 endpoint 与密钥集中放在 skill 根目录的 `config.local.json`。
**不要**把密钥复制到 SKILL.md、报告、issue 评论里。

验证连通性：

```bash
python3 scripts/mcp_call.py check                       # 全部源
python3 scripts/mcp_call.py check --source sorftime     # 单个源
python3 scripts/mcp_call.py list  --source sellersprite # 列出工具
```

`check` 会把能力矩阵写成 JSON（`--out path`），S0 阶段用的就是它。

---

## 一级源

### tikhub —— 社媒

| 项 | 值 |
|---|---|
| MCP | `https://mcp.tikhub.io/tikhub/mcp`（路径是 `/tikhub/mcp`，不是 `/mcp`） |
| 认证 | `Authorization: Bearer <api_key>` |
| REST | `https://api.tikhub.io`（同一个 key） |
| 覆盖 | TikTok / Instagram / YouTube / 小红书 / 抖音 / 微博 / 快手 / 西瓜 |

**关键事实**：
- MCP 只暴露 **23 个工具**，且大部分是 `demo_*`（固定样本、1 小时缓存）和用户信息类。
  真正可用的通用入口是 `hybrid_video_data`。
- **完整数据面在 REST API**。用 `mcp_call.py rest` 直接打：
  ```bash
  python3 scripts/mcp_call.py rest --source tikhub \
    --path /api/v1/tiktok/web/fetch_general_search \
    --params '{"keyword":"pilates girl","count":"20"}' --out raw/tk_search.json
  ```
- **Cloudflare 会拦截非浏览器 UA**（报 `error 1010: browser_signature_banned`）。
  `mcp_call.py` 在 `requires_browser_ua: true` 的源上会自动带 Chrome UA —— 自己写请求时也必须带。
- 计费：REST 按调用计费。`/api/v1/tikhub/user/get_user_info` 返回
  `balance` 与 `free_credit`，**S0 之后应看一眼余额**，余额见底时后续阶段要么降级、要么先让用户充值。
- 常用端点：`/api/v1/tiktok/web/fetch_general_search`、`/api/v1/tiktok/web/fetch_search_video`、
  `/api/v1/instagram/web/...`、`/api/v1/youtube/web/...`。
  完整列表见 `https://api.tikhub.io/#/`（OpenAPI 文档）。

### 卖家精灵 SellerSprite —— Amazon

| 项 | 值 |
|---|---|
| MCP | `https://mcp.sellersprite.com/mcp?secret-key=<key>`（密钥在 query string） |
| 工具数 | 45 |

**必知的两个坑**：

1. **所有工具的入参都包一层 `request` 对象**：
   ```json
   {"request": {"marketplace": "US", "keyword": "pilates grip socks"}}
   ```
   直接传平铺参数会报参数错误。
2. **`market_research` 的 `departmentKeyword` 是模糊匹配，且命中失败时静默返回 0 行**
   （HTTP 200、`"total": 0`）。实测：
   - `Sports & Outdoors` ✅、`Clothing, Shoes & Jewelry` ✅、`yoga` ✅、`pilates` ✅、`socks` ✅
   - `yoga socks` ❌、`Yoga & Pilates Apparel` ❌

   `pipeline.py::fetch_s5` 因此按 `category → product_keyword → 各单词` 的顺序探测，
   并把每次探测结果写进 `raw/`，最后一个成功的写入 `raw/05_market_research.json`。

**常用工具**：

| 工具 | 用途 |
|---|---|
| `google_trend` | Google Trends 月度数（S2 趋势验证） |
| `market_research` | 类目级市场规模 / 集中度 / 新品占比（S5） |
| `keyword_research` | 关键词市场的搜索量、购买率、供需比、PPC 竞价 |
| `aba_research_weekly` / `_monthly` | ABA 热门/异动/增长/潜力关键词 |
| `product_research` | 多维筛选商品（S5 竞品样本） |
| `asin_detail` / `asin_prediction` | 单品详情与销量预测 |
| `traffic_keyword` / `keyword_order` | ASIN 的流量关键词正反向查询 |

`product_research` 的 `matchType`：**1=词组、2=模糊（默认）、3=精准**。
默认的模糊匹配会把无关品拉进来（实测用 `pilates grip socks` 模糊匹配会返回 BIC 圆珠笔），
选品场景一律用 `3`。

### Sorftime —— 多平台电商 + 站外

| 项 | 值 |
|---|---|
| MCP | `https://mcp.sorftime.com?key=<key>`（streamableHttp） |
| 工具数 | 105 |
| 覆盖 | Amazon / TikTok Shop / Shopee / Temu / Walmart / 1688 / Reddit |

**关键事实**：
- **配额耗尽时返回 HTTP 200 + 正文「MCP使用次数已达到上限，请升级您的套餐」**。
  这不是协议错误，MCP 客户端不会抛异常 —— 必须显式识别。
  `pipeline.py::looks_like_quota_error()` 用"响应体很短且含配额关键词"来判定。
- 参数是**平铺**的（不像 SellerSprite 需要包 `request`），例如：
  ```json
  {"keyword": "pilates socks", "keyword_support_site": "US"}
  ```
- 对选品特别有用的：`keyword_list`（实时热词榜）、`keyword_trend`、`product_search`、
  `category_report_from_history`、`reddit_post_search`（真实的站外用户抱怨语料）、
  `tiktok_product_search`、`ali1688_similar_product`（找源头工厂报价）。

---

## 二级：Apify

CLI 形态，需要 `APIFY_TOKEN` 环境变量。没有 token 时 S0 会显示 `degraded`
（二进制存在但环境不完整），降级链直接跳到三级。

用于 MCP 覆盖不到或配额耗尽时的抓取：TikTok / Instagram / Amazon 各类现成 actor。

---

## 三级：WebFetch / WebSearch

公开页面检索与抓取。没有 schema，取回的数据需要人工规范化进阶段 CSV。
适合：Google Trends 公开页快照、行业选品文章的榜单、新闻与博客里的趋势信号。

**必须**在 `evidence_url` 里留下可点开的链接，否则这条数据在报告里无法被复核。

---

## 四级：浏览器操作（最后手段）

`browser-skill`（`bsk` CLI）操作已登录的 Chromium。

- 只在 1–3 级都拿不到时才用。
- 慢、非确定性，且**结果不可复现** —— 必须在分析笔记里写明"此数据来自浏览器操作"，
  并附上 URL 与截图/抓取时间。
- 需要登录态的源（如 TikTok 创作者后台）只能走这条。

---

## 常见故障对照

| 现象 | 原因 | 处理 |
|---|---|---|
| tikhub 返回 `error 1010` | Cloudflare 拦了非浏览器 UA | 带 Chrome User-Agent |
| tikhub MCP 404 | 路径写成 `/mcp` | 正确路径是 `/tikhub/mcp` |
| SellerSprite 报参数错误 | 没包 `request` | `{"request": {...}}` |
| SellerSprite 返回 0 行 | `departmentKeyword` 是长短语 | 退到单词或一级类目名 |
| SellerSprite 竞品全是无关品 | `matchType` 默认模糊 | 传 `matchType: 3` |
| Sorftime 返回一句话 | 配额耗尽 | 视为不可用，降级到 SellerSprite |
| MCP 调用成功但内容为空 | 工具需要 `request` 包装或参数名不对 | 先 `list --json` 看 inputSchema |

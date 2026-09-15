# 降级链

```
一级 MCP          → 二级 Apify      → 三级 WebFetch/WebSearch → 四级 浏览器操作
(structured)         (scraper CLI)     (public web)              (logged-in browser)
```

## 规则

1. **严格按序尝试。** 不允许跳过一级直接用手工抓取 —— 一级源有 schema、有配额、
   结果可复现；低层级来源的结果确定性依次下降。
2. **每次降级都要留痕。** 命中层级写进数据行的 `source_tier` 列。
   建议取值：`mcp` / `apify` / `web` / `browser`。
   S1 的示例数据里出现过 `tier1-mcp` / `tier2-apify` / `tier3-web` 这种写法 ——
   只要**同一批数据内部取值一致**即可，但报告里的"数据层级"KPI 会直接读这一列，
   所以不要混用两套命名。
3. **降级要写进分析笔记。** 每个阶段的 `analysis/*.md` 里必须有一节说明
   哪些数据是降级取得的、以及为什么一级源没能满足。
4. **不允许静默降级。** 如果一级源的失败原因会改变结论的可信度
   （例如配额耗尽导致只能拿到近 3 个月数据），必须显式写在报告里。

## 每一级的判定与退出条件

| 层级 | 何时进入 | 何时算失败并往下走 |
|---|---|---|
| 一级 MCP | 默认起点 | 连通性失败 / 配额耗尽（Sorftime 的"使用次数已达到上限"）/ 工具不存在 / 返回空结果 |
| 二级 Apify | 一级不可用或覆盖不到 | `APIFY_TOKEN` 缺失 / actor 运行失败 / 输出为空 |
| 三级 Web | 一二级都不可用 | 页面取不到 / 数据是渲染后才有（SSR 拿不到） |
| 四级 浏览器 | 三级拿不到，且数据需要登录态 | 无法继续降级 —— 此时应停下来告诉用户缺什么 |

## 代码位置

- **连通性探测**：`scripts/mcp_call.py check` → 写 `data/00_source_capability.csv`
- **配额/软失败识别**：`scripts/pipeline.py::looks_like_quota_error()`
  —— 判据是"响应体很短（<200 字符）且含配额关键词"。
  这类失败不会抛异常，必须显式检查，否则会把一句错误提示当成正常数据写进 CSV。
- **带探针的取数**：`scripts/pipeline.py::fetch_s5()`
  —— 展示了"按候选参数逐个探测、记录每次结果、命中即停"的写法，
  这是处理"参数模糊匹配、失败静默返回空"这类接口的推荐模式。

## 记录模板

在阶段分析笔记里这样写：

```markdown
## 降级记录
- S2 趋势数据：`pilates girl` 在 SellerSprite `google_trend` 返回空序列，
  已降级到 WebFetch（Google Trends 公开页），source_tier = `web`。
  取回区间为公开页默认的近 12 个月，**不足 24 期，故该词未参与 Trend 判定**。
- S5 市场数据：Sorftime 配额耗尽（返回"MCP使用次数已达到上限"），
  整段降级到 SellerSprite，source_tier = `mcp`。
```

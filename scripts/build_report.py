#!/usr/bin/env python3
"""Render the conclusion-bearing HTML report.

Every number printed here is read back out of the stage tables, so the report can
never contain a conclusion that the Excel workbook cannot back up. Sections that a
stage did not produce render as an explicit gap rather than being silently omitted.
"""
from __future__ import annotations

import argparse
import csv
import html
import json
import sys
from pathlib import Path

CSS = """
:root{--ink:#12161c;--muted:#5d6874;--line:#e2e6ea;--brand:#1f3864;--accent:#c2410c;
--ok:#0f766e;--bad:#b91c1c;--warn:#a16207;--bg:#fbfbfa;}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);
font:15px/1.7 -apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Hiragino Sans GB","Microsoft YaHei",sans-serif;}
.wrap{max-width:1120px;margin:0 auto;padding:56px 28px 96px}
header{border-bottom:3px solid var(--ink);padding-bottom:22px;margin-bottom:36px}
h1{font-size:31px;line-height:1.3;margin:0 0 10px;letter-spacing:-.4px}
.sub{color:var(--muted);font-size:14px}
h2{font-size:21px;margin:52px 0 14px;padding-bottom:8px;border-bottom:1px solid var(--line);letter-spacing:-.2px}
h3{font-size:16px;margin:28px 0 8px}
p{margin:10px 0}
table{border-collapse:collapse;width:100%;margin:16px 0;font-size:13.5px;background:#fff}
th,td{border:1px solid var(--line);padding:8px 11px;text-align:left;vertical-align:top}
th{background:var(--brand);color:#fff;font-weight:600;white-space:nowrap}
tr:nth-child(even) td{background:#f7f8f9}
.verdict{display:inline-block;padding:2px 9px;border-radius:11px;font-size:12px;font-weight:700;letter-spacing:.3px}
.v-trend{background:#d1fae5;color:var(--ok)} .v-fad{background:#fee2e2;color:var(--bad)}
.v-unclear{background:#fef3c7;color:var(--warn)}
.v-go{background:#d1fae5;color:var(--ok)} .v-hold{background:#fef3c7;color:var(--warn)}
.v-kill{background:#fee2e2;color:var(--bad)}
.kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(186px,1fr));gap:14px;margin:22px 0}
.kpi{background:#fff;border:1px solid var(--line);border-left:4px solid var(--brand);padding:15px 17px;border-radius:3px}
.kpi .k{font-size:12px;color:var(--muted);letter-spacing:.4px;text-transform:uppercase}
.kpi .v{font-size:25px;font-weight:700;margin-top:5px;letter-spacing:-.6px}
.kpi .n{font-size:12px;color:var(--muted);margin-top:3px}
.callout{background:#fff;border:1px solid var(--line);border-left:4px solid var(--accent);padding:16px 19px;margin:20px 0;border-radius:3px}
.callout.gap{border-left-color:var(--warn);background:#fffdf5}
.gap-list{background:#fffdf5;border:1px dashed var(--warn);padding:14px 18px;border-radius:3px;font-size:14px}
.gate{display:inline-block;width:19px;height:19px;line-height:19px;text-align:center;border-radius:50%;
font-size:12px;font-weight:700;color:#fff}
.gate.p{background:var(--ok)} .gate.f{background:var(--bad)} .gate.n{background:#c7ccd1}
svg{display:block;max-width:100%;background:#fff;border:1px solid var(--line);border-radius:3px;margin:14px 0}
code{background:#eef1f4;padding:1px 5px;border-radius:3px;font-size:13px}
footer{margin-top:64px;padding-top:20px;border-top:1px solid var(--line);color:var(--muted);font-size:13px}
"""


def esc(v) -> str:
    return html.escape(str(v if v is not None else ""))


def read_csv(path: Path) -> tuple[list[str], list[dict]]:
    if not path.is_file():
        return [], []
    with path.open(encoding="utf-8-sig", newline="") as f:
        rdr = csv.DictReader(f)
        return (rdr.fieldnames or []), [r for r in rdr]


def f(v) -> float | None:
    try:
        return float(str(v).replace(",", "").replace("%", "").strip())
    except (TypeError, ValueError):
        return None


# --------------------------------------------------------------------------- charts
def line_chart(series: dict[str, list[tuple[str, float]]], width=1040, height=300,
               xlabel="", ylabel="Google Trends 指数", title="") -> str:
    if not series:
        return ""
    pad_l, pad_r, pad_t, pad_b = 56, 150, 26 if not title else 44, 40
    all_v = [v for s in series.values() for _, v in s]
    all_x = [x for s in series.values() for x, _ in s]
    if not all_v:
        return ""
    vmax = max(all_v) or 1
    xs = sorted(set(all_x))
    xi = {x: i for i, x in enumerate(xs)}
    W, H = width - pad_l - pad_r, height - pad_t - pad_b

    def px(x): return pad_l + (xi[x] / max(len(xs) - 1, 1)) * W
    def py(v): return pad_t + H - (v / vmax) * H

    out = [f'<svg viewBox="0 0 {width} {height}" width="{width}" height="{height}" role="img">']
    if title:
        out.append(f'<text x="{pad_l}" y="22" font-size="14" font-weight="700" fill="#12161c">{esc(title)}</text>')
    for i in range(5):
        y = pad_t + H * i / 4
        val = vmax * (1 - i / 4)
        out.append(f'<line x1="{pad_l}" y1="{y:.1f}" x2="{pad_l+W}" y2="{y:.1f}" stroke="#e2e6ea"/>')
        out.append(f'<text x="{pad_l-8}" y="{y+4:.1f}" font-size="11" fill="#5d6874" text-anchor="end">{val:.0f}</text>')
    step = max(len(xs) // 8, 1)
    for i, x in enumerate(xs):
        if i % step == 0:
            out.append(f'<text x="{px(x):.1f}" y="{pad_t+H+20}" font-size="10.5" fill="#5d6874" '
                       f'text-anchor="middle">{esc(x)}</text>')
    palette = ["#1f3864", "#c2410c", "#0f766e", "#7c3aed", "#be123c", "#0369a1"]
    for n, (name, s) in enumerate(sorted(series.items())):
        s = sorted(s)
        color = palette[n % len(palette)]
        pts = " ".join(f"{px(x):.1f},{py(v):.1f}" for x, v in s if x in xi)
        if pts:
            out.append(f'<polyline points="{pts}" fill="none" stroke="{color}" stroke-width="2.1"/>')
            ly = pad_t + 15 + n * 19
            out.append(f'<line x1="{pad_l+W+14}" y1="{ly}" x2="{pad_l+W+34}" y2="{ly}" stroke="{color}" stroke-width="3"/>')
            out.append(f'<text x="{pad_l+W+40}" y="{ly+4}" font-size="12" fill="#12161c">{esc(name[:20])}</text>')
    out.append(f'<text x="{pad_l+W/2:.0f}" y="{height-6}" font-size="11" fill="#5d6874" text-anchor="middle">{esc(xlabel)}</text>')
    out.append("</svg>")
    return "".join(out)


def bar_chart(rows: list[tuple[str, float]], width=1040, height=None,
              title="", color="#1f3864", unit="%") -> str:
    if not rows:
        return ""
    rh, pad_l, pad_r, pad_t = 26, 210, 90, 44 if title else 18
    height = height or (pad_t + len(rows) * rh + 22)
    W = width - pad_l - pad_r
    vmax = max(v for _, v in rows) or 1
    out = [f'<svg viewBox="0 0 {width} {height}" width="{width}" height="{height}" role="img">']
    if title:
        out.append(f'<text x="0" y="20" font-size="14" font-weight="700" fill="#12161c">{esc(title)}</text>')
    for i, (label, val) in enumerate(rows):
        y = pad_t + i * rh
        w = (val / vmax) * W
        out.append(f'<text x="{pad_l-10}" y="{y+17}" font-size="12.5" fill="#12161c" text-anchor="end">{esc(label[:26])}</text>')
        out.append(f'<rect x="{pad_l}" y="{y+4}" width="{w:.1f}" height="{rh-10}" fill="{color}" rx="2"/>')
        out.append(f'<text x="{pad_l+w+8:.1f}" y="{y+17}" font-size="12" fill="#5d6874">{val:.1f}{unit}</text>')
    out.append("</svg>")
    return "".join(out)


def table(cols: list[str], rows: list[dict], max_rows=40, verdict_cols=()) -> str:
    if not cols:
        return '<p class="sub">（该阶段未产出数据）</p>'
    out = ["<table><thead><tr>"]
    for c in cols:
        out.append(f"<th>{esc(c)}</th>")
    out.append("</tr></thead><tbody>")
    for r in rows[:max_rows]:
        out.append("<tr>")
        for c in cols:
            v = r.get(c, "")
            if c in verdict_cols:
                cls = {"trend": "v-trend", "fad": "v-fad", "unclear": "v-unclear",
                       "go": "v-go", "hold": "v-hold", "kill": "v-kill"}.get(str(v).strip().lower(), "")
                out.append(f'<td><span class="verdict {cls}">{esc(v)}</span></td>')
            else:
                out.append(f"<td>{esc(v)}</td>")
        out.append("</tr>")
    out.append("</tbody></table>")
    if len(rows) > max_rows:
        out.append(f'<p class="sub">共 {len(rows)} 行，此处显示前 {max_rows} 行；完整数据见 Excel。</p>')
    return "".join(out)


# --------------------------------------------------------------------------- report
def build(run_dir: Path) -> Path:
    run_dir = run_dir.resolve()
    out_dir = run_dir / "out"
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / "selection_report.html"

    state = json.loads((run_dir / "state.json").read_text(encoding="utf-8"))
    def csvp(rel): return read_csv(run_dir / rel)

    seeds_c, seeds = csvp("data/01_seed_keywords.csv")
    ts_c, ts = csvp("data/02_trend_timeseries.csv")
    sum_c, sums = csvp("data/02_trend_summary.csv")
    scene_c, scenes = csvp("data/03_scene_keywords.csv")
    sub_c, subs = csvp("data/03_subcommunity.csv")
    corp_c, corp = csvp("data/04_corpus_index.csv")
    pain_c, pains = csvp("data/04_painpoints.csv")
    mkt_c, mkts = csvp("data/05_market_overview.csv")
    band_c, bands = csvp("data/05_price_band.csv")
    comp_c, comps = csvp("data/05_competitors.csv")
    cand_c, cands = csvp("data/06_candidate_scores.csv")
    src_c, srcs = csvp("data/00_source_capability.csv")

    stages = state.get("stages", {})
    # S7 is the delivery step; this file IS its artifact, so it is excluded from the
    # gate table and from the gap list - otherwise every report would report itself missing.
    analysis_stages = [s for k, s in stages.items() if k != "S7"]
    n_passed = sum(1 for s in analysis_stages if s["status"] == "passed")
    gaps = [s for s in analysis_stages if s["status"] != "passed"]

    # ---------- conclusions (all derived from the tables above)
    trends = [r for r in sums if str(r.get("trend_verdict", "")).lower() == "trend"]
    fads = [r for r in sums if str(r.get("trend_verdict", "")).lower() == "fad"]
    top_pain = max(pains, key=lambda r: f(r.get("share_pct")) or 0) if pains else None
    top_cand = max(cands, key=lambda r: f(r.get("total_score")) or 0) if cands else None
    mkt = mkts[0] if mkts else None
    source_tiers = sorted({str(r.get("source_tier")) for r in (seeds + ts + pains + mkts) if r.get("source_tier")})

    conclusions: list[str] = []
    if trends:
        best = max(trends, key=lambda r: f(r.get("cagr_pct")) or 0)
        conclusions.append(
            f"在 {len(sums)} 个候选热词中，{len(trends)} 个通过趋势验证（CAGR ≥10% 且观测窗口 ≥24 期）。"
            f"增长最强的是 <b>{esc(best.get('keyword'))}</b>，年复合增长 {esc(best.get('cagr_pct'))}%，"
            f"季节性指数 {esc(best.get('seasonality_index'))}。")
    if fads:
        conclusions.append(
            "被判定为短期热度的词（不进入下一阶段）：" + "、".join(esc(r.get("keyword")) for r in fads) + "。")
    if top_pain:
        conclusions.append(
            f"语料聚类中提及频次最高的痛点是「<b>{esc(top_pain.get('painpoint'))}</b>」，"
            f"占比 {esc(top_pain.get('share_pct'))}%（{esc(top_pain.get('mentions'))} 次提及，类别 {esc(top_pain.get('category'))}）。")
    if subs:
        conclusions.append(
            f"最终锁定的子圈层是 <b>{esc(subs[0].get('subcommunity'))}</b>，"
            f"定义性关键词：{esc(subs[0].get('defining_keywords'))}。")
    if mkt:
        conclusions.append(
            f"电商侧校验：{esc(mkt.get('marketplace'))} / {esc(mkt.get('category'))} "
            f"月销售额约 {esc(mkt.get('monthly_revenue_usd'))}，均价 {esc(mkt.get('avg_price'))}，"
            f"头部品牌集中度 {esc(mkt.get('top10_brand_share_pct'))}%。")
    if top_cand:
        conclusions.append(
            f"机会评分最高的是 <b>{esc(top_cand.get('candidate'))}</b>（{esc(top_cand.get('total_score'))} 分），"
            f"判定 <b>{esc(top_cand.get('verdict'))}</b>。")
    if not conclusions:
        conclusions.append("本轮尚未产出任何可支撑结论的数据表。请先完成 S1–S5 的数据获取。")

    # ---------- KPIs
    kpis = [
        ("数据源可用", f"{sum(1 for r in srcs if str(r.get('status')) == 'ok')}/{len(srcs) or 0}", "一级数据源连通性"),
        ("候选热词", str(len(seeds)), "S1 圈层热词条目"),
        ("通过趋势验证", str(len(trends)), "Trend 判定数"),
        ("痛点条目", str(len(pains)), "S4 聚类结果"),
        ("竞品样本", str(len(comps)), "S5 采集 ASIN 数"),
        ("候选单品", str(len(cands)), "S6 评分条目"),
        ("阶段通过", f"{n_passed}/{len(analysis_stages)}", "S0–S6 中通过 gate 的阶段"),
        ("数据层级", "、".join(source_tiers) or "-", "本报告实际命中的数据源层级"),
    ]
    kpi_html = "".join(
        f'<div class="kpi"><div class="k">{esc(k)}</div><div class="v">{esc(v)}</div><div class="n">{esc(n)}</div></div>'
        for k, v, n in kpis)

    # ---------- stage gate table
    gate_rows = []
    for s in analysis_stages:
        g = s.get("gate") or {}
        checks = g.get("checks", [])
        passed = sum(1 for c in checks if c.get("ok"))
        mark = {"passed": "p", "failed": "f"}.get(s["status"], "n")
        gate_rows.append({
            "stage": s["id"], "name": s["name"],
            "gate": f'<span class="gate {mark}">{"P" if s["status"] == "passed" else ("F" if s["status"] == "failed" else "-")}</span>',
            "checks": f"{passed}/{len(checks)}" if checks else "-",
            "state": s["status"],
            "notes": "<br>".join(esc(n["text"]) for n in (s.get("notes") or [])) or "",
        })
    gate_html = ["<table><thead><tr><th>阶段</th><th>名称</th><th>验证</th><th>通过项</th><th>状态</th><th>备注</th></tr></thead><tbody>"]
    for r in gate_rows:
        gate_html.append(
            f'<tr><td>{esc(r["stage"])}</td><td>{esc(r["name"])}</td><td>{r["gate"]}</td>'
            f'<td>{esc(r["checks"])}</td><td>{esc(r["state"])}</td><td>{r["notes"]}</td></tr>')
    gate_html.append("</tbody></table>")
    gate_html = "".join(gate_html)

    # ---------- charts
    series: dict[str, list[tuple[str, float]]] = {}
    for r in ts:
        v = f(r.get("value"))
        if v is not None:
            series.setdefault(str(r.get("keyword")), []).append((str(r.get("period")), v))
    trend_chart = line_chart(series, title="S2 趋势时间序列（Google Trends 月度数）", xlabel="月份")

    pain_bars = sorted([(str(r.get("painpoint"))[:26], f(r.get("share_pct")) or 0) for r in pains],
                       key=lambda x: -x[1])[:10]
    pain_chart = bar_chart(pain_bars, title="S4 痛点提及占比", color="#c2410c")
    band_bars = [(str(r.get("price_band")), f(r.get("units_share_pct")) or 0) for r in bands]
    band_chart = bar_chart(band_bars, title="S5 价格带销量占比", color="#0f766e")
    score_bars = sorted([(str(r.get("candidate"))[:26], f(r.get("total_score")) or 0) for r in cands],
                        key=lambda x: -x[1])[:10]
    score_chart = bar_chart(score_bars, title="S6 候选机会总分", color="#1f3864", unit="")

    # ---------- gaps
    gap_html = ""
    if gaps:
        items = "".join(
            f'<li><b>{esc(s["id"])} {esc(s["name"])}</b> — 状态 <code>{esc(s["status"])}</code>；'
            + (
                "缺失：" + "、".join(
                    esc(c.get("path") or c.get("check"))
                    for c in (s.get("gate") or {}).get("checks", []) if not c.get("ok"))
                if (s.get("gate") or {}).get("checks") else
                "尚未执行。所需数据文件见 SKILL.md 的 S" + esc(s["id"][1]) + " 契约。"
            ) + "</li>"
            for s in gaps)
        gap_html = (f'<div class="gap-list"><b>未闭环的阶段（结论在这些维度上是不完整的）</b>'
                    f'<ul>{items}</ul></div>')

    # ---------- assemble
    body = f"""<div class="wrap">
<header>
  <h1>{esc(state.get('topic'))}</h1>
  <div class="sub">
    社媒 × 关键词选品分析报告 &nbsp;·&nbsp; 市场 {esc(state.get('market'))}
    &nbsp;·&nbsp; 生成于 {esc(state.get('updated_at'))}
    &nbsp;·&nbsp; 起始关键词 {esc(', '.join(state.get('seed_keywords') or []) or '-')}
  </div>
</header>

<section id="conclusions">
<h2>一、结论</h2>
<div class="callout">
{"".join(f'<p>{c}</p>' for c in conclusions)}
</div>
<div class="kpis">{kpi_html}</div>
{gap_html}
</section>

<section id="gates">
<h2>二、分阶段结果验证</h2>
<p>每个阶段都有一道机器可校验的 gate。<b>分析结论只有在对应数据表通过 gate 之后才会被允许生成</b>，
因此下表既是进度表，也是本报告可信度的边界说明。</p>
{gate_html}
</section>

<section id="trend">
<h2>三、趋势证据（S1–S2）</h2>
<h3>3.1 热词发现</h3>
{table(seeds_c, seeds, max_rows=25)}
<h3>3.2 时间序列</h3>
{trend_chart or '<p class="sub">（无时间序列数据）</p>'}
<h3>3.3 逐词判定</h3>
{table(sum_c, sums, verdict_cols=('trend_verdict',))}
</section>

<section id="voice">
<h2>四、圈层与痛点证据（S3–S4）</h2>
<h3>4.1 场景关键词</h3>
{table(scene_c, scenes, max_rows=25)}
<h3>4.2 锁定子圈层</h3>
{table(sub_c, subs, max_rows=5)}
<h3>4.3 语料规模</h3>
<p>收录语料文档 <b>{len(corp)}</b> 篇，累计正文 <b>{sum(int(f(r.get('text_chars')) or 0) for r in corp):,}</b> 字。</p>
<h3>4.4 痛点聚类</h3>
{pain_chart or '<p class="sub">（无痛点数据）</p>'}
{table(pain_c, pains, max_rows=20)}
</section>

<section id="market">
<h2>五、电商侧市场验证（S5）</h2>
{table(mkt_c, mkts, max_rows=5)}
{band_chart or ''}
{table(band_c, bands, max_rows=12)}
<h3>5.3 竞品样本</h3>
{table(comp_c, comps, max_rows=20)}
</section>

<section id="score">
<h2>六、单品收敛与机会评分（S6）</h2>
{score_chart or '<p class="sub">（无评分数据）</p>'}
{table(cand_c, cands, verdict_cols=('verdict',))}
</section>

<section id="provenance">
<h2>七、数据溯源与局限</h2>
<h3>7.1 数据源能力矩阵</h3>
{table(src_c, srcs, max_rows=12)}
<h3>7.2 降级链</h3>
<p>本次运行的数据获取按 <code>MCP(一级) → Apify(二级) → WebFetch/WebSearch(三级) → 浏览器操作(四级)</code>
的顺序尝试；每条数据行的 <code>source_tier</code> 列记录了它实际命中的层级。
命中的层级越低，数值的确定性和可复现性越弱，结论应相应打折。</p>
<h3>7.3 本报告的局限</h3>
<ul>
<li>Google Trends 返回的是<b>相对指数</b>（0–100），不是绝对搜索量，只能用于比较走势，不能直接换算市场规模。</li>
<li>电商侧数据为<b>第三方估算</b>（SellerSprite / Sorftime / Apify 抽取），与实际后台数据存在偏差。</li>
<li>社媒语料抽样受平台可抓取性影响，可能系统性低估未使用英文表述的圈层。</li>
<li>所有结论均绑定<b>采集时间戳</b>；趋势类判断建议在 4–8 周后重跑一次再决策。</li>
</ul>
</section>

<section id="next">
<h2>八、下一步</h2>
<ol>
<li>对评分靠前的候选，用真实打样成本与物流报价复算单元经济模型。</li>
<li>用同一套关键词跑一次小规模投放/内容测试，验证声量能否转化为点击与加购。</li>
<li>重跑 <code>pipeline.py fetch --stage S2</code> 并对比时间序列，确认趋势未被证伪。</li>
</ol>
</section>

<footer>
由 product-selection skill 生成 · 全部原始数据见同目录 <code>selection_data.xlsx</code> ·
阶段中间产物见 run 目录的 <code>raw/</code>、<code>data/</code>、<code>analysis/</code>。
</footer>
</div>"""

    doc = (f"<!DOCTYPE html><html lang=\"zh-CN\"><head><meta charset=\"utf-8\">"
           f"<meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">"
           f"<title>{esc(state.get('topic'))} · 选品分析报告</title>"
           f"<style>{CSS}</style></head><body>{body}</body></html>")
    out.write_text(doc, encoding="utf-8")
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run-dir", default="./selection_run")
    args = ap.parse_args()
    p = build(Path(args.run_dir))
    print(f"wrote {p} ({p.stat().st_size:,} bytes)")
    return 0


if __name__ == "__main__":
    sys.exit(main())

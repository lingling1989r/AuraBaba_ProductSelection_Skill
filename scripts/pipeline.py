#!/usr/bin/env python3
"""Staged pipeline for 社媒 x 关键词选品 (social-media x keyword product selection).

Design contract
---------------
1. DATA FIRST, ANALYSIS SECOND. Every stage has two kinds of artifacts:
     - `data`     : normalized tables fetched from sources (raw/ and data/)
     - `analysis` : conclusions derived from those tables (analysis/)
   A stage's analysis gate cannot pass until its data gate passes. There is no
   path that lets a conclusion exist without the table under it.
2. EVERY STAGE HAS A GATE. Gates are mechanical: file presence, required columns,
   minimum row counts, and named numeric assertions. `check` writes a verdict.
3. RESUMABLE. state.json records stage status; re-running never redoes a passed
   stage unless --force is given.
4. SOURCE FALLBACK IS RECORDED. Every data file carries a `source_tier` column so
   the report can be honest about which tier a number came from.

Commands
--------
  init      --run-dir DIR --topic "..." [--market US] [--seed "a" --seed "b"]
  status    [--json]
  fetch     --stage S1 [--force]     # deterministic fetchers where a machine can do it
  check     --stage S1               # run the stage gate, write verdict, advance state
  check-all
  next                               # print the next stage that is not passing
  note      --stage S1 --text "..."
  finalize                           # build Excel + HTML from whatever passed
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import os
import statistics
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
SKILL_ROOT = HERE.parent
sys.path.insert(0, str(HERE))

RUN_MARKER = "state.json"

# --------------------------------------------------------------------------- stage spec
STAGES: list[dict] = [
    {
        "id": "S0",
        "name": "环境与数据源自检",
        "goal": "确认社媒源与电商源的真实可用性，记录降级链起点，避免在配额耗尽的数据源上浪费整轮。",
        "data": [("data/00_source_capability.csv", ["source", "tier", "status", "tool_count", "notes"])],
        "analysis": [("analysis/00_plan.md", None)],
        "gate": ["g_s0"],
        "fallback": ["mcp", "apify", "web", "browser"],
    },
    {
        "id": "S1",
        "name": "圈层热词发现",
        "goal": "不做商品词搜索。从目标人群所在的社媒圈层里，找出正在变热的『身份热词』，作为整个选品的入口。",
        "data": [
            ("data/01_seed_keywords.csv",
             ["keyword", "keyword_type", "platform", "heat_score", "heat_cagr_pct", "obs_window", "source_tier", "captured_at", "evidence_url"]),
        ],
        "analysis": [("analysis/01_seed_notes.md", None)],
        "gate": ["g_s1"],
        "fallback": ["mcp:tikhub", "apify", "web", "browser"],
    },
    {
        "id": "S2",
        "name": "趋势验证（Trend 还是 Fad）",
        "goal": "对候选热词做时间序列验证。只有『长期结构性增长』的词才允许进入下一阶段，避免把一阵风当真趋势。",
        "data": [
            ("data/02_trend_timeseries.csv", ["keyword", "period", "value", "source", "source_tier", "captured_at"]),
            ("data/02_trend_summary.csv",
             ["keyword", "n_periods", "first_value", "last_value", "cagr_pct", "seasonality_index", "trend_verdict", "source_tier"]),
        ],
        "analysis": [("analysis/02_trend_notes.md", None)],
        "gate": ["g_s2"],
        "fallback": ["mcp:sellersprite", "mcp:sorftime", "web", "browser"],
    },
    {
        "id": "S3",
        "name": "场景与子圈层锁定",
        "goal": "顺着热词进入行为网络：用评论区和微网红口播里的高频词，把一个模糊圈层收敛成一个可命名的子圈层。",
        "data": [
            ("data/03_scene_keywords.csv",
             ["scene_keyword", "parent_keyword", "platform", "frequency", "evidence_type", "source_url", "source_tier", "captured_at"]),
            ("data/03_subcommunity.csv", ["subcommunity", "defining_keywords", "audience_size_signal", "why_picked", "source_tier"]),
        ],
        "analysis": [("analysis/03_subcommunity_notes.md", None)],
        "gate": ["g_s3"],
        "fallback": ["mcp:tikhub", "apify", "web", "browser"],
    },
    {
        "id": "S4",
        "name": "语料池构建与痛点聚类",
        "goal": "把目标圈层的真实语言（视频口播 + 评论）变成语料，再用词频聚类找出被大牌忽略的痛点与对应单品。",
        "data": [
            ("data/04_corpus_index.csv", ["doc_id", "platform", "creator", "followers", "url", "text_chars", "captured_at"]),
            ("data/04_painpoints.csv",
             ["painpoint_id", "painpoint", "category", "mentions", "share_pct", "example_quote", "evidence_url", "source_tier"]),
        ],
        "analysis": [("analysis/04_painpoint_notes.md", None)],
        "gate": ["g_s4"],
        "fallback": ["mcp:tikhub", "apify", "web", "browser"],
    },
    {
        "id": "S5",
        "name": "电商侧市场验证",
        "goal": "用电商数据给社媒洞察做压力测试：市场够不够大、头部垄断程度、价格带、新品还有没有活路。",
        "data": [
            ("data/05_market_overview.csv",
             ["marketplace", "category", "category_node_id", "sample_scope", "product_count",
              "monthly_units", "monthly_revenue_usd", "avg_price", "avg_profit_pct",
              "top3_brand_share_pct", "top10_brand_share_pct", "brand_count",
              "new_product_share_pct", "return_rate_pct", "search_to_purchase_ratio",
              "source_tool", "source_tier", "captured_at"]),
            ("data/05_price_band.csv",
             ["marketplace", "category", "price_band", "product_count", "units_share_pct", "avg_rating", "avg_reviews", "source_tool", "source_tier"]),
            ("data/05_competitors.csv",
             ["marketplace", "asin", "title", "brand", "seller", "price", "monthly_units",
              "monthly_revenue_usd", "rating", "reviews", "bsr", "listing_date",
              "search_tool", "source_tier", "captured_at"]),
        ],
        "analysis": [("analysis/05_market_notes.md", None)],
        "gate": ["g_s5"],
        "fallback": ["mcp:sellersprite", "mcp:sorftime", "apify", "web", "browser"],
    },
    {
        "id": "S6",
        "name": "单品收敛与机会评分",
        "goal": "把 S2/S4/S5 的证据合成一个可解释的评分，把候选收敛到少数几个可立项的单品。",
        "data": [
            ("data/06_candidate_scores.csv",
             ["candidate", "trend_score", "painpoint_score", "market_score", "competition_score", "economics_score", "total_score", "verdict", "evidence_refs"]),
        ],
        "analysis": [("analysis/06_recommendation.md", None)],
        "gate": ["g_s6"],
        "fallback": ["derived"],
    },
    {
        "id": "S7",
        "name": "交付：HTML 报告 + Excel 全量数据",
        "goal": "产出带结论的 HTML 报告，以及承载全部数据的 Excel 工作簿（每个阶段一个 sheet + 汇总 + 溯源）。",
        "data": [("out/selection_data.xlsx", None)],
        "analysis": [("out/selection_report.html", None)],
        "gate": ["g_s7"],
        "fallback": ["derived"],
    },
]

STAGE_BY_ID = {s["id"]: s for s in STAGES}

# --------------------------------------------------------------------------- utils
def now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


class Run:
    def __init__(self, run_dir: Path):
        self.dir = run_dir
        self.state_path = run_dir / RUN_MARKER

    # -- lifecycle
    @classmethod
    def create(cls, run_dir: Path, topic: str, market: str, seeds: list[str],
               category: str = "", product_keyword: str = ""):
        for sub in ("raw", "data", "analysis", "out", "logs"):
            (run_dir / sub).mkdir(parents=True, exist_ok=True)
        state = {
            "topic": topic,
            "market": market,
            "category": category,
            "product_keyword": product_keyword or category,
            "seed_keywords": seeds,
            "created_at": now(),
            "updated_at": now(),
            "stages": {
                s["id"]: {"id": s["id"], "name": s["name"], "status": "pending",
                          "gate": None, "notes": [], "started_at": None, "finished_at": None}
                for s in STAGES
            },
        }
        r = cls(run_dir)
        r.save(state)
        return r

    @classmethod
    def load(cls, run_dir: Path) -> "Run":
        if not (run_dir / RUN_MARKER).is_file():
            raise SystemExit(f"no pipeline run at {run_dir} (missing {RUN_MARKER}). run `init` first.")
        return cls(run_dir)

    def read(self) -> dict:
        return json.loads(self.state_path.read_text(encoding="utf-8"))

    def save(self, state: dict) -> None:
        state["updated_at"] = now()
        tmp = self.state_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(self.state_path)

    def stage(self, sid: str) -> dict:
        return self.read()["stages"][sid]

    def mark(self, sid: str, status: str, gate: dict | None = None) -> None:
        st = self.read()
        entry = st["stages"][sid]
        entry["status"] = status
        if gate is not None:
            entry["gate"] = gate
        if status == "running" and not entry.get("started_at"):
            entry["started_at"] = now()
        if status in ("passed", "failed", "degraded"):
            entry["finished_at"] = now()
        self.save(st)


def read_csv(path: Path) -> tuple[list[str], list[dict]]:
    with path.open(encoding="utf-8-sig", newline="") as f:
        rdr = csv.DictReader(f)
        rows = [r for r in rdr]
        return (rdr.fieldnames or []), rows


def write_csv(path: Path, columns: list[str], rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=columns, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow(r)


def find_col(cols: list[str], *want: str) -> str | None:
    low = {c.lower().strip(): c for c in cols}
    for w in want:
        if w.lower() in low:
            return low[w.lower()]
    return None


def to_float(v) -> float | None:
    try:
        return float(str(v).replace(",", "").replace("%", "").strip())
    except (TypeError, ValueError):
        return None


# --------------------------------------------------------------------------- gates
def gate_result(ok: bool, checks: list[dict]) -> dict:
    return {"ok": ok, "checked_at": now(), "checks": checks}


def _file_check(run: Run, rel: str) -> dict:
    p = run.dir / rel
    return {"check": "file_exists", "path": rel, "ok": p.is_file(),
            "detail": f"{p.stat().st_size} bytes" if p.is_file() else "missing"}


def _table_check(run: Run, rel: str, columns: list[str] | None, min_rows: int) -> dict:
    p = run.dir / rel
    if not p.is_file():
        return {"check": "table", "path": rel, "ok": False, "detail": "missing"}
    if p.suffix.lower() in (".xlsx", ".html"):
        return {"check": "artifact", "path": rel, "ok": p.stat().st_size > 500,
                "detail": f"{p.stat().st_size} bytes"}
    cols, rows = read_csv(p)
    missing = [c for c in (columns or []) if c not in cols]
    ok = not missing and len(rows) >= min_rows
    return {"check": "table", "path": rel, "ok": ok,
            "detail": f"rows={len(rows)} (min {min_rows}); missing_columns={missing or 'none'}"}


def g_s0(run: Run) -> list[dict]:
    checks = [_table_check(run, "data/00_source_capability.csv",
                           ["source", "tier", "status", "tool_count", "notes"], 1)]
    p = run.dir / "data/00_source_capability.csv"
    if p.is_file():
        _, rows = read_csv(p)
        usable = [r for r in rows if r.get("status") in ("ok", "degraded")]
        social = [r for r in usable if r.get("kind") in ("mcp", "mcp+rest", "cli")]
        checks.append({"check": "at_least_one_live_source", "ok": len(social) >= 1,
                       "detail": f"{len(social)} usable source(s)"})
    return checks


def g_s1(run: Run) -> list[dict]:
    d = run.dir / "data/01_seed_keywords.csv"
    checks = [_table_check(run, "data/01_seed_keywords.csv",
                           ["keyword", "keyword_type", "platform", "heat_cagr_pct", "source_tier", "captured_at"], 8)]
    if d.is_file():
        _, rows = read_csv(d)
        types = {str(r.get("keyword_type", "")).strip().lower() for r in rows}
        checks.append({"check": "has_identity_keywords", "ok": "identity" in types,
                       "detail": f"keyword_type values = {sorted(t for t in types if t)}"})
        prov = [r for r in rows if r.get("source_tier") and r.get("captured_at")]
        checks.append({"check": "provenance_complete", "ok": len(prov) == len(rows),
                       "detail": f"{len(prov)}/{len(rows)} rows carry source_tier + captured_at"})
    return checks


def g_s2(run: Run) -> list[dict]:
    checks = [
        _table_check(run, "data/02_trend_timeseries.csv",
                     ["keyword", "period", "value", "source", "source_tier"], 48),
        _table_check(run, "data/02_trend_summary.csv",
                     ["keyword", "n_periods", "cagr_pct", "seasonality_index", "trend_verdict"], 3),
    ]
    s = run.dir / "data/02_trend_summary.csv"
    if s.is_file():
        _, rows = read_csv(s)
        trends = [r for r in rows if str(r.get("trend_verdict", "")).lower() in ("trend", "趋势")]
        checks.append({"check": "at_least_one_survivor", "ok": len(trends) >= 1,
                       "detail": f"{len(trends)}/{len(rows)} keywords classified as TREND"})
        long_enough = [r for r in rows if (to_float(r.get("n_periods")) or 0) >= 24]
        checks.append({"check": "window_ge_24_periods", "ok": len(long_enough) == len(rows),
                       "detail": f"{len(long_enough)}/{len(rows)} keywords observed >=24 periods"})
    return checks


def g_s3(run: Run) -> list[dict]:
    checks = [
        _table_check(run, "data/03_scene_keywords.csv",
                     ["scene_keyword", "parent_keyword", "platform", "frequency", "source_tier"], 10),
        _table_check(run, "data/03_subcommunity.csv",
                     ["subcommunity", "defining_keywords", "why_picked"], 1),
    ]
    return checks


def g_s4(run: Run) -> list[dict]:
    checks = [
        _table_check(run, "data/04_corpus_index.csv",
                     ["doc_id", "platform", "url", "text_chars"], 10),
        _table_check(run, "data/04_painpoints.csv",
                     ["painpoint_id", "painpoint", "category", "mentions", "share_pct"], 3),
    ]
    p = run.dir / "data/04_painpoints.csv"
    if p.is_file():
        _, rows = read_csv(p)
        shares = [to_float(r.get("share_pct")) for r in rows]
        shares = [s for s in shares if s is not None]
        top = max(shares) if shares else 0
        total_mentions = sum((to_float(r.get("mentions")) or 0) for r in rows)
        checks.append({"check": "corpus_is_substantial", "ok": total_mentions >= 100,
                       "detail": f"total mentions = {int(total_mentions)} (need >=100 before shares mean anything)"})
        checks.append({"check": "painpoint_concentrated", "ok": top >= 20,
                       "detail": f"top painpoint share = {top:.1f}% (need >=20% to call it a signal)"})
        checks.append({"check": "covers_product_categories", "ok": len({r.get("category") for r in rows}) >= 2,
                       "detail": f"categories = {sorted({r.get('category') for r in rows})}"})
    return checks


def g_s5(run: Run) -> list[dict]:
    checks = [
        _table_check(run, "data/05_market_overview.csv",
                     ["marketplace", "category", "monthly_revenue_usd", "avg_price", "top10_brand_share_pct"], 1),
        _table_check(run, "data/05_price_band.csv",
                     ["marketplace", "price_band", "product_count", "units_share_pct"], 3),
        _table_check(run, "data/05_competitors.csv",
                     ["marketplace", "asin", "brand", "price", "monthly_units", "rating", "reviews"], 10),
        _table_check(run, "data/05_market_overview.csv",
                     ["marketplace", "category", "monthly_revenue_usd", "avg_price",
                      "top10_brand_share_pct", "source_tool", "source_tier"], 1),
    ]
    p = run.dir / "data/05_market_overview.csv"
    if p.is_file():
        _, rows = read_csv(p)
        r = rows[0] if rows else {}
        rev = to_float(r.get("monthly_revenue_usd"))
        share = to_float(r.get("top10_brand_share_pct"))
        if rev is not None:
            checks.append({"check": "market_is_big_enough", "ok": rev >= 1_000_000,
                           "detail": f"monthly revenue = ${rev:,.0f} (need >= $1M/month to be interesting)"})
        if share is not None:
            checks.append({"check": "market_not_locked_up", "ok": share <= 70,
                           "detail": f"top-10 brand share = {share:.1f}% (need <=70% for a newcomer to have room)"})
    return checks


def g_s6(run: Run) -> list[dict]:
    checks = [_table_check(run, "data/06_candidate_scores.csv",
                           ["candidate", "total_score", "verdict", "evidence_refs"], 3)]
    p = run.dir / "data/06_candidate_scores.csv"
    if p.is_file():
        _, rows = read_csv(p)
        scored = [r for r in rows if to_float(r.get("total_score")) is not None]
        checks.append({"check": "scores_numeric", "ok": len(scored) == len(rows),
                       "detail": f"{len(scored)}/{len(rows)} rows have a numeric total_score"})
        with_refs = [r for r in rows if (r.get("evidence_refs") or "").strip()]
        checks.append({"check": "every_score_is_traceable", "ok": len(with_refs) == len(rows),
                       "detail": f"{len(with_refs)}/{len(rows)} rows cite evidence_refs back to S2/S4/S5"})
    return checks


def g_s7(run: Run) -> list[dict]:
    checks = [
        _table_check(run, "out/selection_data.xlsx", None, 1),
        _table_check(run, "out/selection_report.html", None, 1),
    ]
    p = run.dir / "out/selection_report.html"
    if p.is_file():
        html = p.read_text(encoding="utf-8", errors="replace")
        checks.append({"check": "report_has_conclusions", "ok": "结论" in html or "Conclusion" in html,
                       "detail": f"{len(html)} chars"})
        charts = html.count("<svg") + html.count("data-chart")
        checks.append({"check": "report_has_visuals", "ok": charts >= 1,
                       "detail": f"{charts} chart/anchor elements"})
    return checks


GATES = {"g_s0": g_s0, "g_s1": g_s1, "g_s2": g_s2, "g_s3": g_s3, "g_s4": g_s4, "g_s5": g_s5, "g_s6": g_s6, "g_s7": g_s7}


def run_gate(run: Run, sid: str) -> dict:
    checks: list[dict] = []
    for key in STAGE_BY_ID[sid]["gate"]:
        checks.extend(GATES[key](run))
    # analysis artifacts are required too, but only after data is in place
    data_ok = all(c["ok"] for c in checks)
    if data_ok:
        for rel, _ in STAGE_BY_ID[sid]["analysis"]:
            checks.append(_file_check(run, rel))
    return gate_result(all(c["ok"] for c in checks), checks)


# --------------------------------------------------------------------------- fetchers
def _mcp_tool(source: str, tool: str, args: dict, out_path: Path) -> dict:
    """Call one MCP tool through mcp_call.py and persist the raw payload."""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    cmd = [sys.executable, str(HERE / "mcp_call.py"), "call", "--source", source,
           "--tool", tool, "--args", json.dumps(args, ensure_ascii=False), "--out", str(out_path)]
    p = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
    payload = out_path.read_text(encoding="utf-8") if out_path.is_file() else ""
    return {"ok": p.returncode == 0, "returncode": p.returncode,
            "stderr": p.stderr[-400:], "payload_path": str(out_path), "payload": payload}


def mcp_content_text(payload: str) -> str:
    """Unwrap the MCP content[] envelope and return concatenated text."""
    try:
        obj = json.loads(payload)
    except json.JSONDecodeError:
        return ""
    txt = ""
    for ch in ((obj.get("result") or {}).get("content") or []):
        if ch.get("type") == "text":
            txt += ch.get("text", "")
    return txt


def looks_like_quota_error(text: str) -> bool:
    """Sorftime answers HTTP 200 with an in-band quota message - must be caught."""
    markers = ("使用次数已达到上限", "升级您的套餐", "quota", "rate limit", "insufficient", "余额不足", "exceeded")
    low = text.lower()
    return len(text) < 200 and any(m.lower() in low for m in markers)


def fetch_s0(run: Run) -> dict:
    out = run.dir / "raw" / "00_preflight.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    cmd = [sys.executable, str(HERE / "mcp_call.py"), "check", "--out", str(out)]
    p = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
    if not out.is_file():
        return {"ok": False, "detail": p.stderr[-500:] or p.stdout[-500:]}
    data = json.loads(out.read_text(encoding="utf-8"))
    rows = []
    for name, e in (data.get("sources") or {}).items():
        rows.append({
            "source": name, "kind": e.get("kind"), "tier": e.get("tier"),
            "status": e.get("status"), "tool_count": e.get("tool_count", ""),
            "notes": (e.get("error") or e.get("note") or "")[:300],
            "captured_at": data.get("checked_at", now()),
        })
    # CLI/tool tiers have no probe; record them so the fallback chain is visible
    write_csv(run.dir / "data" / "00_source_capability.csv",
              ["source", "kind", "tier", "status", "tool_count", "notes", "captured_at"], rows)
    plan = [
        "# 数据源自检与执行计划",
        "",
        f"- 生成时间：{data.get('checked_at')}",
        f"- 主题：{run.read().get('topic')}",
        f"- 目标市场：{run.read().get('market')}",
        "",
        "## 数据源状态",
        "",
        "| source | kind | tier | status | tools | notes |",
        "|---|---|---|---|---|---|",
    ]
    for r in rows:
        plan.append(f"| {r['source']} | {r['kind']} | {r['tier']} | {r['status']} | {r['tool_count']} | {r['notes']} |")
    plan += [
        "",
        "## 降级链",
        "",
        "`MCP(一级) -> Apify(二级) -> WebFetch/WebSearch(三级) -> 浏览器操作(四级)`",
        "",
        "任一阶段的数据获取都必须按此顺序尝试，并在数据文件的 `source_tier` 列记录实际命中层级。",
        "",
        "## 执行计划",
        "",
        "| 阶段 | 名称 | 数据源 | 产出 |",
        "|---|---|---|---|",
    ]
    for s in STAGES:
        outs = ", ".join(rel for rel, _ in s["data"])
        plan.append(f"| {s['id']} | {s['name']} | {', '.join(s['fallback'])} | {outs} |")
    (run.dir / "analysis" / "00_plan.md").write_text("\n".join(plan) + "\n", encoding="utf-8")
    return {"ok": True, "detail": f"{len(rows)} sources probed"}


def _read_seed_keywords(run: Run) -> list[str]:
    seeds = list(run.read().get("seed_keywords") or [])
    p = run.dir / "data" / "01_seed_keywords.csv"
    if p.is_file():
        _, rows = read_csv(p)
        seeds += [r["keyword"] for r in rows if r.get("keyword")]
    seen, out = set(), []
    for s in seeds:
        if s and s.lower() not in seen:
            seen.add(s.lower())
            out.append(s)
    return out


def fetch_s2(run: Run) -> dict:
    """Google Trends per keyword via SellerSprite; compute CAGR + seasonality."""
    seeds = _read_seed_keywords(run)
    market = run.read().get("market") or "US"
    if not seeds:
        return {"ok": False, "detail": "no seed keywords. Fill data/01_seed_keywords.csv first."}
    ts_rows, sum_rows, errors = [], [], []
    for kw in seeds:
        raw_path = run.dir / "raw" / f"02_trend_{abs(hash(kw)) % 10**8}.json"
        res = _mcp_tool("sellersprite", "google_trend",
                        {"request": {"keyword": kw, "marketplace": market, "monthly": True}}, raw_path)
        text = mcp_content_text(res.get("payload", ""))
        if looks_like_quota_error(text):
            errors.append(f"{kw}: quota")
            continue
        try:
            body = json.loads(text)
            items = ((body.get("data") or {}).get("items") or [])
        except (json.JSONDecodeError, AttributeError):
            errors.append(f"{kw}: unparsable response")
            continue
        if not items:
            errors.append(f"{kw}: empty series")
            continue
        vals = [(int(i["time"]), float(i["value"])) for i in items if i.get("value") is not None]
        for t, v in vals:
            ts_rows.append({"keyword": kw, "period": time.strftime("%Y-%m", time.gmtime(t / 1000)),
                            "value": v, "source": "sellersprite:google_trend",
                            "source_tier": "mcp", "captured_at": now()})
        sum_rows.append(_trend_summary(kw, vals))
    if ts_rows:
        write_csv(run.dir / "data" / "02_trend_timeseries.csv",
                  ["keyword", "period", "value", "source", "source_tier", "captured_at"], ts_rows)
    if sum_rows:
        write_csv(run.dir / "data" / "02_trend_summary.csv",
                  ["keyword", "n_periods", "first_value", "last_value", "cagr_pct",
                   "seasonality_index", "trend_verdict", "source_tier"], sum_rows)
    notes = ["# 趋势验证笔记", "", f"- 市场：{market}", f"- 关键词数：{len(sum_rows)}", ""]
    for r in sum_rows:
        notes.append(f"- **{r['keyword']}**：{r['n_periods']} 期，CAGR {r['cagr_pct']}%，"
                     f"季节性指数 {r['seasonality_index']}，判定 **{r['trend_verdict']}**")
    if errors:
        notes += ["", "## 未取得数据的词（需走降级链）", ""] + [f"- {e}" for e in errors]
    (run.dir / "analysis" / "02_trend_notes.md").write_text("\n".join(notes) + "\n", encoding="utf-8")
    return {"ok": bool(sum_rows), "detail": f"{len(sum_rows)} keywords summarised; errors={errors}"}


def _trend_summary(kw: str, vals: list[tuple[int, float]]) -> dict:
    vals = sorted(vals)
    ys = [v for _, v in vals]
    n = len(ys)
    first, last = ys[0], ys[-1]
    years = max((vals[-1][0] - vals[0][0]) / 1000 / 31_557_600, 0.5)
    cagr = ((last / first) ** (1 / years) - 1) * 100 if first > 0 else 0.0
    # seasonality: how much of the variance is explained by month-of-year
    by_month: dict[int, list[float]] = {}
    for t, v in vals:
        by_month.setdefault(time.gmtime(t / 1000).tm_mon, []).append(v)
    means = [statistics.fmean(v) for v in by_month.values() if v]
    grand = statistics.fmean(ys) or 1.0
    seasonality = ((max(means) - min(means)) / grand * 100) if len(means) >= 4 else 0.0
    verdict = "Trend" if (cagr >= 10 and n >= 24) else ("Fad" if cagr < 0 else "Unclear")
    return {"keyword": kw, "n_periods": n, "first_value": round(first, 2), "last_value": round(last, 2),
            "cagr_pct": round(cagr, 2), "seasonality_index": round(seasonality, 1),
            "trend_verdict": verdict, "source_tier": "mcp"}


def _pct(v) -> float | str:
    """SellerSprite returns concentration ratios as 0..1 fractions."""
    x = to_float(v)
    return round(x * 100, 2) if x is not None else ""


def fetch_s5(run: Run) -> dict:
    """Amazon market overview (market_research) + competitor sample (product_research)."""
    state = run.read()
    market = state.get("market") or "US"
    keyword = state.get("product_keyword") or state.get("category") or ""
    detail: dict = {}

    # ---- 1) category-level market overview
    # departmentKeyword is matched against Amazon department/node names and silently
    # returns 0 rows on a miss. Multi-word phrases ("yoga socks") miss; single words
    # ("socks") and department names ("Sports & Outdoors") hit. Probe widest-first.
    probes: list[str] = []
    for k in (state.get("category"), state.get("product_keyword"), keyword):
        if k:
            probes.append(k)
    for tok in (state.get("product_keyword") or keyword or "").split():
        if len(tok) > 3:
            probes.append(tok)
    probes = list(dict.fromkeys(probes))
    items, used_keyword, attempts = [], "", []
    for probe in probes:
        raw_path = run.dir / "raw" / f"05_market_research_{abs(hash(probe)) % 10**8}.json"
        res = _mcp_tool("sellersprite", "market_research",
                        {"request": {"marketplace": market, "departmentKeyword": probe}}, raw_path)
        text = mcp_content_text(res.get("payload", ""))
        if looks_like_quota_error(text) or not text:
            attempts.append({"keyword": probe, "rows": 0, "error": (text or res.get("stderr", ""))[:160]})
            continue
        try:
            data = json.loads(text).get("data") or {}
        except json.JSONDecodeError:
            attempts.append({"keyword": probe, "rows": 0, "error": "unparsable"})
            continue
        got = (data.get("items") if isinstance(data, dict) else data) or []
        attempts.append({"keyword": probe, "rows": len(got)})
        if got:
            items, used_keyword = got, probe
            (run.dir / "raw" / "05_market_research.json").write_text(text, encoding="utf-8")
            break
    if not items:
        return {"ok": False, "detail": f"market_research returned no rows for any of {probes}", "attempts": attempts}
    rows = [{
        "marketplace": market,
        "category": it.get("nodeLabelPath") or used_keyword,
        "category_node_id": it.get("nodeId") or "",
        "sample_scope": f"sellerSprite market_research, top {it.get('topProducts')} listings",
        "product_count": it.get("totalProducts") or "",
        "monthly_units": it.get("totalUnits") or "",
        "monthly_revenue_usd": it.get("totalRevenue") or "",
        "avg_price": it.get("avgPrice") or "",
        "avg_profit_pct": it.get("avgProfit") or "",
        "top3_brand_share_pct": _pct(it.get("top3BrandCrn")),
        "top10_brand_share_pct": _pct(it.get("top10BrandCrn")),
        "brand_count": it.get("brands") or "",
        "new_product_share_pct": _pct(it.get("l12NewRatio")),
        "return_rate_pct": it.get("returnRatio") or "",
        "search_to_purchase_ratio": it.get("searchToPurchaseRatio") or "",
        "source_tool": "sellersprite:market_research",
        "source_tier": "mcp",
        "captured_at": now(),
    } for it in items[:50]]
    write_csv(run.dir / "data" / "05_market_overview.csv",
              ["marketplace", "category", "category_node_id", "sample_scope", "product_count",
               "monthly_units", "monthly_revenue_usd", "avg_price", "avg_profit_pct",
               "top3_brand_share_pct", "top10_brand_share_pct", "brand_count",
               "new_product_share_pct", "return_rate_pct", "search_to_purchase_ratio",
               "source_tool", "source_tier", "captured_at"], rows)
    detail["overview_rows"] = len(rows)
    detail["overview_attempts"] = attempts
    detail["overview_keyword_used"] = used_keyword

    # ---- 2) competitor sample for the keyword
    if keyword:
        c_raw = run.dir / "raw" / "05_product_research.json"
        # matchType 3 = exact phrase; the default (2, fuzzy) drags in unrelated listings.
        cres = _mcp_tool("sellersprite", "product_research",
                         {"request": {"marketplace": market, "keyword": keyword, "size": 30,
                                      "page": 1, "matchType": 3,
                                      "order": {"field": "total_units", "desc": True}}}, c_raw)
        ctext = mcp_content_text(cres.get("payload", ""))
        if ctext and not looks_like_quota_error(ctext):
            try:
                cdata = json.loads(ctext).get("data") or {}
                citems = cdata.get("items") if isinstance(cdata, dict) else cdata
                crows = [{
                    "marketplace": market,
                    "asin": it.get("asin") or "",
                    "title": (it.get("title") or "")[:180],
                    "brand": it.get("brand") or "",
                    "seller": it.get("sellerName") or "",
                    "price": it.get("price") or "",
                    "monthly_units": it.get("units") or it.get("amzUnit") or "",
                    "monthly_revenue_usd": it.get("revenue") or it.get("amzRevenue") or "",
                    "rating": it.get("rating") or "",
                    "reviews": it.get("ratings") or "",
                    "bsr": it.get("bsr") or "",
                    "listing_date": it.get("availableDate") or it.get("listingDate") or "",
                    "search_tool": "sellersprite:product_research",
                    "source_tier": "mcp",
                    "captured_at": now(),
                } for it in (citems or [])]
                if crows:
                    write_csv(run.dir / "data" / "05_competitors.csv",
                              ["marketplace", "asin", "title", "brand", "seller", "price",
                               "monthly_units", "monthly_revenue_usd", "rating", "reviews",
                               "bsr", "listing_date", "search_tool", "source_tier", "captured_at"], crows)
                    detail["competitor_rows"] = len(crows)
            except json.JSONDecodeError:
                detail["competitor_error"] = "unparsable product_research response"
        else:
            detail["competitor_error"] = (ctext or "empty")[:160]
    return {"ok": True, "detail": detail}


FETCHERS = {"S0": fetch_s0, "S1": None, "S2": fetch_s2, "S3": None, "S4": None, "S5": fetch_s5, "S6": None, "S7": None}


def do_finalize(run: Run) -> dict:
    results = {}
    for script, label in (("build_excel.py", "excel"), ("build_report.py", "report")):
        p = subprocess.run([sys.executable, str(HERE / script), "--run-dir", str(run.dir)],
                           capture_output=True, text=True, timeout=600)
        results[label] = {"ok": p.returncode == 0, "out": p.stdout[-400:], "err": p.stderr[-400:]}
        print(f"[{label}] {'OK' if p.returncode == 0 else 'FAIL'}")
        if p.stdout.strip():
            print("   " + p.stdout.strip().replace("\n", "\n   ")[-600:])
        if p.returncode != 0 and p.stderr.strip():
            print("   " + p.stderr.strip().replace("\n", "\n   ")[-600:])
    return results


# --------------------------------------------------------------------------- cli
def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--run-dir", default="./selection_run")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("init")
    p.add_argument("--topic", required=True)
    p.add_argument("--market", default="US")
    p.add_argument("--category", default="", help="Amazon category path used for market_research departmentKeyword")
    p.add_argument("--product-keyword", default="", help="product keyword used for product_research competitor sample")
    p.add_argument("--seed", action="append", default=[])

    p = sub.add_parser("status")
    p.add_argument("--json", action="store_true")

    p = sub.add_parser("fetch"); p.add_argument("--stage", required=True); p.add_argument("--force", action="store_true")
    p = sub.add_parser("check"); p.add_argument("--stage", required=True)
    p = sub.add_parser("check-all")
    p = sub.add_parser("next")
    p = sub.add_parser("note"); p.add_argument("--stage", required=True); p.add_argument("--text", required=True)
    p = sub.add_parser("finalize")

    args = ap.parse_args()
    run_dir = Path(args.run_dir).resolve()

    if args.cmd == "init":
        Run.create(run_dir, args.topic, args.market, args.seed,
                   category=args.category, product_keyword=args.product_keyword)
        print(f"initialised pipeline at {run_dir}")
        print(f"topic={args.topic!r} market={args.market} category={args.category!r} seeds={args.seed}")
        return 0

    run = Run.load(run_dir)

    if args.cmd == "status":
        st = run.read()
        if args.json:
            print(json.dumps(st, ensure_ascii=False, indent=2))
        else:
            print(f"topic: {st['topic']}  market: {st['market']}  updated: {st['updated_at']}")
            for s in STAGES:
                e = st["stages"][s["id"]]
                mark = {"pending": " ", "running": "~", "passed": "P", "failed": "X", "degraded": "!"}.get(e["status"], "?")
                print(f"  [{mark}] {s['id']} {s['name']:32} {e['status']}")
        return 0

    if args.cmd == "next":
        st = run.read()
        for s in STAGES:
            if st["stages"][s["id"]]["status"] != "passed":
                print(s["id"])
                return 0
        print("DONE")
        return 0

    if args.cmd == "note":
        st = run.read()
        st["stages"][args.stage]["notes"].append({"at": now(), "text": args.text})
        run.save(st)
        print(f"noted on {args.stage}")
        return 0

    if args.cmd == "fetch":
        sid = args.stage.upper()
        st = run.read()
        if st["stages"][sid]["status"] == "passed" and not args.force:
            print(f"{sid} already passed; use --force to refetch")
            return 0
        fn = FETCHERS.get(sid)
        if fn is None:
            spec = STAGE_BY_ID[sid]
            print(f"{sid} {spec['name']} has no deterministic fetcher - it needs agent judgement.")
            print("Required data files:")
            for rel, cols in spec["data"]:
                print(f"  {rel}")
                if cols:
                    print(f"    columns: {', '.join(cols)}")
            print("Suggested sources (in fallback order): " + " -> ".join(spec["fallback"]))
            print("After writing the files, run: pipeline.py check --stage " + sid)
            return 0
        run.mark(sid, "running")
        out = fn(run)
        print(json.dumps(out, ensure_ascii=False, indent=2))
        run.mark(sid, "running" if out.get("ok") else "failed")
        return 0 if out.get("ok") else 1

    if args.cmd == "check":
        sid = args.stage.upper()
        verdict = run_gate(run, sid)
        out = run.dir / "analysis" / f"gate_{sid}.json"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(verdict, ensure_ascii=False, indent=2), encoding="utf-8")
        run.mark(sid, "passed" if verdict["ok"] else "failed", verdict)
        for c in verdict["checks"]:
            print(f"  [{'PASS' if c['ok'] else 'FAIL'}] {c['check']:28} {c.get('path', '')} {c.get('detail', '')}")
        print(f"{sid}: {'PASSED' if verdict['ok'] else 'FAILED'}  (verdict -> {out})")
        return 0 if verdict["ok"] else 1

    if args.cmd == "check-all":
        worst = 0
        for s in STAGES:
            sid = s["id"]
            verdict = run_gate(run, sid)
            (run.dir / "analysis" / f"gate_{sid}.json").write_text(
                json.dumps(verdict, ensure_ascii=False, indent=2), encoding="utf-8")
            run.mark(sid, "passed" if verdict["ok"] else "failed", verdict)
            bad = [c for c in verdict["checks"] if not c["ok"]]
            print(f"{sid} {'PASS' if verdict['ok'] else 'FAIL'}  ({len(verdict['checks']) - len(bad)}/{len(verdict['checks'])} checks)")
            for c in bad:
                print(f"     FAIL {c['check']}: {c.get('detail', '')}")
            worst = worst or (0 if verdict["ok"] else 1)
        return worst

    if args.cmd == "finalize":
        print("building deliverables...")
        do_finalize(run)
        verdict = run_gate(run, "S7")
        (run.dir / "analysis" / "gate_S7.json").write_text(
            json.dumps(verdict, ensure_ascii=False, indent=2), encoding="utf-8")
        run.mark("S7", "passed" if verdict["ok"] else "failed", verdict)
        for c in verdict["checks"]:
            print(f"  [{'PASS' if c['ok'] else 'FAIL'}] {c['check']:28} {c.get('path','')} {c.get('detail','')}")
        return 0 if verdict["ok"] else 1

    return 2


if __name__ == "__main__":
    sys.exit(main())

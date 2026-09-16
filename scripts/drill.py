#!/usr/bin/env python3
"""Multi-level drill-down for 圈层热词 (segment hot-word) discovery.

Why this exists
---------------
S1 must not be a single flat list of keywords the operator types from memory.
The method is a *drill*: a seed word opens a ring of hotter, more specific words
belonging to the same community, and each further ring needs a human pass
because machine-ranked rings drift towards generic head terms (see below).

Two rings, two data planes
--------------------------
  --ring social   tikhub: search the parent word, then collect the hashtags the
                  videos actually carry plus the #tags inside their captions.
                  This is the co-occurrence ring - what the community calls
                  itself. Has NO ready-made "related identity word" API; the
                  ring is extracted from content.
  --ring ecom     SellerSprite `keyword_miner`: given one keyword it returns
                  that word's related-keyword ring with absolute search volume.
                  Falls back to Sorftime `keyword_extends`, then to a manual web
                  paste. These are Amazon *search terms* - product words, not
                  identity words. Do not confuse the two planes.

The drift trap (measured, not theorised)
----------------------------------------
Drilling `pilates grip socks` on the ecom ring returns, by absolute volume:

    yoga mat                     1,129,657
    halloween                      781,484
    socks                          767,328
    pilates socks                  425,318   <- the actual sibling
    socks for women                361,682
    womens socks                   216,723
    grip socks                     197,407

`halloween` and `yoga mat` outrank the real sibling by 2-3x. Ranking the ring
by absolute volume alone therefore walks straight into head/unrelated terms -
and "what already sells on the platform is by definition a red ocean" is the
starting axiom of this whole method. The social ring drifts too: drilling
`pilates girl` puts the head term `pilates` at #1 (47 co-occurrences).

So a ring is never auto-adopted: `--accept` must be given explicit words, and
every accepted word is written with `level` + `parent_keyword` so the ring it
came from stays auditable.

API traps (all measured)
------------------------
  keyword_miner            params wrapped in `request`; takes `size` (default
                           5!) but REJECTS `historyDate` - passing it silently
                           returns total=0 with HTTP-200 OK, which looks
                           identical to "no related words exist".
  keyword_research_trends  flat params (no `request` wrapper); response is a
                           LIST under `data`; misspells `keywrod`.
  keyword_extends          (Sorftime) flat params; quota exhaustion arrives as
                           HTTP 200 with a 20-char Chinese body.

Usage
-----
  python3 drill.py --run-dir ./selection_run --ring ecom   --from "pilates grip socks"
  python3 drill.py --run-dir ./selection_run --ring social --from "pilates girl" --top 25
  python3 drill.py --run-dir ./selection_run --ring social --from "pilates girl" \
      --accept "pilatesstrength,pilatescommunity" --type identity
"""
from __future__ import annotations

import argparse
import collections
import json
import re
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import pipeline as pl  # noqa: E402

# A hashtag token: ASCII word chars or CJK, at least 2 chars. TikTok captions
# carry them inline ("🫶🏼 #workoutsplit #pilatesstrength"), so we scrape the text
# as well as the structured `challenges` array.
HASH_RE = re.compile(r"#([0-9A-Za-z_\u4e00-\u9fff]{2,40})")

# Hashtags that co-occur with everything and therefore mean nothing. Two kinds:
# reach tags (fyp...) and platform artifacts that TikTok attaches to every video
# regardless of content ("original sound" was the #6 hit on a real pilates drill).
STOP_TAGS = {
    "fyp", "foryou", "foryoupage", "viral", "trending", "tiktok", "fypシ",
    "explore", "reels", "shorts", "subscribe", "follow", "like", "share",
    "tiktokmademebuyit", "tiktokshop", "amazonfinds", "viralvideo",
    "original sound", "originalsound", "originalaudio", "sound", "capcut",
    "editing", "video", "content", "contentcreator",
}


# --------------------------------------------------------------------- helpers
def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _slug(s: str, n: int = 40) -> str:
    return re.sub(r"[^0-9A-Za-z]+", "-", s).strip("-").lower()[:n]


def _rel(p: Path, run: pl.Run) -> str:
    try:
        return str(p.relative_to(run.dir))
    except ValueError:
        return str(p)


def _read_run(run_dir: str) -> pl.Run:
    d = Path(run_dir).resolve()
    if not (d / pl.RUN_MARKER).is_file():
        raise SystemExit(f"no pipeline run at {d} (missing {pl.RUN_MARKER}). run `init` first.")
    return pl.Run(d)


def _parse_json(text: str):
    """SellerSprite wraps its payload as a JSON *string* inside result.content[].text."""
    try:
        return json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return None


def _rows_with_key(obj, key: str) -> list[dict]:
    """Recursively collect every dict carrying `key` (payload nesting varies)."""
    out: list[dict] = []

    def walk(o):
        if isinstance(o, list):
            for x in o:
                walk(x)
        elif isinstance(o, dict):
            if key in o:
                out.append(o)
            for v in o.values():
                walk(v)

    walk(obj)
    return out


# ------------------------------------------------------------------ ecom ring
def drill_ecom(parent: str, market: str, run: pl.Run, size: int = 20) -> dict:
    """keyword_miner (SellerSprite) -> keyword_extends (Sorftime) -> manual."""
    attempts: list[dict] = []
    slug = _slug(parent)

    # -- tier 1: SellerSprite keyword_miner (passing `keyword` returns the ring,
    #    NOT the parent itself; `keywordList` would be exact-match only).
    #    `historyDate` must NOT be sent: this tool rejects it and answers
    #    HTTP 200 with total=0/items=[], which reads exactly like "no related
    #    words". `size` defaults to 5, so it has to be set explicitly or the
    #    ring is truncated to the 5 head terms - the drift trap in miniature.
    req1 = {"marketplace": market, "keyword": parent, "size": size}
    raw1 = run.dir / "raw" / f"drill_ecom_{slug}_sellersprite.json"
    t0 = time.time()
    res = pl._mcp_tool("sellersprite", "keyword_miner", {"request": req1}, raw1)
    text = pl.mcp_content_text(res.get("payload", ""))
    latency = int((time.time() - t0) * 1000)
    body = _parse_json(text)
    cands: list[dict] = []
    if body and not pl.looks_like_quota_error(text):
        for r in _rows_with_key(body, "keyword"):
            kw = str(r.get("keyword") or "").strip()
            if not kw:
                continue
            cands.append({
                "keyword": kw,
                "metric_name": "searches",
                "metric_value": pl.to_float(r.get("searches")),
                "evidence": "keyword_miner:related",
                "extra": {
                    "purchase_rate": r.get("purchaseRate"),
                    "monopoly_click_rate": r.get("monopolyClickRate"),
                    "supply_demand_ratio": r.get("supplyDemandRatio"),
                    "ad_products": r.get("adProducts"),
                    "search_rank": r.get("searchRank"),
                    "avg_price": r.get("avgPrice"),
                },
            })
    attempts.append({"source": "sellersprite", "tier": "mcp", "tool": "keyword_miner",
                     "request": req1,
                     "status": "ok" if cands else ("quota" if body is None or
                                                   pl.looks_like_quota_error(text) else "empty"),
                     "latency_ms": latency, "candidates": len(cands),
                     "error": "" if cands else (res.get("stderr") or "")[:200],
                     "raw_path": _rel(raw1, run)})
    if cands:
        return {"ok": True, "ring": "ecom", "parent": parent, "market": market,
                "tier": "mcp", "source": "sellersprite:keyword_miner",
                "candidates": cands, "attempts": attempts, "raw_path": _rel(raw1, run),
                "note": "Amazon 搜索词（商品词空间），不是圈层身份词"}

    # -- tier 2: Sorftime keyword_extends (flat params, unlike SellerSprite)
    raw2 = run.dir / "raw" / f"drill_ecom_{slug}_sorftime.json"
    t0 = time.time()
    res2 = pl._mcp_tool("sorftime", "keyword_extends",
                        {"keyword": parent, "keyword_support_site": market}, raw2)
    text2 = pl.mcp_content_text(res2.get("payload", ""))
    quota = pl.looks_like_quota_error(text2)
    body2 = None if quota else _parse_json(text2)
    cands2: list[dict] = []
    if body2:
        for r in _rows_with_key(body2, "keyword"):
            kw = str(r.get("keyword") or "").strip()
            if kw:
                cands2.append({"keyword": kw, "metric_name": "searches",
                               "metric_value": pl.to_float(r.get("search") or r.get("searches")),
                               "evidence": "keyword_extends", "extra": {}})
    attempts.append({"source": "sorftime", "tier": "mcp", "tool": "keyword_extends",
                     "request": {"keyword": parent, "keyword_support_site": market},
                     "status": "quota" if quota else ("ok" if cands2 else "empty"),
                     "latency_ms": int((time.time() - t0) * 1000),
                     "candidates": len(cands2),
                     "error": "配额耗尽" if quota else (res2.get("stderr") or "")[:200],
                     "raw_path": _rel(raw2, run)})
    if cands2:
        return {"ok": True, "ring": "ecom", "parent": parent, "market": market,
                "tier": "mcp", "source": "sorftime:keyword_extends",
                "candidates": cands2, "attempts": attempts, "raw_path": _rel(raw2, run),
                "note": "Amazon 搜索词（商品词空间），不是圈层身份词"}

    # -- tier 3: manual. The ring must NOT be invented; the operator pastes it.
    attempts.append({"source": "web", "tier": "web", "tool": "人工粘贴",
                     "request": {}, "status": "manual", "latency_ms": 0, "candidates": 0,
                     "error": "两级都未取到，需人工从公开页/后台抄录该词的相关词环"})
    return {"ok": False, "ring": "ecom", "parent": parent, "market": market,
            "tier": "web", "source": "", "candidates": [], "attempts": attempts,
            "raw_path": "", "error": "电商侧两级都未取到相关词环，请人工补齐"}


# ---------------------------------------------------------------- social ring
def drill_social(parent: str, market: str, run: pl.Run, count: int = 30) -> dict:
    """tikhub search -> hashtags carried by the videos + #tags inside captions."""
    slug = _slug(parent)
    raw = run.dir / "raw" / f"drill_social_{slug}_tikhub.json"
    raw.parent.mkdir(parents=True, exist_ok=True)
    cmd = [sys.executable, str(HERE / "mcp_call.py"), "rest", "--source", "tikhub",
           "--path", "/api/v1/tiktok/web/fetch_general_search",
           "--params", json.dumps({"keyword": parent, "count": str(count)}, ensure_ascii=False),
           "--out", str(raw)]
    t0 = time.time()
    p = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
    latency = int((time.time() - t0) * 1000)
    body = _parse_json(raw.read_text(encoding="utf-8")) if raw.is_file() else None

    freq: collections.Counter = collections.Counter()
    evidence: dict[str, str] = {}
    if body:
        # structured: every video's `challenges` array
        for ch in _rows_with_key(body, "title"):
            t = str(ch.get("title") or "").strip().lower()
            if t and t not in STOP_TAGS:
                freq[t] += 1
                evidence.setdefault(t, "hashtag")
        # unstructured: #tags inline in captions
        for d in _rows_with_key(body, "desc"):
            for m in HASH_RE.findall(str(d)):
                t = m.strip().lower()
                if t and t not in STOP_TAGS:
                    freq[t] += 1
                    evidence.setdefault(t, "caption")

    cands = [{"keyword": k, "metric_name": "cooccurrence", "metric_value": float(v),
              "evidence": evidence.get(k, "hashtag"), "extra": {}}
             for k, v in freq.most_common()]

    attempts = [{"source": "tikhub", "tier": "mcp", "tool": "fetch_general_search",
                 "request": {"keyword": parent, "count": str(count)},
                 "status": "ok" if cands else "empty", "latency_ms": latency,
                 "candidates": len(cands),
                 "error": "" if cands else (p.stderr or "no hashtags parsed")[:200],
                 "raw_path": _rel(raw, run)}]
    if not cands:
        attempts.append({"source": "web", "tier": "web", "tool": "人工粘贴",
                         "request": {}, "status": "manual", "latency_ms": 0,
                         "candidates": 0, "error": "未解析出标签环，需人工查看原始响应或走 browser 四级"})
    return {"ok": bool(cands), "ring": "social", "parent": parent, "market": market,
            "tier": "mcp", "source": "tikhub:fetch_general_search",
            "candidates": cands, "attempts": attempts, "raw_path": _rel(raw, run),
            "note": "社媒标签共现环 —— 圈层身份词的来源"}


# ------------------------------------------------------------------- adoption
def keyword_levels(run: pl.Run) -> dict[str, int]:
    """Existing keyword -> level, from state.json seeds (level 0) and the S1 table."""
    levels: dict[str, int] = {}
    for s in (run.read().get("seed_keywords") or []):
        levels[str(s).strip().lower()] = 0
    p = run.dir / "data" / "01_seed_keywords.csv"
    if p.is_file():
        _, rows = pl.read_csv(p)
        for r in rows:
            kw = str(r.get("keyword") or "").strip().lower()
            if not kw:
                continue
            lv = pl.to_float(r.get("level"))
            levels[kw] = int(lv) if lv is not None else 0
    return levels


def accept(run: pl.Run, parent: str, words: list[str], ring: str, ktype: str,
           market: str, source: str, tier: str, metrics: dict[str, dict]) -> dict:
    """Append chosen words to data/01_seed_keywords.csv at level = parent level + 1."""
    levels = keyword_levels(run)
    parent_lv = levels.get(parent.strip().lower(), 0)
    new_level = parent_lv + 1

    path = run.dir / "data" / "01_seed_keywords.csv"
    cols = ["keyword", "keyword_type", "level", "parent_keyword", "platform", "heat_score",
            "heat_cagr_pct", "obs_window", "source_tier", "captured_at", "evidence_url"]
    rows: list[dict] = []
    if path.is_file():
        _, rows = pl.read_csv(path)
    have = {str(r.get("keyword") or "").strip().lower() for r in rows}

    added, skipped = [], []
    for w in words:
        w = w.strip()
        if not w or w.lower() in have:
            skipped.append(w)
            continue
        m = metrics.get(w.lower(), {})
        rows.append({
            "keyword": w,
            "keyword_type": ktype,
            "level": new_level,
            "parent_keyword": parent,
            "platform": "tiktok" if ring == "social" else "amazon",
            "heat_score": m.get("metric_value") if m.get("metric_name") != "searches"
                          else "",
            "heat_cagr_pct": "",
            "obs_window": "",
            "source_tier": tier,
            "captured_at": _now(),
            "evidence_url": f"drill://{ring}/{parent}",
        })
        added.append(w)
        have.add(w.lower())

    pl.write_csv(path, cols, rows)
    return {"ok": True, "added": added, "skipped_existing": skipped,
            "level": new_level, "parent": parent, "parent_level": parent_lv,
            "rows_total": len(rows)}


# ------------------------------------------------------------------------ cli
def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--run-dir", default="./selection_run")
    ap.add_argument("--ring", choices=["ecom", "social"], required=True,
                    help="ecom = 电商/搜索侧（商品词）；social = 社媒侧（圈层身份词）")
    ap.add_argument("--from", dest="parent", required=True, help="被下钻的上一级词")
    ap.add_argument("--market", help="默认取 run 的 market")
    ap.add_argument("--top", type=int, default=20, help="打印前 N 个候选")
    ap.add_argument("--count", type=int, default=30,
                    help="取多少个候选：社媒侧=抓多少条视频，电商侧=keyword_miner 的 size")
    ap.add_argument("--accept", help="逗号分隔：采纳这些候选词，写入 01_seed_keywords.csv")
    ap.add_argument("--type", default="identity",
                    dest="ktype", help="采纳时的 keyword_type，默认 identity")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    run = _read_run(args.run_dir)
    market = (args.market or run.read().get("market") or "").strip().upper()
    if not market:
        print("--market 必填（run 里也没有）：下游每个源都按它取数", file=sys.stderr)
        return 2

    res = (drill_social(args.parent, market, run, args.count) if args.ring == "social"
           else drill_ecom(args.parent, market, run, args.count))

    if args.accept:
        words = [w.strip() for w in args.accept.split(",") if w.strip()]
        metrics = {c["keyword"].lower(): c for c in res["candidates"]}
        res["accepted"] = accept(run, args.parent, words, args.ring, args.ktype,
                                 market, res.get("source") or "", res.get("tier") or "web",
                                 metrics)

    if args.json:
        print(json.dumps(res, ensure_ascii=False, indent=2))
        return 0 if res.get("ok") else 1

    print(f"# 下钻环：{res['ring']}  父词：{res['parent']}  市场：{market}")
    print(f"# 命中：{res.get('source') or '未命中'}（tier={res.get('tier')}）")
    if res.get("note"):
        print(f"# 注意：{res['note']}")
    print()
    if res["candidates"]:
        print(f"{'候选词':34} {'指标':>12}  来源")
        for c in res["candidates"][:args.top]:
            mv = c.get("metric_value")
            mv = f"{mv:,.0f}" if isinstance(mv, (int, float)) else "-"
            print(f"  {c['keyword'][:32]:34} {mv:>12}  {c['evidence']}")
        print()
    if res.get("attempts"):
        print("# 降级链走位")
        for a in res["attempts"]:
            print(f"  - {a['source']:14} tier={a['tier']:5} {a['status']:7} "
                  f"{a['latency_ms']:>6}ms  {a.get('error') or ''}")
    if res.get("accepted"):
        acc = res["accepted"]
        print(f"\n# 已采纳 {len(acc['added'])} 个词，写入 level={acc['level']}"
              f"（父词 {acc['parent']} 在 level={acc['parent_level']}）")
        if acc["skipped_existing"]:
            print(f"  跳过已存在：{', '.join(acc['skipped_existing'])}")
    else:
        print("\n# 未采纳任何词 —— 下钻结果不自动入库。"
              "挑好后用 --accept \"词1,词2\" 写入，或人工填 01_seed_keywords.csv。")
        print("# 提醒：不要只按绝对值挑，绝对值高的往往是泛词/头部红海。")
    return 0 if res.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())

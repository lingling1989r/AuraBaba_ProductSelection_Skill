#!/usr/bin/env python3
"""社媒 x 平台 Trend 选品 MVP - backend.

A thin, stdlib-only HTTP layer over the skill's scripts. It adds the two things
the CLI pipeline deliberately leaves to a human:

  1. TREND ACQUISITION you can watch happen.  Each keyword is walked down the
     fallback chain (sellersprite -> sorftime -> manual web paste). Every attempt
     is recorded with its tier, latency, raw payload and error, so the UI can
     show exactly which tier a number came from instead of asserting one.

  2. HUMAN DECISION POINTS.  Every stage has a question that a machine must not
     answer for you (which identity keyword is the entry, whether an Unclear
     series is Trend or Fad, whether the market is worth entering). A stage does
     not advance until the decision is recorded - and overriding a threshold
     requires a written reason, matching the contract in references/verification.md.

Run:  python3 mvp/server.py [--port 8787] [--open]
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

HERE = Path(__file__).resolve().parent
SKILL_ROOT = HERE.parent
SCRIPTS = SKILL_ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

import mcp_call as mc  # noqa: E402
import pipeline as pl  # noqa: E402

RUN_DIR = HERE / "run"
DECISIONS_PATH = RUN_DIR / "decisions.json"
CACHE_PATH = RUN_DIR / "trend_cache.json"
LAST_TREND_PATH = RUN_DIR / "last_trend.json"
SOURCES_PATH = RUN_DIR / "sources.json"

# --------------------------------------------------------------------------- spec
# The human question for each stage. `kind` drives the widget the UI renders.
HUMAN_POINTS: dict[str, dict] = {
    "S0": {
        "kind": "proceed_or_stop",
        "question": "一级源里有不可用的，是否降级继续？",
        "why": "配额耗尽可能让数据只能覆盖近 3 个月。这会改变结论的可信度，不能由脚本替你决定。",
        "options": [
            {"value": "degrade_ok", "label": "接受降级，继续"},
            {"value": "stop", "label": "停下来，等配额恢复"},
        ],
    },
    "S1": {
        "kind": "pick_one",
        "target": "identity",
        "question": "这些身份热词里，你要进的是哪一个圈层？",
        "why": "入口圈层决定了后面 S3/S4 抓谁的评论和口播。选宽泛的标签会收敛不出单品。",
        "options": [],
    },
    "S2": {
        "kind": "reclassify",
        "question": "逐词复核 Trend / Fad，Unclear 的由你裁决。",
        "why": "CAGR 与季节性指数是机器算的，但『季节性波动』和『结构性增长』的区分往往需要人判断。",
        "options": [],
    },
    "S3": {
        "kind": "pick_one",
        "target": "subcommunity",
        "question": "这个子圈层值得做吗？用它还是换一个？",
        "why": "声量更大的宽泛标签常常无法收敛出单品，需要一个可命名的子圈层作为目标。",
        "options": [],
    },
    "S4": {
        "kind": "pick_one",
        "target": "painpoint",
        "question": "Top 痛点指向哪个载体？决定要做的单品形态。",
        "why": "痛点决定做什么，而不是先有单品再找痛点。这个映射是产品策略判断，不是统计结果。",
        "options": [],
    },
    "S5": {
        "kind": "proceed_or_stop",
        "question": "市场够大、没有被锁死吗？要不要继续？",
        "why": "月销售额与头部集中度是第三方估算，阈值达标不代表能进场。这一步是人的商业判断。",
        "options": [
            {"value": "go", "label": "继续，进入评分"},
            {"value": "kill", "label": "放弃这个方向"},
        ],
    },
    "S6": {
        "kind": "pick_one",
        "target": "candidate",
        "question": "建议只对哪一个候选进入打样？",
        "why": "评分可以并列，资源不能。必须选一个，并写下为什么不是其他几个。",
        "options": [],
    },
    "S7": {"kind": "none", "question": "交付由脚本生成，无需人工判断。", "why": "", "options": []},
}

# Fallback chain for trend data. Each entry: (source, tier label, tool, arg builder).
TREND_CHAIN = [
    ("sellersprite", "mcp", "google_trend",
     lambda kw, market: {"request": {"keyword": kw, "marketplace": market, "monthly": True}}),
    ("sorftime", "mcp", "keyword_trend",
     lambda kw, market: {"keyword": kw, "keyword_support_site": market}),
]


# --------------------------------------------------------------------------- io
def now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _load(path: Path, default):
    if path.is_file():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return default
    return default


def _save(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")


def load_decisions() -> dict:
    return _load(DECISIONS_PATH, {})


def load_cache() -> dict:
    return _load(CACHE_PATH, {})


def load_config() -> dict:
    try:
        return mc.load_config(None)
    except SystemExit:
        return {"sources": {}, "fallback_chain": ["mcp", "apify", "web", "browser"], "gate_defaults": {}}


# --------------------------------------------------------------------------- sources
def probe_sources() -> dict:
    """Live connectivity probe. Mirrors `mcp_call.py check` but returns structured data."""
    cfg = load_config()
    out = {"checked_at": now(), "sources": {}}
    for name, src in (cfg.get("sources") or {}).items():
        kind = src.get("kind")
        entry = {"tier": src.get("tier"), "kind": kind, "label": src.get("label"),
                 "covers": src.get("covers") or []}
        t0 = time.time()
        if kind in ("mcp", "mcp+rest"):
            try:
                s = mc.McpSession(name, src)
                info = s.initialize()
                tools = s.call("tools/list", {"cursor": None}, req_id=2)
                tl = (tools.get("result") or {}).get("tools") or []
                entry.update(status="ok", tool_count=len(tl),
                             server=(info.get("serverInfo") or {}).get("name"),
                             tools=[t["name"] for t in tl])
            except SystemExit as e:
                entry.update(status="error", error=str(e)[:300])
            except Exception as e:  # noqa: BLE001
                entry.update(status="error", error=f"{type(e).__name__}: {e}"[:300])
        elif kind == "cli":
            import os
            import shutil
            found = shutil.which(src.get("command") or "")
            env_missing = [k for k in (src.get("env") or []) if not os.environ.get(k)]
            entry.update(status="ok" if (found and not env_missing) else "degraded",
                         binary=found, env_missing=env_missing)
        else:
            entry.update(status="ok", note="agent tool - no network probe possible")
        entry["latency_ms"] = int((time.time() - t0) * 1000)
        out["sources"][name] = entry
    _save(SOURCES_PATH, out)
    return out


# --------------------------------------------------------------------------- trend
def _slug(text: str) -> str:
    return "".join(c if c.isalnum() else "-" for c in text.lower())[:48].strip("-")


def extract_series(payload) -> list[tuple[str, float]]:
    """Find a [(period, value)] series anywhere in an unknown response shape.

    The three sources have different envelopes and no shared schema, so we search
    for the largest list of dicts that has both a time-like and a value-like key.
    """
    best: list[tuple[str, float]] = []
    time_keys = ("time", "date", "period", "month", "timestamp", "ts", "startDate", "ds")
    val_keys = ("value", "count", "search", "index", "heat", "score", "volume", "y")

    def norm_period(raw) -> str | None:
        if isinstance(raw, (int, float)):
            v = float(raw)
            if v > 1e11:          # ms epoch
                v /= 1000
            if v > 1e8:           # s epoch
                return time.strftime("%Y-%m", time.gmtime(v))
            return None
        s = str(raw)
        return s[:7] if len(s) >= 7 else None

    def walk(node):
        nonlocal best
        if isinstance(node, list):
            rows: list[tuple[str, float]] = []
            for item in node:
                if not isinstance(item, dict):
                    rows = []
                    break
                tk = next((k for k in time_keys if k in item), None)
                vk = next((k for k in val_keys if k in item), None)
                if not tk or not vk:
                    rows = []
                    break
                p = norm_period(item[tk])
                try:
                    v = float(item[vk])
                except (TypeError, ValueError):
                    rows = []
                    break
                if p:
                    rows.append((p, v))
            if len(rows) > len(best):
                best = rows
            for item in node:
                walk(item)
        elif isinstance(node, dict):
            for v in node.values():
                walk(v)

    walk(payload)
    return sorted(best)


def summarize(kw: str, series: list[tuple[str, float]], tier: str) -> dict:
    """CAGR + seasonality + verdict. Deliberately the same rule as pipeline._trend_summary."""
    import statistics
    ys = [v for _, v in series]
    n = len(ys)
    if n < 2:
        return {}
    first, last = ys[0], ys[-1]
    a, b = series[0][0], series[-1][0]
    months = (int(b[:4]) - int(a[:4])) * 12 + (int(b[5:7]) - int(a[5:7]))
    years = max(months / 12.0, 0.5)
    cagr = ((last / first) ** (1 / years) - 1) * 100 if first > 0 else 0.0
    by_month: dict[int, list[float]] = {}
    for p, v in series:
        by_month.setdefault(int(p[5:7]), []).append(v)
    means = [statistics.fmean(v) for v in by_month.values() if v]
    grand = statistics.fmean(ys) or 1.0
    seasonality = ((max(means) - min(means)) / grand * 100) if len(means) >= 4 else 0.0
    verdict = "Trend" if (cagr >= 10 and n >= 24) else ("Fad" if cagr < 0 else "Unclear")
    return {"keyword": kw, "n_periods": n, "first_value": round(first, 2), "last_value": round(last, 2),
            "cagr_pct": round(cagr, 2), "seasonality_index": round(seasonality, 1),
            "trend_verdict": verdict, "source_tier": tier}


def _call_tool(source: str, tool: str, args: dict) -> dict:
    cfg = load_config()
    try:
        src = mc.get_source(cfg, source)
    except SystemExit as e:
        return {"ok": False, "error": str(e), "payload": ""}
    if not src.get("mcp_url"):
        return {"ok": False, "error": "source has no mcp_url", "payload": ""}
    t0 = time.time()
    try:
        s = mc.McpSession(source, src)
        s.initialize()
        res = s.call("tools/call", {"name": tool, "arguments": args})
    except SystemExit as e:
        return {"ok": False, "error": str(e)[:300], "payload": "", "latency_ms": int((time.time() - t0) * 1000)}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": f"{type(e).__name__}: {e}"[:300], "payload": "",
                "latency_ms": int((time.time() - t0) * 1000)}
    envelope = json.dumps(res, ensure_ascii=False)
    # MCP wraps the actual data in result.content[].text as a JSON *string*;
    # searching the envelope alone would never find the series.
    inner = pl.mcp_content_text(envelope)
    return {"ok": "error" not in res,
            "error": (res.get("error") or {}).get("message", "")[:300] if "error" in res else "",
            "payload": inner or envelope, "envelope": envelope,
            "latency_ms": int((time.time() - t0) * 1000)}


def fetch_trend(keywords: list[str], market: str) -> dict:
    """Walk TREND_CHAIN per keyword; record every attempt; stop at first success."""
    results = []
    for kw in keywords:
        attempts = []
        landed = None
        for source, tier, tool, build_args in TREND_CHAIN:
            args = build_args(kw, market)
            r = _call_tool(source, tool, args)
            text = r.get("payload") or ""
            quota = pl.looks_like_quota_error(text)
            raw_path = RUN_DIR / "raw" / f"trend_{_slug(kw)}_{source}.json"
            raw_path.parent.mkdir(parents=True, exist_ok=True)
            raw_path.write_text(r.get("envelope") or text, encoding="utf-8")
            attempt = {"source": source, "tier": tier, "tool": tool,
                       "request": args, "status": "error", "latency_ms": r.get("latency_ms", 0),
                       "error": "", "raw_excerpt": text[:1200], "raw_path": str(raw_path.relative_to(HERE))}
            if quota:
                attempt.update(status="quota_exhausted", error="配额耗尽（响应体是提示语而非数据）")
                attempts.append(attempt)
                continue
            if not r.get("ok"):
                attempt.update(error=r.get("error") or "tool error")
                attempts.append(attempt)
                continue
            try:
                body = json.loads(text)
            except json.JSONDecodeError:
                attempt.update(error="响应不是 JSON")
                attempts.append(attempt)
                continue
            series = extract_series(body)
            if not series:
                attempt.update(error="解析不出时间序列（空序列或 schema 不匹配）")
                attempts.append(attempt)
                continue
            attempt.update(status="ok", series_points=len(series))
            attempts.append(attempt)
            landed = {"tier": tier, "source": source, "tool": tool, "series": series,
                      "summary": summarize(kw, series, tier)}
            break
        if not landed:
            attempts.append({"source": "web/browser", "tier": "web", "tool": "人工粘贴",
                             "status": "manual", "error": "一级/二级都拿不到，需要人工从公开页粘贴，或走浏览器操作",
                             "latency_ms": 0, "raw_excerpt": ""})
        results.append({"keyword": kw, "landed": landed, "attempts": attempts})
    cache = load_cache()
    for r in results:
        if r["landed"]:
            cache[r["keyword"]] = {"fetched_at": now(), "market": market, **r["landed"]}
    _save(CACHE_PATH, cache)
    out = {"market": market, "fetched_at": now(), "chain": [c[1] + ":" + c[0] for c in TREND_CHAIN],
           "results": results}
    # Keep the whole run (attempts + chain + raw excerpts) so the UI can replay it
    # after a reload instead of showing an empty panel.
    _save(LAST_TREND_PATH, out)
    return out


def parse_manual(keyword: str, raw: str, market: str, tier: str = "web") -> dict:
    """Accept a pasted series so the manual tier is a first-class citizen, not a dead end."""
    rows: list[tuple[str, float]] = []
    text = raw.strip()
    if text.startswith(("{", "[")):
        try:
            rows = extract_series(json.loads(text))
        except json.JSONDecodeError:
            rows = []
    if not rows:
        for line in text.splitlines():
            parts = [p.strip() for p in line.replace("\t", ",").split(",")]
            if len(parts) >= 2:
                try:
                    v = float(parts[1])
                except ValueError:
                    continue
                p = parts[0][:7]
                if len(p) == 7 and p[4] == "-":
                    rows.append((p, v))
    if not rows:
        return {"ok": False, "error": "解析不出 (period, value) 对。支持 2023-01,42 每行一条，或粘贴 JSON。"}
    rows.sort()
    cache = load_cache()
    cache[keyword] = {"fetched_at": now(), "market": market, "tier": tier,
                      "source": "manual-paste", "tool": "paste", "series": rows,
                      "summary": summarize(keyword, rows, tier)}
    _save(CACHE_PATH, cache)
    return {"ok": True, "keyword": keyword, "series_points": len(rows)}


# --------------------------------------------------------------------------- bootstrap
def bootstrap() -> dict:
    cfg = load_config()
    decisions = load_decisions()
    stages = []
    for s in pl.STAGES:
        sid = s["id"]
        hp = HUMAN_POINTS.get(sid, {"kind": "none", "question": "", "why": "", "options": []})
        stages.append({
            "id": sid,
            "name": s["name"],
            "goal": s["goal"],
            "inputs": [{"path": p, "columns": cols} for p, cols in s["data"]],
            "analysis": [p for p, _ in s["analysis"]],
            "fallback": s["fallback"],
            "human": hp,
            "decision": decisions.get(sid),
        })
    return {
        "stages": stages,
        "sources": _load(SOURCES_PATH, {}),
        "cache": load_cache(),
        "last_trend": _load(LAST_TREND_PATH, None),
        "gate_defaults": cfg.get("gate_defaults") or {},
        "fallback_chain": cfg.get("fallback_chain") or ["mcp", "apify", "web", "browser"],
        "generated_at": now(),
    }


def record_decision(payload: dict) -> dict:
    sid = payload.get("stage")
    action = payload.get("action")
    note = (payload.get("note") or "").strip()
    if not (sid in pl.STAGE_BY_ID and action):
        return {"ok": False, "error": "stage and action are required"}
    # A threshold override without a written reason is exactly the anti-pattern the
    # pipeline guards against - refuse it here too.
    if action in ("override_threshold", "reclassify") and len(note) < 4:
        return {"ok": False, "error": "调阈值 / 改判定必须写明理由（至少 4 个字）"}
    decisions = load_decisions()
    prev = decisions.get(sid)
    decisions[sid] = {"action": action, "target": payload.get("target") or "",
                      "payload": payload.get("value"), "note": note,
                      "decided_at": now(), "revision": (prev or {}).get("revision", 0) + 1}
    _save(DECISIONS_PATH, decisions)
    return {"ok": True, "decision": decisions[sid]}


# --------------------------------------------------------------------------- http
class Handler(BaseHTTPRequestHandler):
    server_version = "product-selection-mvp"

    def log_message(self, fmt, *args):  # quieter, single line
        sys.stderr.write("  %s %s\n" % (self.address_string(), fmt % args))

    def _send(self, obj, code=200):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _html(self, text: str):
        body = text.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path = urlparse(self.path).path
        if path in ("/", "/index.html"):
            f = HERE / "app.html"
            if not f.is_file():
                return self._send({"error": "app.html missing"}, 500)
            return self._html(f.read_text(encoding="utf-8"))
        if path == "/api/bootstrap":
            return self._send(bootstrap())
        if path == "/api/health":
            return self._send({"ok": True, "skill_root": str(SKILL_ROOT), "generated_at": now()})
        return self._send({"error": "not found", "path": path}, 404)

    def do_POST(self):
        path = urlparse(self.path).path
        try:
            n = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(n) or b"{}")
        except (ValueError, json.JSONDecodeError) as e:
            return self._send({"ok": False, "error": f"bad body: {e}"}, 400)
        try:
            if path == "/api/sources/probe":
                return self._send({"ok": True, **probe_sources()})
            if path == "/api/trend/fetch":
                kws = [k.strip() for k in (body.get("keywords") or []) if k and k.strip()]
                if not kws:
                    return self._send({"ok": False, "error": "keywords is empty"}, 400)
                return self._send({"ok": True, **fetch_trend(kws, body.get("market") or "US")})
            if path == "/api/trend/manual":
                r = parse_manual(body.get("keyword") or "", body.get("raw") or "", body.get("market") or "US")
                return self._send(r, 200 if r.get("ok") else 400)
            if path == "/api/decision":
                r = record_decision(body)
                return self._send(r, 200 if r.get("ok") else 400)
            if path == "/api/decision/clear":
                d = load_decisions()
                d.pop(body.get("stage"), None)
                _save(DECISIONS_PATH, d)
                return self._send({"ok": True})
        except Exception:  # noqa: BLE001 - surface the traceback to the UI
            return self._send({"ok": False, "error": traceback.format_exc()[-800:]}, 500)
        return self._send({"error": "not found", "path": path}, 404)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--port", type=int, default=8787)
    ap.add_argument("--host", default="127.0.0.1")
    args = ap.parse_args()
    RUN_DIR.mkdir(parents=True, exist_ok=True)
    try:
        srv = ThreadingHTTPServer((args.host, args.port), Handler)
    except OSError as e:
        print(f"cannot bind {args.host}:{args.port} ({e}). "
              f"Something else is already listening there - pass --port <other>.", file=sys.stderr)
        return 2
    print(f"product-selection MVP  ->  http://{args.host}:{args.port}")
    print(f"skill root: {SKILL_ROOT}")
    print("Ctrl-C to stop.")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

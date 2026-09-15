#!/usr/bin/env python3
"""Generic MCP streamable-HTTP client + TikHub REST helper.

Why this exists: the skill must keep working even when the harness does not have
the MCP servers registered. Everything the skill needs is reachable from the shell.

Usage
-----
  python3 mcp_call.py check                       # connectivity for every source
  python3 mcp_call.py check --source tikhub
  python3 mcp_call.py list  --source sellersprite
  python3 mcp_call.py call  --source sellersprite --tool google_trend --args '{"keyword":"pilates socks"}'
  python3 mcp_call.py rest  --source tikhub --path /api/v1/tiktok/web/fetch_search_video --params '{"keyword":"pilates"}'

Exit codes: 0 ok, 2 config error, 3 connectivity error, 4 tool error.
"""
from __future__ import annotations

import argparse
import json
import os
import random
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
SKILL_ROOT = HERE.parent
BROWSER_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)
DEFAULT_PROTOCOL = "2025-06-18"
MAX_RETRIES = 3


# --------------------------------------------------------------------------- config
def load_config(explicit: str | None = None) -> dict:
    candidates = []
    if explicit:
        candidates.append(Path(explicit))
    if os.environ.get("SKS_CONFIG"):
        candidates.append(Path(os.environ["SKS_CONFIG"]))
    candidates += [
        SKILL_ROOT / "config.local.json",
        Path.cwd() / "config.local.json",
        Path.home() / ".config" / "social-keyword-selection" / "config.local.json",
    ]
    for p in candidates:
        if p.is_file():
            return json.loads(p.read_text(encoding="utf-8"))
    raise SystemExit(
        "config.local.json not found. Looked in: "
        + ", ".join(str(c) for c in candidates)
    )


def get_source(cfg: dict, name: str) -> dict:
    src = (cfg.get("sources") or {}).get(name)
    if not src:
        raise SystemExit(f"unknown source '{name}'. known: {list((cfg.get('sources') or {}).keys())}")
    return src


# ------------------------------------------------------------------------- http
def _headers(src: dict, extra: dict | None = None) -> dict:
    h = {
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
    }
    if src.get("requires_browser_ua"):
        h["User-Agent"] = BROWSER_UA
    key = src.get("api_key")
    if key and src.get("auth_header"):
        h[src["auth_header"]] = (src.get("auth_prefix") or "") + key
    if extra:
        h.update(extra)
    return h


def _http(method: str, url: str, headers: dict, body: bytes | None = None, timeout: int = 60):
    req = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.read().decode("utf-8", "replace"), dict(r.headers), r.status
    except urllib.error.HTTPError as e:
        return e.read().decode("utf-8", "replace"), dict(e.headers), e.code
    except Exception as e:  # noqa: BLE001 - network layer, report verbatim
        return f"__NETWORK__{type(e).__name__}: {e}", {}, 0


def _post_json(url: str, headers: dict, payload: dict, timeout: int = 60):
    return _http("POST", url, headers, json.dumps(payload).encode(), timeout)


def parse_messages(body: str) -> list[dict]:
    """MCP replies are either raw JSON or SSE frames (`data: {...}`). Handle both."""
    out: list[dict] = []
    for raw in body.splitlines():
        line = raw.strip()
        if line.startswith("data:"):
            line = line[5:].strip()
        if not line or line.startswith(("event:", "id:", "retry:", ":")):
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    if not out:
        try:
            out.append(json.loads(body))
        except json.JSONDecodeError:
            pass
    return out


class McpSession:
    """One MCP session: initialize -> notifications/initialized -> tools/*."""

    def __init__(self, name: str, src: dict):
        self.name = name
        self.src = src
        self.url = src.get("mcp_url")
        if not self.url:
            raise SystemExit(f"source '{name}' has no mcp_url")
        self.session_id: str | None = None
        self.extra: dict = {}

    def _send(self, payload: dict, timeout: int = 60):
        hdrs = _headers(self.src, self.extra)
        body, resp_h, code = _post_json(self.url, hdrs, payload, timeout)
        sid = resp_h.get("Mcp-Session-Id") or resp_h.get("mcp-session-id")
        if sid:
            self.session_id = sid
            self.extra["Mcp-Session-Id"] = sid
        return body, code

    def initialize(self) -> dict:
        payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": DEFAULT_PROTOCOL,
                "capabilities": {},
                "clientInfo": {"name": "aura-social-keyword-selection", "version": "1.0"},
            },
        }
        last = None
        for attempt in range(MAX_RETRIES):
            body, code = self._send(payload)
            if code == 200:
                msgs = parse_messages(body)
                if msgs and "result" in msgs[0]:
                    self._send({"jsonrpc": "2.0", "method": "notifications/initialized"})
                    return msgs[0]["result"]
                last = body
            elif code in (429, 500, 502, 503, 504) or code == 0:
                last = body
                time.sleep(min(2 ** attempt + random.random(), 8))
                continue
            else:
                raise SystemExit(f"[{self.name}] initialize failed HTTP {code}: {body[:400]}")
        raise SystemExit(f"[{self.name}] initialize failed after retries: {str(last)[:400]}")

    def call(self, method: str, params: dict | None = None, req_id: int = 2) -> dict:
        payload = {"jsonrpc": "2.0", "id": req_id, "method": method, "params": params or {}}
        last = None
        for attempt in range(MAX_RETRIES):
            body, code = self._send(payload, timeout=120)
            if code == 200:
                for m in parse_messages(body):
                    if m.get("id") == req_id or "result" in m or "error" in m:
                        return m
                last = body
            elif code in (429, 500, 502, 503, 504) or code == 0:
                last = body
                time.sleep(min(2 ** attempt + random.random(), 8))
                continue
            else:
                return {"error": {"code": code, "message": body[:400]}, "http_status": code}
        return {"error": {"code": "retries_exhausted", "message": str(last)[:400]}}


# --------------------------------------------------------------------------- commands
def cmd_check(cfg: dict, args) -> int:
    names = [args.source] if args.source else list((cfg.get("sources") or {}).keys())
    results = {
        "checked_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "sources": {},
    }
    worst = 0
    for name in names:
        src = get_source(cfg, name)
        kind = src.get("kind")
        entry: dict = {"tier": src.get("tier"), "kind": kind, "label": src.get("label")}
        if kind in ("mcp", "mcp+rest"):
            try:
                s = McpSession(name, src)
                info = s.initialize()
                tools = s.call("tools/list", {"cursor": None}, req_id=2)
                tl = (tools.get("result") or {}).get("tools") or []
                entry.update(
                    status="ok",
                    server=(info.get("serverInfo") or {}).get("name"),
                    version=(info.get("serverInfo") or {}).get("version"),
                    tool_count=len(tl),
                    tools=[t["name"] for t in tl],
                )
            except SystemExit as e:
                entry.update(status="error", error=str(e)[:300])
                worst = max(worst, 3)
        elif kind == "cli":
            import shutil

            found = shutil.which(src.get("command") or "")
            env_ok = all(os.environ.get(k) for k in (src.get("env") or []))
            entry.update(
                status="ok" if (found and env_ok) else "degraded",
                binary=found,
                env_missing=[k for k in (src.get("env") or []) if not os.environ.get(k)],
            )
        else:
            entry.update(status="ok", note="agent tool, no network probe")
        results["sources"][name] = entry
        flag = {"ok": "OK", "degraded": "DEGRADED", "error": "ERROR"}[entry["status"]]
        print(f"[{flag:8}] {name:14} {entry.get('label','')}"
              + (f" tools={entry.get('tool_count')}" if entry.get("tool_count") else "")
              + (f" error={entry.get('error')}" if entry.get("error") else ""))
    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\nwrote {args.out}")
    return worst


def cmd_list(cfg: dict, args) -> int:
    src = get_source(cfg, args.source)
    s = McpSession(args.source, src)
    s.initialize()
    res = s.call("tools/list", {"cursor": None})
    tools = (res.get("result") or {}).get("tools") or []
    if args.json:
        print(json.dumps(tools, ensure_ascii=False, indent=2))
    else:
        print(f"{len(tools)} tools on {args.source}:")
        for t in tools:
            print(f"  - {t['name']}: {(t.get('description') or '')[:120]}")
    return 0


def cmd_call(cfg: dict, args) -> int:
    src = get_source(cfg, args.source)
    try:
        arguments = json.loads(args.args) if args.args else {}
    except json.JSONDecodeError as e:
        raise SystemExit(f"--args is not valid JSON: {e}") from e
    s = McpSession(args.source, src)
    s.initialize()
    res = s.call("tools/call", {"name": args.tool, "arguments": arguments})
    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(json.dumps(res, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"wrote {args.out}")
    else:
        print(json.dumps(res, ensure_ascii=False, indent=2))
    return 0 if "error" not in res else 4


def cmd_rest(cfg: dict, args) -> int:
    """TikHub REST escape hatch - the full API surface when MCP is too narrow."""
    src = get_source(cfg, args.source)
    base = (src.get("rest_base") or "").rstrip("/")
    if not base:
        raise SystemExit(f"source '{args.source}' has no rest_base")
    url = base + args.path
    if args.params:
        url += "?" + urllib.parse.urlencode(json.loads(args.params))
    hdrs = {
        "Accept": "application/json",
        "User-Agent": BROWSER_UA,
    }
    key = src.get("api_key")
    if key:
        hdrs[src.get("rest_auth_header") or "Authorization"] = (src.get("rest_auth_prefix") or "") + key
    body, _, code = _http("GET", url, hdrs, None, timeout=120)
    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(body, encoding="utf-8")
        print(f"wrote {args.out} (HTTP {code})")
    else:
        print(body[:6000])
    return 0 if code == 200 else 3


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", help="path to config.local.json")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("check", help="probe every source for connectivity")
    p.add_argument("--source")
    p.add_argument("--out", help="write capability matrix JSON here")

    p = sub.add_parser("list", help="list MCP tools on a source")
    p.add_argument("--source", required=True)
    p.add_argument("--json", action="store_true")

    p = sub.add_parser("call", help="call one MCP tool")
    p.add_argument("--source", required=True)
    p.add_argument("--tool", required=True)
    p.add_argument("--args", default="{}")
    p.add_argument("--out")

    p = sub.add_parser("rest", help="raw GET against a source's REST base (TikHub)")
    p.add_argument("--source", required=True)
    p.add_argument("--path", required=True)
    p.add_argument("--params", default=None)
    p.add_argument("--out")

    args = ap.parse_args()
    cfg = load_config(args.config)
    return {"check": cmd_check, "list": cmd_list, "call": cmd_call, "rest": cmd_rest}[args.cmd](cfg, args)


if __name__ == "__main__":
    sys.exit(main())

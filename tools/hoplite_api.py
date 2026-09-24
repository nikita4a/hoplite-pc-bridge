#!/usr/bin/env python3
"""hoplite_api.py — minimal stdlib-only CLI for the Hoplite REST API.

Covers everything the hoplite-pc-bridge / hoplite-gateway stack actually uses:
projects, threads (create/poll/stop), messages, model-providers, MCP server
CRUD + probe, plus a `raw` escape hatch for any endpoint.

Auth: X-Api-Key header (hop_...) read from a config.json — NEVER hardcoded.
NOTE (observed 2026-09): POST /api/threads/{id}/messages REJECTS the API key
(401 invalid_api_key). Appending to an existing thread needs browser session
cookies. Thread CREATE works with the API key alone. See docs/api-cookbook.md.

Usage:
    python hoplite_api.py --help
    python hoplite_api.py projects
    python hoplite_api.py threads --limit 3
    python hoplite_api.py ask --prompt "Reply with exactly: PROBE-OK" --wait 300

Config (default C:/Users/User/tmp/hoplite-gateway/config.json):
    api_key   — hop_... key            (required)
    api_base  — default https://api.hoplite.sh
    cookies   — optional session dict for `append` (never printed)
"""
from __future__ import annotations

import argparse
import json
import ssl
import sys
import time
import urllib.request as urlreq
from urllib.error import HTTPError, URLError
from http.cookiejar import Cookie, CookieJar
from pathlib import Path
from urllib.request import HTTPCookieProcessor, build_opener

DEFAULT_CONFIG = "C:/Users/User/tmp/hoplite-gateway/config.json"
ORIGIN = "https://app.hoplite.sh"
USER_AGENT = "hoplite-api-cli/1.0"
TERMINAL_THREAD_STATUS = {"ready", "failed", "archived"}

# ponytail: single process, no retry loop; Hoplite 429s surface as errors
HTTP_TIMEOUT = 60


def die(msg: str, code: int = 1) -> None:
    print(f"ERROR: {msg}", file=sys.stderr)
    sys.exit(code)


def load_config(path: str) -> dict:
    p = Path(path)
    if not p.is_file():
        die(f"config not found: {path} (pass --config with a config.json containing api_key)")
    try:
        cfg = json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        die(f"config {path} is not valid JSON: {e}")
    if not isinstance(cfg.get("api_key"), str) or not cfg["api_key"]:
        die(f"config {path} has no api_key — create one in the Hoplite UI")
    return cfg


class HopliteClient:
    """One config, two openers: key-auth (default) and cookie-auth (append)."""

    def __init__(self, cfg: dict):
        self.base = cfg.get("api_base", "https://api.hoplite.sh").rstrip("/")
        self.key = cfg["api_key"]
        self.cookies = cfg.get("cookies") or {}
        self._ctx = ssl.create_default_context()

    def _headers(self, use_key: bool) -> dict:
        h = {
            "Origin": ORIGIN,
            "Referer": ORIGIN + "/",
            "Accept": "application/json",
            "User-Agent": USER_AGENT,
        }
        if use_key:
            h["X-Api-Key"] = self.key
        return h

    def _cookie_opener(self):
        jar = CookieJar()
        for name, value in self.cookies.items():
            c = Cookie(
                version=0, name=name, value=str(value), port=None, port_specified=False,
                domain="app.hoplite.sh", domain_specified=True, domain_initial_dot=False,
                path="/", path_specified=True, secure=True, expires=None,
                discard=False, comment=None, comment_url=None, rest={}, rfc2109=False)
            jar.set_cookie(c)
        return build_opener(HTTPCookieProcessor(jar))

    def call(self, method: str, path: str, body: dict | None = None,
             use_key: bool = True, with_cookies: bool = False) -> tuple[int, str]:
        url = self.base + path
        data = json.dumps(body).encode() if body is not None else None
        headers = self._headers(use_key)
        if data is not None:
            headers["Content-Type"] = "application/json"
        opener = self._cookie_opener() if with_cookies else build_opener()
        req = urlreq.Request(url, data=data, method=method, headers=headers)
        try:
            with opener.open(req, timeout=HTTP_TIMEOUT) as r:
                return r.status, r.read().decode("utf-8", "replace")
        except HTTPError as e:
            return e.code, e.read().decode("utf-8", "replace")
        except URLError as e:
            die(f"cannot reach {self.base}: {e.reason}")

    def json_call(self, method: str, path: str, body: dict | None = None,
                  use_key: bool = True, with_cookies: bool = False) -> dict:
        status, text = self.call(method, path, body, use_key, with_cookies)
        try:
            data = json.loads(text) if text.strip() else {}
        except json.JSONDecodeError:
            die(f"{method} {path} -> {status}: non-JSON body: {text[:300]}")
        if status >= 400:
            die(f"{method} {path} -> {status}: {json.dumps(data, ensure_ascii=False)[:500]}")
        return data


def out(data, limit: int = 0) -> None:
    text = json.dumps(data, indent=2, ensure_ascii=False)
    if limit and len(text) > limit:
        text = text[:limit] + f"\n... ({len(text)} chars total, truncated by --limit)"
    print(text)


def parse_header_args(pairs: list[str] | None) -> dict | None:
    if not pairs:
        return None
    headers = {}
    for pair in pairs:
        if ":" not in pair:
            die(f"bad --header {pair!r}: expected 'Name: Value'")
        k, v = pair.split(":", 1)
        headers[k.strip()] = v.strip()
    return headers


def get_first_project_id(client: HopliteClient, override: str | None) -> str:
    if override:
        return override
    data = client.json_call("GET", "/api/projects")
    projects = data.get("projects") if isinstance(data, dict) else data
    if not projects:
        die("no projects on this account — create one at https://app.hoplite.sh first")
    pid = projects[0].get("id") or projects[0].get("projectId")
    if not pid:
        die(f"cannot find project id in first project: {json.dumps(projects[0])[:300]}")
    return pid


# ── subcommand implementations ─────────────────────────────────────────────

def cmd_session(client, args) -> None:
    out(client.json_call("GET", "/api/auth/session"))


def cmd_projects(client, args) -> None:
    out(client.json_call("GET", "/api/projects"))


def cmd_threads(client, args) -> None:
    out(client.json_call("GET", f"/api/threads?limit={args.limit}"))


def cmd_thread(client, args) -> None:
    out(client.json_call("GET", f"/api/threads/{args.id}"))


def cmd_messages(client, args) -> None:
    data = client.json_call("GET", f"/api/threads/{args.id}/messages")
    msgs = data.get("messages") if isinstance(data, dict) else data
    if msgs is None:
        msgs = data.get("data", [])
    if args.last:
        msgs = msgs[-args.last:]
    if isinstance(data, dict) and msgs is not data:
        out({"count": len(msgs), "messages": msgs})
    else:
        out(msgs)


def cmd_models(client, args) -> None:
    out(client.json_call("GET", "/api/model-providers"))


def cmd_mcp_servers(client, args) -> None:
    out(client.json_call("GET", "/api/mcp/servers"))


def cmd_mcp_catalog(client, args) -> None:
    out(client.json_call("GET", "/api/mcp/catalog"), limit=args.limit or 4000)


def cmd_mcp_create(client, args) -> None:
    if not args.url.startswith("https://"):
        die("url must be https and publicly reachable (Hoplite rejects loopback/private hosts)")
    config = {
        "transport": args.transport,
        "url": args.url,
        "description": args.description or f"{args.name} (registered via hoplite_api.py)",
    }
    headers = parse_header_args(args.header)
    if headers:
        config["headers"] = headers
    if args.needs_auth:
        config["needsAuth"] = True
    body = {"name": args.name, "enabled": not args.disabled, "config": config}
    out(client.json_call("POST", "/api/mcp/servers", body))


def cmd_mcp_patch(client, args) -> None:
    body: dict = {}
    if args.name:
        body["name"] = args.name
    if args.enabled is not None:
        body["enabled"] = args.enabled
    if args.url:
        body["config"] = {"transport": args.transport, "url": args.url}
    if not body:
        die("nothing to patch: pass --name/--enabled/--url")
    out(client.json_call("PATCH", f"/api/mcp/servers/{args.id}", body))


def cmd_mcp_delete(client, args) -> None:
    out(client.json_call("DELETE", f"/api/mcp/servers/{args.id}"))


def cmd_mcp_probe(client, args) -> None:
    if not args.url.startswith("https://"):
        die("probe url must be https (loopback/private is rejected server-side)")
    body: dict = {"url": args.url, "transport": args.transport}
    headers = parse_header_args(args.header)
    if headers:
        body["headers"] = headers
    out(client.json_call("POST", "/api/mcp/probe", body))


def cmd_ask(client, args) -> None:
    """Create a thread with an initial prompt and poll until the agent finishes."""
    pid = get_first_project_id(client, args.project)
    body: dict = {"projectId": pid, "prompt": args.prompt}
    if args.title:
        body["title"] = args.title
    if args.model:
        body["model"] = args.model
    if args.reasoning:
        body["reasoning"] = {"mode": args.reasoning}
    if args.speed:
        body["speed"] = args.speed
    created = client.json_call("POST", "/api/threads", body)
    thread = created.get("thread", created)
    tid = thread.get("id")
    if not tid:
        die(f"no thread id in create response: {json.dumps(created)[:300]}")
    print(f"thread: {tid}  status: {thread.get('status', '?')}", file=sys.stderr)
    if not args.wait:
        out(thread)
        return
    deadline = time.time() + args.wait
    status = (thread.get("status") or "").lower()
    while status not in TERMINAL_THREAD_STATUS:
        if time.time() > deadline:
            die(f"thread {tid} not terminal after {args.wait}s (last status: {status}); "
                f"it may still complete — check https://app.hoplite.sh", 2)
        time.sleep(args.poll)
        data = client.json_call("GET", f"/api/threads/{tid}")
        t = data.get("thread", data)
        status = (t.get("status") or "").lower()
        print(f"  status={status}", file=sys.stderr)
    msgs_data = client.json_call("GET", f"/api/threads/{tid}/messages")
    msgs = msgs_data.get("messages", msgs_data) if isinstance(msgs_data, dict) else msgs_data
    assistant = [m for m in msgs if m.get("role") == "assistant"]
    print(f"final status: {status}", file=sys.stderr)
    out({
        "thread_id": tid,
        "status": status,
        "assistant_messages": assistant[-args.last:],
        "total_messages": len(msgs),
    })


def cmd_append(client, args) -> None:
    """Follow-up message — needs session cookies, NOT the API key."""
    if not client.cookies:
        die("config.json has no `cookies` — POST /messages rejects X-Api-Key (401 invalid_api_key). "
            "Export browser cookies first (see hoplite-gateway GUIDE.md)")
    status, text = client.call("POST", f"/api/threads/{args.id}/messages",
                               {"content": args.text}, use_key=False, with_cookies=True)
    print(f"HTTP {status}")
    try:
        out(json.loads(text))
    except (json.JSONDecodeError, ValueError):
        print(text)


def cmd_stop(client, args) -> None:
    out(client.json_call("POST", f"/api/threads/{args.id}/stop", {}))


def cmd_raw(client, args) -> None:
    body = json.loads(args.body) if args.body else None
    status, text = client.call(args.method.upper(), args.path, body)
    print(f"HTTP {status}")
    try:
        out(json.loads(text))
    except (json.JSONDecodeError, ValueError):
        print(text)


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="hoplite_api.py",
        description="Minimal stdlib CLI for the Hoplite REST API (api.hoplite.sh).",
        epilog="All commands print pretty JSON and exit non-zero on HTTP >= 400.")
    ap.add_argument("--config", default=DEFAULT_CONFIG,
                    help=f"path to config.json with api_key (default: {DEFAULT_CONFIG})")
    sub = ap.add_subparsers(dest="cmd", required=True)

    def add(name: str, fn, help_: str):
        s = sub.add_parser(name, help=help_)
        s.set_defaults(fn=fn)
        return s

    add("session", cmd_session, "GET /api/auth/session — who am I")
    add("projects", cmd_projects, "GET /api/projects — list projects")
    s = add("threads", cmd_threads, "GET /api/threads?limit=N — list threads")
    s.add_argument("--limit", type=int, default=10)
    s = add("thread", cmd_thread, "GET /api/threads/{id} — thread detail")
    s.add_argument("id")
    s = add("messages", cmd_messages, "GET /api/threads/{id}/messages — message list")
    s.add_argument("id")
    s.add_argument("--last", type=int, default=0, help="show only last N messages")
    add("models", cmd_models, "GET /api/model-providers — available models")
    add("mcp-servers", cmd_mcp_servers, "GET /api/mcp/servers — registered MCP servers")
    s = add("mcp-catalog", cmd_mcp_catalog, "GET /api/mcp/catalog — MCP catalog")
    s.add_argument("--limit", type=int, default=0, help="truncate output to N chars")
    s = add("mcp-create", cmd_mcp_create, "POST /api/mcp/servers — register an MCP server")
    s.add_argument("--name", required=True)
    s.add_argument("--url", required=True, help="https URL (public, not loopback)")
    s.add_argument("--transport", choices=["http", "sse"], default="http")
    s.add_argument("--description")
    s.add_argument("--header", action="append", help="'Name: Value', repeatable")
    s.add_argument("--needs-auth", action="store_true")
    s.add_argument("--disabled", action="store_true")
    s = add("mcp-patch", cmd_mcp_patch, "PATCH /api/mcp/servers/{id}")
    s.add_argument("id")
    s.add_argument("--name")
    s.add_argument("--enabled", type=lambda x: x.lower() == "true")
    s.add_argument("--url")
    s.add_argument("--transport", choices=["http", "sse"], default="http")
    s = add("mcp-delete", cmd_mcp_delete, "DELETE /api/mcp/servers/{id}")
    s.add_argument("id")
    s = add("mcp-probe", cmd_mcp_probe, "POST /api/mcp/probe — health-check an MCP endpoint")
    s.add_argument("--url", required=True)
    s.add_argument("--transport", choices=["http", "sse"], default="http")
    s.add_argument("--header", action="append", help="'Name: Value', repeatable")
    s = add("ask", cmd_ask, "create a thread with a prompt, poll until done, print answer")
    s.add_argument("--prompt", required=True)
    s.add_argument("--project", help="project id (default: first project)")
    s.add_argument("--title")
    s.add_argument("--model", help="e.g. claude-sonnet-4-6 (default: project default)")
    s.add_argument("--reasoning",
                   choices=["off", "none", "minimal", "low", "medium", "high", "xhigh", "max"])
    s.add_argument("--speed", choices=["standard", "fast"])
    s.add_argument("--wait", type=int, default=0, help="seconds to wait for the agent (0 = don't wait)")
    s.add_argument("--poll", type=float, default=3.0)
    s.add_argument("--last", type=int, default=2)
    s = add("append", cmd_append, "POST /api/threads/{id}/messages (needs session cookies)")
    s.add_argument("id")
    s.add_argument("--text", required=True)
    s = add("stop", cmd_stop, "POST /api/threads/{id}/stop")
    s.add_argument("id")
    s = add("raw", cmd_raw, "escape hatch: METHOD /api/path [--body JSON]")
    s.add_argument("method")
    s.add_argument("path", help="e.g. /api/threads?limit=1")
    s.add_argument("--body")
    return ap


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    args = build_parser().parse_args()
    cfg = load_config(args.config)
    client = HopliteClient(cfg)
    args.fn(client, args)


if __name__ == "__main__":
    main()

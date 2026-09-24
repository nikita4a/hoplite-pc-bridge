#!/usr/bin/env python3
"""One-shot "РАБОТАЕТ?" check for the whole Hoplite <-> PC stack.

Checks, in order:
  1. local bridge (mcp-proxy -> desktop-commander) answers an MCP handshake
  2. public ngrok URL answers the same handshake
  3. a real tool executes on this PC through the public URL (shell)
  4. the folder sandbox denies a path outside allowedDirectories
  5. Hoplite has the server registered (GET /api/mcp/servers)
  6. Hoplite's cloud can reach it (POST /api/mcp/probe -> connected/toolCount)
  7. the OpenAI gateway (:8787) is healthy and a turn completes
  8. omp + opencode configs still reference the stack

Exit code 0 = everything green.

Usage: python status_all.py
"""
from __future__ import annotations

import json
import pathlib
import sys
import time
import urllib.error
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parent
BRIDGE_CFG = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))
GW_CFG = json.loads((ROOT.parent / "hoplite-gateway" / "config.json").read_text(encoding="utf-8"))
API_KEY = GW_CFG["api_key"]
API_BASE = GW_CFG.get("api_base", "https://api.hoplite.sh")

sys.path.insert(0, str(ROOT))
import bridge  # noqa: E402

LOCAL_URL = f"http://127.0.0.1:{BRIDGE_CFG['port']}/{BRIDGE_CFG['secret_path']}"
PUBLIC_URL = (ROOT / "status.json")
PUBLIC: str = ((json.loads(PUBLIC_URL.read_text(encoding="utf-8")).get("mcp_url") or "")
               if PUBLIC_URL.exists() else "")
if not PUBLIC:
    sys.exit("no public URL in status.json — start the bridge first (start_bridge.bat)")

results: list[tuple[bool, str, str]] = []


def check(name: str, fn) -> None:
    t0 = time.time()
    try:
        ok, detail = fn()
    except Exception as e:                                    # noqa: BLE001 - report, don't crash
        ok, detail = False, f"{type(e).__name__}: {e}"
    results.append((ok, name, f"{detail}  [{time.time() - t0:.1f}s]"))
    print(f"{'PASS' if ok else 'FAIL'}  {name:<46} {detail}  [{time.time() - t0:.1f}s]")


def api(method: str, path: str, body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(API_BASE + path, data=data, method=method,
                                 headers={"X-Api-Key": API_KEY,
                                          "Origin": "https://app.hoplite.sh",
                                          "Accept": "application/json",
                                          "User-Agent": "hoplite-pc-bridge/status"})
    if data:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=90) as r:
            return r.status, json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        return e.code, e.read(300).decode("utf-8", "replace")


# ── 1-2: MCP handshake, local and public ────────────────────────────────────
def handshake(url: str):
    def run():
        ok, sid = bridge.verify(url, BRIDGE_CFG)
        return ok, ("session " + sid[:8]) if ok and sid else "no session"
    return run


# ── 3-4: real tool execution + sandbox boundary ─────────────────────────────
def tools_and_sandbox():
    ok, sid = bridge.verify(PUBLIC, BRIDGE_CFG)
    if not ok or not sid:
        return False, "handshake failed"

    def call(req_id, name, args, timeout=90):
        st, body, _ = bridge.mcp_call(PUBLIC, {
            "jsonrpc": "2.0", "id": req_id, "method": "tools/call",
            "params": {"name": name, "arguments": args}}, sid, None, timeout=timeout)
        if st != 200 or not isinstance(body, dict):
            return None, f"HTTP {st}"
        res = body.get("result") or {}
        text = "".join(p.get("text", "") for p in res.get("content", []) if isinstance(p, dict))
        return (not res.get("isError")), text

    marker = "STATUS-SHELL-OK"
    _, out = call(1, "start_process",
                  {"command": f"echo {marker}", "timeout_ms": 20000})
    shell_ok = marker in out
    denied, _ = call(2, "read_file", {"path": "C:/Windows/win.ini"})
    sandbox_ok = denied is False
    return shell_ok and sandbox_ok, (
        f"shell={'OK' if shell_ok else 'FAIL'} sandbox_denied={'OK' if sandbox_ok else 'FAIL'}")


# ── 5: registered in Hoplite ────────────────────────────────────────────────
def registered():
    st, body = api("GET", "/api/mcp/servers")
    if st != 200 or not isinstance(body, dict):
        return False, f"HTTP {st}: {str(body)[:120]}"
    servers = body.get("servers", [])
    ours = [s for s in servers if s.get("name") == "pc-tools"]
    if not ours:
        return False, f"pc-tools not in the list ({len(servers)} servers)"
    s = ours[0]
    return (s.get("enabled") is True and s.get("config", {}).get("url") == PUBLIC), \
        f"id={s.get('id')} enabled={s.get('enabled')} projectId={s.get('projectId')}"


# ── 6: Hoplite cloud can reach us ───────────────────────────────────────────
def cloud_probe():
    st, body = api("POST", "/api/mcp/probe", {"url": PUBLIC})
    if st != 200 or not isinstance(body, dict):
        return False, f"HTTP {st}: {str(body)[:120]}"
    p = body.get("probe", {})
    return bool(p.get("connected")) and (p.get("toolCount") or 0) > 0, \
        f"connected={p.get('connected')} tools={p.get('toolCount')} server={p.get('serverName')}"


# ── 7: OpenAI gateway healthy + one real turn ───────────────────────────────
def gateway():
    with urllib.request.urlopen("http://127.0.0.1:8787/health", timeout=10) as r:
        h = json.loads(r.read())
    if not h.get("ok") or not h.get("auth"):
        return False, f"health={h.get('ok')} auth={h.get('auth')}"
    payload = {"model": "hoplite-opus-5",
               "messages": [{"role": "user", "content": "Reply with exactly: GW-OK"}],
               "stream": False, "user": "status-all"}
    req = urllib.request.Request("http://127.0.0.1:8787/v1/chat/completions",
                                 data=json.dumps(payload).encode(),
                                 headers={"Content-Type": "application/json",
                                          "Authorization": "Bearer sk-local"})
    with urllib.request.urlopen(req, timeout=280) as r:
        out = json.loads(r.read())
    text = out["choices"][0]["message"]["content"]
    return "GW-OK" in text, f"turn -> {text[:40]!r} uptime={h.get('uptime_s')}s"


# ── 8: client configs still wired ───────────────────────────────────────────
def client_configs():
    omp = pathlib.Path.home() / ".omp/agent/mcp.json"
    oc = pathlib.Path.home() / ".config/opencode/opencode.jsonc"
    notes = []
    ok = True
    if omp.exists():
        d = json.loads(omp.read_text(encoding="utf-8"))
        has = "pc-tools" in d.get("mcpServers", {})
        ok &= has
        notes.append(f"omp pc-tools={'yes' if has else 'NO'}")
    else:
        ok = False
        notes.append("omp mcp.json missing")
    if oc.exists():
        txt = oc.read_text(encoding="utf-8")
        has = "hoplite/hoplite" in txt or '"hoplite"' in txt
        ok &= has
        notes.append(f"opencode hoplite={'yes' if has else 'NO'}")
    else:
        notes.append("opencode.jsonc missing")
    return ok, ", ".join(notes)


def main() -> int:
    print(f"local  : {LOCAL_URL}")
    print(f"public : {PUBLIC}\n")
    check("1. bridge local handshake", handshake(LOCAL_URL))
    check("2. bridge public handshake", handshake(PUBLIC))
    check("3+4. shell on this PC + sandbox denies outside", tools_and_sandbox)
    check("5. registered in Hoplite", registered)
    check("6. Hoplite cloud reaches us", cloud_probe)
    check("7. OpenAI gateway turn", gateway)
    check("8. omp + opencode configs", client_configs)

    passed = sum(1 for ok, _, _ in results if ok)
    print(f"\n{'=' * 72}\n{passed}/{len(results)} checks passed -> "
          f"{'РАБОТАЕТ' if passed == len(results) else 'ЕСТЬ ПРОБЛЕМЫ'}")
    for ok, name, detail in results:
        if not ok:
            print(f"  FAIL {name}: {detail}")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())

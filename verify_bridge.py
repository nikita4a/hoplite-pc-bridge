#!/usr/bin/env python3
"""Re-verify an already-running bridge without restarting it.

Reads status.json, then walks the MCP handshake (initialize -> tools/list)
over the PUBLIC ngrok URL and prints the exposed tool names.

Usage: python verify_bridge.py
Exit:  0 = the cloud agent can reach and list tools, 1 = it cannot.
"""
from __future__ import annotations

import json
import re
import time
from pathlib import Path

import bridge

STATUS = Path(__file__).resolve().parent / "status.json"
CONFIG = Path(__file__).resolve().parent / "config.json"


def prove_tools(url: str, cfg: dict, sid: str | None) -> bool:
    """Actually exercise tools over the public URL and probe the folder sandbox.

    tools/list only proves the tunnel is wired. This proves the cloud agent can
    act on this PC, and reports honestly whether allowedDirectories gates reads.
    Reuses the session bridge.verify() already opened: a second initialize against
    the same proxy hangs on an unclosed stream, so there is one handshake per run.
    """
    extra = {"X-API-Key": cfg["api_key"]} if cfg.get("api_key") else None

    def call_tool(req_id: int, name: str, arguments: dict) -> tuple[bool | None, str]:
        """True = the tool ran, False = the server refused it, None = transport failed.

        Conflating None with False once reported a dropped request as "sandbox is
        gone" — a false security alarm. One retry absorbs transient tunnel drops.
        """
        st_, b_ = None, None
        for attempt in (1, 2):
            st_, b_, _ = bridge.mcp_call(url, {
                "jsonrpc": "2.0", "id": req_id, "method": "tools/call",
                "params": {"name": name, "arguments": arguments}}, sid, extra, timeout=90)
            if st_ == 200 and isinstance(b_, dict):
                break
            if attempt == 1:
                time.sleep(2)
        if st_ != 200 or not isinstance(b_, dict):
            return None, f"HTTP {st_}: {str(b_)[:300]}"
        if "error" in b_:
            return None, str(b_["error"])[:300]
        res = b_.get("result") or {}
        text = "".join(p.get("text", "") for p in res.get("content", []) if isinstance(p, dict))
        return (not res.get("isError")), text

    allowed = cfg.get("desktop_commander", {}).get("allowedDirectories", [])
    ok = True
    if allowed:
        good, out = call_tool(2, "list_directory", {"path": allowed[0]})
        label = "OK" if good else ("TRANSPORT" if good is None else "REFUSED")
        print(f"[prove] list_directory {allowed[0]} -> {label} ({len(out)} chars of listing)")
        if not good:
            print(f"        {out[:300]}")
            ok = False
    good, out = call_tool(3, "get_config", {})
    if good:
        blob = out[out.find("{"):] if "{" in out else out
        try:
            server_cfg = json.loads(blob)
        except json.JSONDecodeError:
            server_cfg = {}
        server_dirs = server_cfg.get("allowedDirectories")
        print(f"[prove] get_config OK -> allowedDirectories={server_dirs} "
              f"blockedCommands={len(server_cfg.get('blockedCommands', []))} "
              f"telemetry={server_cfg.get('telemetryEnabled')}")
        if allowed and server_dirs is not None and set(server_dirs) != set(allowed):
            print("[prove] WARNING: the running server did not pick up allowedDirectories — "
                  "check ~/.claude-server-commander/config.json")
    else:
        print(f"[prove] get_config FAIL -> {out[:300]}")
    good, out = call_tool(4, "read_file", {"path": "C:/Windows/win.ini"})
    if good is False:
        print("[prove] boundary read_file C:/Windows/win.ini -> DENIED (folder sandbox holds)")
    elif good is True:
        print("[prove] boundary read_file C:/Windows/win.ini -> ALLOWED — allowedDirectories "
              "is NOT gating reads; the whole disk is shared")
        ok = False
    else:
        print(f"[prove] boundary read_file C:/Windows/win.ini -> INCONCLUSIVE (transport): {out[:140]}")
    marker = "BRIDGE-SHELL-OK"
    good, out = call_tool(5, "start_process",
                          {"command": f"echo {marker} && hostname", "timeout_ms": 20000})
    ran = marker in out
    print(f"[prove] start_process shell -> {'OK, executed on this PC' if ran else 'FAIL: ' + out[:200]}")
    if not ran:
        ok = False
    sess = re.search(r'"sessionId":\s*"([^"]+)"', out)
    if sess:                                     # best-effort cleanup of the spawned shell
        call_tool(6, "force_terminate", {"sessionId": sess.group(1)})
    return ok


def main() -> int:
    if not STATUS.exists():
        print("no status.json — the bridge is not running (start_bridge.bat)")
        return 1
    status = json.loads(STATUS.read_text(encoding="utf-8"))
    cfg = json.loads(CONFIG.read_text(encoding="utf-8"))
    url = status.get("mcp_url")
    if not url:
        print("status.json has no mcp_url")
        return 1
    print(f"bridge state={status.get('state')} pid={status.get('pid')} "
          f"proxy_pid={status.get('proxy_pid')} ngrok_pid={status.get('ngrok_pid')}")
    print(f"started_at={status.get('started_at')} port={status.get('port')}")
    ok, sid = bridge.verify(url, cfg)
    if ok:
        # sid is None in stateless mode (mcp-proxy --stateless) — tools/call works without it.
        ok = prove_tools(url, cfg, sid)
    print("VERDICT:", "OK — public MCP endpoint is usable" if ok
          else "FAIL — public MCP endpoint is not usable")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())

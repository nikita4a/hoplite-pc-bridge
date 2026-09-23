#!/usr/bin/env python3
"""hoplite-pc-bridge — expose local PC tools to the Hoplite cloud agent.

Chain:
    Hoplite cloud agent
      -> https://<static>.ngrok-free.dev/<secret path>     (public, capability URL)
      -> ngrok agent                                        (outbound-only tunnel)
      -> mcp-proxy  127.0.0.1:<port>  (streamable HTTP MCP) (loopback only)
      -> @wonderwhy-er/desktop-commander (stdio)            (shell + files, on this PC)

Run:
    python bridge.py            start proxy + tunnel, print the Hoplite dialog values
    python bridge.py --verify   same, then prove tools/list works over the PUBLIC url
    python bridge.py --stop     kill a bridge started by this script (via status.json)

Stdlib only. Windows-first, POSIX-tolerant.
"""
from __future__ import annotations

import argparse
import concurrent.futures
import json
import os
import re
import secrets
import shutil
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent
CONFIG_FILE = ROOT / "config.json"
STATUS_FILE = ROOT / "status.json"
DC_CONFIG = Path.home() / ".claude-server-commander" / "config.json"   # desktop-commander's REAL config
NGROK_API = "http://127.0.0.1:4040/api/tunnels"

# Keys bridge.py merges into DC_CONFIG. Only what is listed in config.json ->
# desktop_commander gets written; everything else in that shared file (clientId,
# usageStats, blockedCommands, limits) is preserved untouched.


# ── config ──────────────────────────────────────────────────────────────────
def load_config() -> dict:
    if not CONFIG_FILE.exists():
        sys.exit(f"no {CONFIG_FILE.name} — copy config.example.json to config.json and edit it")
    cfg = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
    cfg.setdefault("port", 8888)
    cfg.setdefault("secret_path", "")
    cfg.setdefault("ngrok", {})
    cfg.setdefault("desktop_commander", {})
    dc = cfg["desktop_commander"]
    dc.setdefault("defaultShell", "powershell.exe")
    dc.setdefault("telemetryEnabled", False)
    # blockedCommands is deliberately NOT defaulted: it lives in the shared
    # ~/.claude-server-commander/config.json and belongs to the user's other clients
    # too. Add it under config.json -> desktop_commander to impose one on this bridge.
    dc.setdefault("allowedDirectories", [])
    if not dc["allowedDirectories"]:
        # Empty allowedDirectories == whole filesystem for file ops. Refuse to default into that.
        sys.exit("config.json: desktop_commander.allowedDirectories is empty — that grants the "
                 "entire filesystem. List the folders you actually want to share.")
    cfg["ngrok_bin"] = cfg.get("ngrok_bin") or shutil.which("ngrok") or ""
    return cfg


def resolve(cfg: dict, key: str, subpath: str) -> str:
    """Absolute path to a node script, from config or the global npm tree."""
    explicit = cfg.get(key)
    if explicit:
        p = Path(explicit)
        if p.exists():
            return str(p)
    npm = Path(os.environ.get("APPDATA", "")) / "npm" / "node_modules" / subpath
    if npm.exists():
        return str(npm)
    sys.exit(f"cannot find {subpath} — set \"{key}\" in config.json")


def find_ngrok(cfg: dict) -> str:
    if cfg["ngrok_bin"] and Path(cfg["ngrok_bin"]).exists():
        return cfg["ngrok_bin"]
    for cand in Path(os.environ.get("LOCALAPPDATA", "")) .glob(
            "Microsoft/WinGet/Packages/Ngrok.Ngrok_*/ngrok.exe"):
        return str(cand)
    sys.exit("ngrok not found — winget install Ngrok.Ngrok, or set \"ngrok_bin\" in config.json")


# ── desktop-commander config ────────────────────────────────────────────────
def apply_dc_config(cfg: dict) -> dict:
    """Merge our keys into desktop-commander's real, SHARED config file.

    It lives at ~/.claude-server-commander/config.json — the package README's
    "config.json in the server's working directory" is wrong (dist/config.js pins
    CONFIG_FILE to the home dir). That file is shared with every other MCP client
    on this machine and carries clientId + usageStats, so it is merged, never
    overwritten, and backed up once before the first write. The server watches the
    directory, so the change hot-reloads.
    """
    DC_CONFIG.parent.mkdir(parents=True, exist_ok=True)
    current: dict = {}
    if DC_CONFIG.exists():
        raw = DC_CONFIG.read_text(encoding="utf-8")
        try:
            loaded = json.loads(raw)
            current = loaded if isinstance(loaded, dict) else {}
        except json.JSONDecodeError:
            current = {}
        if not list(DC_CONFIG.parent.glob("config.json.bak-*")):
            bak = DC_CONFIG.with_name(f"config.json.bak-{time.strftime('%Y%m%d-%H%M%S')}")
            bak.write_text(raw, encoding="utf-8")
    changed = {}
    for key, value in cfg["desktop_commander"].items():
        if current.get(key) != value:
            changed[key] = (current.get(key), value)
        current[key] = value
    if changed:
        DC_CONFIG.write_text(json.dumps(current, indent=2, ensure_ascii=False), encoding="utf-8")
    return changed


# ── processes ───────────────────────────────────────────────────────────────
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def start_proxy(cfg: dict) -> subprocess.Popen:
    node = shutil.which("node") or "node"
    proxy_js = resolve(cfg, "mcp_proxy_js", "mcp-proxy/dist/bin/mcp-proxy.mjs")
    dc_js = resolve(cfg, "desktop_commander_js", "@wonderwhy-er/desktop-commander/dist/index.js")
    secret = cfg["secret_path"]
    args = [node, proxy_js,
            "--host", "127.0.0.1",            # loopback only: ngrok is the sole ingress
            "--port", str(cfg["port"]),
            "--server", "stream",              # no /sse endpoint to leave unauthenticated
            "--streamEndpoint", f"/{secret}",  # capability URL: the path IS the credential
            "--corsAddAllowedHeader", "X-API-Key"]
    if cfg.get("api_key"):
        args += ["--apiKey", cfg["api_key"]]
    args += ["--", node, dc_js]
    log = (ROOT / "bridge.log").open("ab", buffering=0)
    return subprocess.Popen(args, cwd=str(ROOT), stdout=log, stderr=subprocess.STDOUT,
                            creationflags=NO_WINDOW)


def parse_ngrok_yml(path: Path) -> tuple[str, str]:
    """Pull (authtoken, domain) out of an ngrok agent config of ANY version.

    The dashboard now emits version:"3" (agent.authtoken + endpoints[].url) while
    older agents reject it ("unknown version '3'. valid versions are: [1 2]").
    So the yml is read as DATA and ngrok is always driven by CLI flags — one
    invocation shape that works on both, no YAML dependency, no version pinning.
    """
    if not path.exists():
        return "", ""
    text = path.read_text(encoding="utf-8", errors="replace")
    token = ""
    m = re.search(r"^\s*authtoken:\s*[\"']?([A-Za-z0-9_\-]+)", text, re.M)
    if m:
        token = m.group(1)
    domain = ""
    m = re.search(r"^\s*url:\s*[\"']?https://([^/\"'\s]+)", text, re.M)
    if not m:
        m = re.search(r"^\s*(?:domain|hostname):\s*[\"']?([^/\"'\s]+)", text, re.M)
    if m:
        domain = m.group(1)
    return token, domain


def start_ngrok(cfg: dict) -> subprocess.Popen:
    ngrok = find_ngrok(cfg)
    ng = cfg["ngrok"]
    token = ng.get("authtoken") or ""
    domain = ng.get("domain") or ""
    cfg_path = ng.get("config_path") or ""
    if cfg_path and (not token or not domain):
        yml_token, yml_domain = parse_ngrok_yml(Path(cfg_path))
        token, domain = token or yml_token, domain or yml_domain
    token = token or os.environ.get("NGROK_AUTHTOKEN", "")
    args = [ngrok, "http", str(cfg["port"]), "--log", "stdout", "--log-format", "json"]
    if domain:
        args += ["--domain", domain]
    if token:
        args += ["--authtoken", token]
    log = (ROOT / "ngrok.log").open("ab", buffering=0)
    return subprocess.Popen(args, stdout=log, stderr=subprocess.STDOUT, creationflags=NO_WINDOW)


def public_url(cfg: dict, deadline_s: int = 45) -> str:
    """Tunnel URL: explicit override -> ngrok local API -> ngrok log lines."""
    url = (cfg["ngrok"].get("public_url") or "").rstrip("/")
    if url:
        return url
    log_path = ROOT / "ngrok.log"
    end = time.time() + deadline_s
    while time.time() < end:
        try:
            with urllib.request.urlopen(NGROK_API, timeout=5) as r:
                tunnels = json.loads(r.read().decode()).get("tunnels", [])
            for t in tunnels:
                if t.get("proto") == "https" and t.get("public_url"):
                    return t["public_url"].rstrip("/")
        except (urllib.error.URLError, OSError, json.JSONDecodeError):
            pass
        if log_path.exists():
            found = re.findall(r'"url":"(https://[^"]+)"',
                               log_path.read_text(encoding="utf-8", errors="replace"))
            if found:
                return found[-1].rstrip("/")
        time.sleep(1)
    return ""


def port_busy(port: int) -> bool:
    import socket
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.4)
        return s.connect_ex(("127.0.0.1", port)) == 0


# ── MCP client (enough of the streamable-HTTP handshake to prove it works) ──
def mcp_call(url: str, payload: dict, session: str | None = None,
             headers_extra: dict | None = None, timeout: int = 40):
    """One JSON-RPC POST. Returns (status, parsed_body_or_text, session_id).

    A wall-clock deadline wraps the request: urlopen's timeout is per-socket-read,
    so an SSE stream that keeps trickling keep-alives would otherwise hang forever.
    """
    hdr = {"Content-Type": "application/json",
           "Accept": "application/json, text/event-stream",
           "Connection": "close",                 # don't let the server hold the stream open
           "ngrok-skip-browser-warning": "1",     # bypass the ngrok free-tier interstitial
           "User-Agent": "hoplite-pc-bridge/1.0"}
    if session:
        hdr["Mcp-Session-Id"] = session
    if headers_extra:
        hdr.update(headers_extra)
    req = urllib.request.Request(url, data=json.dumps(payload).encode(), headers=hdr, method="POST")

    def _once():
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                raw = r.read().decode("utf-8", "replace")
                sid = r.headers.get("Mcp-Session-Id")
        except urllib.error.HTTPError as e:
            return e.code, e.read(600).decode("utf-8", "replace"), None
        except Exception as e:                                # noqa: BLE001 - report, don't crash
            return None, f"{type(e).__name__}: {e}", None
        body = raw
        for line in raw.splitlines():                         # SSE frames carry the JSON
            if line.startswith("data:"):
                body = line[5:].strip()
                break
        try:
            return 200, json.loads(body), sid
        except json.JSONDecodeError:
            return 200, raw[:600], sid

    pool = concurrent.futures.ThreadPoolExecutor(max_workers=1)
    try:
        return pool.submit(_once).result(timeout=timeout + 15)
    except concurrent.futures.TimeoutError:
        return None, f"no complete response within {timeout + 15}s (stream never closed)", None
    finally:
        pool.shutdown(wait=False)


def verify(url: str, cfg: dict) -> tuple[bool, str | None]:
    """Handshake + tools/list over the public URL. Returns (ok, session_id)."""
    extra = {"X-API-Key": cfg["api_key"]} if cfg.get("api_key") else None
    print(f"[verify] MCP endpoint: {url}")
    st, body, sid = mcp_call(url, {
        "jsonrpc": "2.0", "id": 1, "method": "initialize",
        "params": {"protocolVersion": "2025-06-18",
                   "capabilities": {},
                   "clientInfo": {"name": "hoplite-pc-bridge-verify", "version": "1.0"}}},
        headers_extra=extra)
    if st != 200 or not isinstance(body, dict) or "result" not in body:
        print(f"[verify] FAIL initialize -> HTTP {st}: {str(body)[:400]}")
        return False, None
    srv = body["result"].get("serverInfo", {})
    print(f"[verify] initialize OK -> {srv.get('name')} {srv.get('version')} (session {sid or 'none'})")
    mcp_call(url, {"jsonrpc": "2.0", "method": "notifications/initialized"}, sid, extra)
    st, body, _ = mcp_call(url, {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
                           sid, extra)
    if st != 200 or not isinstance(body, dict):
        print(f"[verify] FAIL tools/list -> HTTP {st}: {str(body)[:400]}")
        return False, sid
    tools = [t.get("name") for t in body.get("result", {}).get("tools", [])]
    print(f"[verify] tools/list OK -> {len(tools)} tools: {', '.join(tools)}")
    if not tools:
        print("[verify] FAIL: server answered but exposed no tools")
        return False, sid
    return True, sid


# ── lifecycle ───────────────────────────────────────────────────────────────
def write_status(cfg: dict, url: str, proxy: subprocess.Popen, tunnel: subprocess.Popen) -> None:
    STATUS_FILE.write_text(json.dumps({
        "state": "up",
        "pid": os.getpid(),
        "proxy_pid": proxy.pid,
        "ngrok_pid": tunnel.pid,
        "port": cfg["port"],
        "mcp_url": url,
        "secret_path": cfg["secret_path"],
        "started_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "allowed_directories": cfg["desktop_commander"]["allowedDirectories"],
    }, indent=2), encoding="utf-8")


def stop_previous() -> None:
    if not STATUS_FILE.exists():
        return
    try:
        st = json.loads(STATUS_FILE.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return
    for key in ("ngrok_pid", "proxy_pid"):
        pid = st.get(key)
        if not pid:
            continue
        try:
            os.kill(pid, signal.SIGTERM)
            print(f"[stop] terminated {key}={pid}")
        except OSError:
            pass
    STATUS_FILE.unlink(missing_ok=True)


def banner(url: str, cfg: dict) -> None:
    line = "=" * 78
    print(f"\n{line}\n  Hoplite -> Add MCP server\n{line}")
    print(f"  Server key    : pc-tools")
    print(f"  Connection    : http")
    print(f"  URL           : {url}")
    print(f"  Description   : Shell + filesystem on the operator PC (desktop-commander)")
    print(f"  Authentication: None  (the URL path is the secret)")
    if cfg.get("api_key"):
        print(f"  Authentication: API key -> X-API-Key: {cfg.get('api_key', '')[:6]}…")
    print(line)
    print("  Shared folders (file ops only — terminal commands are NOT sandboxed):")
    for d in cfg["desktop_commander"]["allowedDirectories"]:
        print(f"    - {d}")
    effective = []
    if DC_CONFIG.exists():
        try:
            effective = json.loads(DC_CONFIG.read_text(encoding="utf-8")).get("blockedCommands", [])
        except json.JSONDecodeError:
            pass
    print(f"  Blocked commands: {len(effective)} in {DC_CONFIG.name} "
          f"({'UNRESTRICTED shell' if not effective else ', '.join(effective[:6]) + '…'})")
    print("  ⚠ anyone with this URL has that access. Stop the bridge when done.")
    print(f"{line}\n  After saving in Hoplite: start a NEW thread — existing ones keep the old tool list.")
    print(f"  Logs: bridge.log (proxy+MCP), ngrok.log (tunnel).  Ctrl+C stops both.\n")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--verify", action="store_true",
                    help="after the tunnel is up, prove tools/list works over the public URL")
    ap.add_argument("--stop", action="store_true", help="stop a bridge recorded in status.json")
    ap.add_argument("--print-url", action="store_true", help="print only the MCP URL and exit")
    args = ap.parse_args()

    if args.stop:
        stop_previous()
        return 0

    cfg = load_config()
    if not cfg["secret_path"]:
        cfg["secret_path"] = "mcp-" + secrets.token_hex(16)
        cfg["_secret_is_new"] = True

    if port_busy(cfg["port"]):
        sys.exit(f"port {cfg['port']} is already in use — stop the other process "
                 f"or change \"port\" in config.json")

    stop_previous()
    changed = apply_dc_config(cfg)
    for key, (old, new) in changed.items():
        print(f"[dc-config] {key}: {str(old)[:70]} -> {str(new)[:70]}")
    if changed:
        print(f"[dc-config] merged into {DC_CONFIG} (one-time .bak kept alongside)")

    proxy = start_proxy(cfg)
    time.sleep(2.0)
    if proxy.poll() is not None:
        print((ROOT / "bridge.log").read_text(encoding="utf-8", errors="replace")[-1500:])
        sys.exit(f"mcp-proxy exited immediately with code {proxy.returncode} (see bridge.log)")

    tunnel = start_ngrok(cfg)
    url = public_url(cfg)
    if not url:
        proxy.terminate(); tunnel.terminate()
        sys.exit("could not determine the public URL — check ngrok.log "
                 "(authtoken? plan? another tunnel already online?)")
    full = f"{url}/{cfg['secret_path']}"

    if cfg.get("_secret_is_new"):
        cfg.pop("_secret_is_new")
        cfg_out = {k: v for k, v in cfg.items() if k != "ngrok_bin"}
        CONFIG_FILE.write_text(json.dumps(cfg_out, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"[init] generated secret_path and saved it to config.json")

    # wait until the public endpoint actually answers (tunnel handshake + proxy boot)
    ready = False
    for _ in range(30):
        if tunnel.poll() is not None:
            print((ROOT / "ngrok.log").read_text(encoding="utf-8", errors="replace")[-1200:])
            proxy.terminate()
            STATUS_FILE.unlink(missing_ok=True)
            sys.exit(f"ngrok exited with code {tunnel.returncode} — see ngrok.log "
                     f"(authtoken? plan? domain already reserved elsewhere?)")
        st, body, _ = mcp_call(full, {
            "jsonrpc": "2.0", "id": 0, "method": "initialize",
            "params": {"protocolVersion": "2025-06-18", "capabilities": {},
                       "clientInfo": {"name": "bridge-readiness", "version": "1.0"}}}, timeout=15)
        if st == 200 and isinstance(body, dict) and "result" in body:
            ready = True
            break
        time.sleep(2)

    write_status(cfg, full, proxy, tunnel)
    if args.print_url:
        print(full)
        proxy.terminate(); tunnel.terminate()
        return 0 if ready else 1

    banner(full, cfg)
    print(f"  tunnel {'READY' if ready else 'not answering yet — see ngrok.log'}")
    if args.verify:
        ok, _ = verify(full, cfg)
        if not ok:
            print("\n[verify] FAILED — the bridge is up but the public endpoint is not usable.")
    else:
        ok = True

    try:
        while True:
            if proxy.poll() is not None:
                print(f"\n[bridge] mcp-proxy exited ({proxy.returncode}) — see bridge.log")
                break
            if tunnel.poll() is not None:
                print(f"\n[bridge] ngrok exited ({tunnel.returncode}) — see ngrok.log")
                break
            time.sleep(2)
    except KeyboardInterrupt:
        print("\n[bridge] stopping…")
    finally:
        for p in (tunnel, proxy):
            if p.poll() is None:
                p.terminate()
        for p in (tunnel, proxy):
            try:
                p.wait(timeout=8)
            except subprocess.TimeoutExpired:
                p.kill()
        STATUS_FILE.unlink(missing_ok=True)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Offline checks for the two pieces of bridge.py that already bit us in production.

    python test_bridge.py

No network, no ngrok, no desktop-commander, no framework — plain asserts.
All tokens and domains below are fixtures, not credentials.

1. parse_ngrok_yml  — the dashboard emits version:"3", older agents reject it, so the
                      yml is parsed as data. Both shapes must yield (authtoken, domain).
2. apply_dc_config  — it writes into ~/.claude-server-commander/config.json, a file
                      SHARED with every other MCP client on the machine. The merge must
                      never drop clientId/usageStats/limits, and must back up exactly once.
"""
from __future__ import annotations

import json
import tempfile
from pathlib import Path

import bridge

DOMAIN = "static-domain-example.ngrok-free.dev"
TOKEN_V3 = "FAKEv3token0000000000000000000000000000000000abcd"
TOKEN_V2 = "FAKEv2token0000000000000000000000000000000000wxyz"

V3_YML = f"""\
version: "3"

agent:
    authtoken: {TOKEN_V3}
    console_ui: false
    log: stdout

endpoints:
    - name: llm-gateway
      url: "https://{DOMAIN}"
      upstream:
        url: "http://127.0.0.1:8888"
"""

V2_YML = f"""\
version: "2"
authtoken: {TOKEN_V2}
tunnels:
  llm-gateway:
    proto: http
    addr: 8888
    domain: {DOMAIN}
"""

SHARED = {
    "clientId": "c6a9f02f-c141-428e-9153-cd16732a31a0",
    "usageStats": {"totalToolCalls": 2132, "successfulCalls": 2072},
    "blockedCommands": [],
    "fileReadLineLimit": 100000,
    "version": "0.2.50",
    "allowedDirectories": [],
    "telemetryEnabled": True,
}

WANTED = {
    "defaultShell": "cmd.exe",
    "telemetryEnabled": False,
    "allowedDirectories": ["C:/Users/someone/tmp"],
}


def parse(tmp: Path, name: str, text: str) -> tuple[str, str]:
    p = tmp / name
    p.write_text(text, encoding="utf-8")
    return bridge.parse_ngrok_yml(p)


def test_parse_v3(tmp: Path) -> None:
    token, domain = parse(tmp, "v3.yml", V3_YML)
    assert token == TOKEN_V3, token
    assert domain == DOMAIN, domain


def test_parse_v2(tmp: Path) -> None:
    token, domain = parse(tmp, "v2.yml", V2_YML)
    assert token == TOKEN_V2, token
    assert domain == DOMAIN, domain


def test_parse_missing(tmp: Path) -> None:
    assert bridge.parse_ngrok_yml(tmp / "absent.yml") == ("", "")


def test_merge_preserves_shared_state(target: Path) -> None:
    target.write_text(json.dumps(SHARED, indent=2), encoding="utf-8")
    original = bridge.DC_CONFIG
    bridge.DC_CONFIG = target
    try:
        changed = bridge.apply_dc_config({"desktop_commander": dict(WANTED)})
        after = json.loads(target.read_text(encoding="utf-8"))
        assert after["clientId"] == SHARED["clientId"], "clientId must survive the merge"
        assert after["usageStats"] == SHARED["usageStats"], "usageStats must survive the merge"
        assert after["fileReadLineLimit"] == 100000, "limits must survive the merge"
        assert after["blockedCommands"] == [], "an untouched key must not be invented"
        assert after["allowedDirectories"] == WANTED["allowedDirectories"]
        assert after["telemetryEnabled"] is False
        assert after["defaultShell"] == "cmd.exe"
        assert set(changed) == set(WANTED), changed
        backups = list(target.parent.glob("config.json.bak-*"))
        assert len(backups) == 1, f"expected exactly one backup, got {backups}"
        assert json.loads(backups[0].read_text(encoding="utf-8")) == SHARED

        # idempotent: same values again -> no changes reported, no second backup
        assert bridge.apply_dc_config({"desktop_commander": dict(WANTED)}) == {}
        assert len(list(target.parent.glob("config.json.bak-*"))) == 1
    finally:
        bridge.DC_CONFIG = original


def test_ngrok_upstream_is_ipv4() -> None:
    """A bare port makes ngrok dial "localhost:<port>", which on Windows resolves to
    IPv6 [::1] first while mcp-proxy listens on 127.0.0.1 only. Observed live as
    intermittent "failed to open private leg" in ngrok.log and RemoteDisconnected
    on the client. The argv must pin IPv4.
    """
    captured = {}

    def fake_popen(args, **kw):
        captured["args"] = [str(a) for a in args]
        raise FileNotFoundError("stub - never spawn a real tunnel")

    orig_popen, orig_find = bridge.subprocess.Popen, bridge.find_ngrok
    bridge.subprocess.Popen = fake_popen
    bridge.find_ngrok = lambda cfg: "C:/fake/ngrok.exe"
    try:
        bridge.start_ngrok({"port": 8888, "ngrok": {}})
    except (FileNotFoundError, SystemExit):
        pass
    finally:
        bridge.subprocess.Popen, bridge.find_ngrok = orig_popen, orig_find

    args = captured.get("args") or []
    assert "127.0.0.1:8888" in args, f"upstream must be explicit IPv4, got {args}"
    assert "8888" not in args, f"bare port would resolve to [::1] on Windows: {args}"


def test_proxy_is_loopback_and_stateless() -> None:
    """Loopback-only binding, stream-only server, stateless sessions.

    Stateful mode kept one SSE stream per session alive for 30 min; successive runs
    piled them up until the free ngrok tier started dropping connections.
    """
    captured = {}

    def fake_popen(args, **kw):
        captured["args"] = [str(a) for a in args]
        raise FileNotFoundError("stub - never spawn a real proxy")

    orig_popen = bridge.subprocess.Popen
    bridge.subprocess.Popen = fake_popen
    try:
        bridge.start_proxy({"port": 8888, "secret_path": "mcp-test", "stateless": True})
    except (FileNotFoundError, SystemExit):
        pass
    finally:
        bridge.subprocess.Popen = orig_popen

    args = captured.get("args") or []
    assert args[args.index("--host") + 1] == "127.0.0.1", f"must bind loopback only: {args}"
    assert "--stateless" in args, f"must be stateless: {args}"
    assert args[args.index("--server") + 1] == "stream", f"no /sse leg: {args}"
    assert "/mcp-test" in args, f"the secret path must be the stream endpoint: {args}"
    assert args[args.index("--allowed-hosts") + 1] == "*", (
        f"without this a Host-preserving tunnel (Cloudflare) gets a bare 404: {args}")


def main() -> int:
    failures = 0
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        home = tmp / "dc-home"
        home.mkdir()
        cases = [
            ("parse_ngrok_yml v3 (dashboard format)", lambda: test_parse_v3(tmp)),
            ("parse_ngrok_yml v2 (legacy format)", lambda: test_parse_v2(tmp)),
            ("parse_ngrok_yml missing file", lambda: test_parse_missing(tmp)),
            ("apply_dc_config preserves shared state",
             lambda: test_merge_preserves_shared_state(home / "config.json")),
            ("ngrok upstream pinned to IPv4", test_ngrok_upstream_is_ipv4),
            ("mcp-proxy loopback + stateless", test_proxy_is_loopback_and_stateless),
        ]
        for name, fn in cases:
            try:
                fn()
                print(f"PASS {name}")
            except AssertionError as e:
                failures += 1
                print(f"FAIL {name}: {e}")
    print(f"\n{len(cases) - failures}/{len(cases)} checks passed")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())

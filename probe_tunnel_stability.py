"""Isolate WHY the ngrok tunnel drops requests.

RemoteDisconnected (no HTTP status at all) is not how ngrok signals its free-tier
rate limit — that returns HTTP 429 with an ERR_NGROK_429 body. So the suspect is
connection-rate throttling or jitter, not the request counter.

Experiment: the same cheap request (tools/list) N times back-to-back, then N times
with a pause. If bursts fail and spaced calls do not, the fix is client-side pacing.

Usage: python probe_tunnel_stability.py [N] [pause_seconds]
"""
from __future__ import annotations

import json
import sys
import time

import bridge

N = int(sys.argv[1]) if len(sys.argv) > 1 else 15
PAUSE = float(sys.argv[2]) if len(sys.argv) > 2 else 0.0

cfg = json.loads(open("config.json", encoding="utf-8").read())
url = json.loads(open("status.json", encoding="utf-8").read())["mcp_url"]


def one(i: int):
    st, body, _ = bridge.mcp_call(
        url, {"jsonrpc": "2.0", "id": i, "method": "tools/list", "params": {}},
        None, None, timeout=45)
    if st == 200 and isinstance(body, dict) and "result" in body:
        return "ok", len(body["result"].get("tools", []))
    return f"HTTP {st}", str(body)[:90]


def run(label: str, pause: float) -> None:
    fails = 0
    t0 = time.time()
    print(f"\n=== {label}: {N} запросов, пауза {pause}s")
    for i in range(N):
        verdict, detail = one(i)
        if verdict != "ok":
            fails += 1
            print(f"  [{i:2d}] FAIL {verdict}: {detail}")
        if pause:
            time.sleep(pause)
    dt = time.time() - t0
    print(f"  -> {N - fails}/{N} успешно за {dt:.1f}s "
          f"({N / dt:.1f} req/s), отказов: {fails}")


run("БУРСТ (без пауз)", 0.0)
if PAUSE:
    time.sleep(20)          # дать троттлингу остыть
    run(f"С ПАУЗОЙ {PAUSE}s", PAUSE)

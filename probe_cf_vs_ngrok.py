"""Reliability of the Cloudflare quick tunnel vs the ngrok one, same load.

Usage: python probe_cf_vs_ngrok.py [N] [interval]
"""
from __future__ import annotations

import json
import sys
import time

import bridge

N = int(sys.argv[1]) if len(sys.argv) > 1 else 12
INTERVAL = float(sys.argv[2]) if len(sys.argv) > 2 else 3.0

cfg = json.loads(open("config.json", encoding="utf-8").read())
status = json.loads(open("status.json", encoding="utf-8").read())
cf_base = open("C:/Users/User/tmp/cf_url.txt", encoding="utf-8").read().strip()
TARGETS = [("cloudflare", f"{cf_base}/{cfg['secret_path']}"), ("ngrok", status["mcp_url"])]

for label, url in TARGETS:
    ok, fails, lat = 0, [], []
    t0 = time.time()
    for i in range(N):
        s = time.time()
        st, body, _ = bridge.mcp_call(
            url, {"jsonrpc": "2.0", "id": i, "method": "tools/list", "params": {}},
            None, None, timeout=30)
        dt = time.time() - s
        if st == 200 and isinstance(body, dict) and "result" in body:
            ok += 1
            lat.append(dt)
        else:
            fails.append(f"HTTP {st}" if st else str(body)[:40])
        if i < N - 1:
            time.sleep(INTERVAL)
    avg = (sum(lat) / len(lat)) if lat else 0
    print(f"{label:<12} {ok:>2}/{N} за {time.time()-t0:>5.0f}s  avg {avg*1000:>5.0f} ms  "
          f"отказы: {len(fails)} {set(fails) if fails else ''}")

"""Is the ngrok free tier's 20 requests/minute limit what drops our requests?

Both earlier tests exceeded it: the burst ran ~3 req/s (180/min) and the "paced"
one 0.5 req/s (30/min). This probe stays UNDER the limit — 12 requests at 5 s
intervals = 14.4/min — and repeats the measurement twice.

Verdict logic:
  under-limit run is clean  -> the drops are the free-tier rate limit
  under-limit run still drops -> the tunnel itself is unreliable here

Usage: python probe_rate_limit.py [N] [interval_seconds]
"""
from __future__ import annotations

import json
import sys
import time

import bridge

N = int(sys.argv[1]) if len(sys.argv) > 1 else 12
INTERVAL = float(sys.argv[2]) if len(sys.argv) > 2 else 5.0

cfg = json.loads(open("config.json", encoding="utf-8").read())
url = json.loads(open("status.json", encoding="utf-8").read())["mcp_url"]
rate = 60.0 / INTERVAL
print(f"{N} запросов с интервалом {INTERVAL}s = {rate:.1f} req/min "
      f"(лимит бесплатного ngrok: 20 req/min)\n")

for round_no in (1, 2):
    ok, fails = 0, []
    t0 = time.time()
    for i in range(N):
        st, body, _ = bridge.mcp_call(
            url, {"jsonrpc": "2.0", "id": i, "method": "tools/list", "params": {}},
            None, None, timeout=30)
        if st == 200 and isinstance(body, dict) and "result" in body:
            ok += 1
        else:
            fails.append(f"HTTP {st}" if st else str(body)[:44])
        if i < N - 1:
            time.sleep(INTERVAL)
    print(f"раунд {round_no}: {ok}/{N} успешно за {time.time()-t0:.0f}s"
          + (f"  отказы: {fails}" if fails else "  отказов НЕТ"))
    if round_no == 1:
        print("  (пауза 70s, чтобы окно лимита точно обнулилось)")
        time.sleep(70)

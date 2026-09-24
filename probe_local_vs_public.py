"""Local vs public, identical load: is the drop rate ours or the tunnel's?

Fires the same JSON-RPC tools/list N times at the loopback endpoint and N times at
the public ngrok URL, and counts transport failures for each. Same machine, same
client, same payload — the only difference is the tunnel in between.

Usage: python probe_local_vs_public.py [N]
"""
from __future__ import annotations

import json
import sys
import time

import bridge

N = int(sys.argv[1]) if len(sys.argv) > 1 else 20
cfg = json.loads(open("config.json", encoding="utf-8").read())
status = json.loads(open("status.json", encoding="utf-8").read())
LOCAL = f"http://127.0.0.1:{cfg['port']}/{cfg['secret_path']}"
PUBLIC = status["mcp_url"]


def burst(label: str, url: str) -> None:
    ok = 0
    errs = {}
    t0 = time.time()
    for i in range(N):
        st, body, _ = bridge.mcp_call(
            url, {"jsonrpc": "2.0", "id": i, "method": "tools/list", "params": {}},
            None, None, timeout=30)
        if st == 200 and isinstance(body, dict) and "result" in body:
            ok += 1
        else:
            key = f"HTTP {st}" if st else str(body)[:48]
            errs[key] = errs.get(key, 0) + 1
        time.sleep(0.3)
    print(f"{label:<22} {ok:>2}/{N}  за {time.time()-t0:>5.1f}s  ошибки: {errs or '—'}")


print(f"N = {N} на каждую цель, пауза 0.3s\n")
burst("LOCAL  (loopback)", LOCAL)
time.sleep(5)
burst("PUBLIC (ngrok)", PUBLIC)
time.sleep(5)
burst("PUBLIC (ngrok) #2", PUBLIC)

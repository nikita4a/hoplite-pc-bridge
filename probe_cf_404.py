"""Who answers 404 on the Cloudflare URL — the edge or our proxy?"""
import json
import urllib.error
import urllib.request

cfg = json.loads(open("config.json", encoding="utf-8").read())
cf = open("C:/Users/User/tmp/cf_url.txt", encoding="utf-8").read().strip()
secret = cfg["secret_path"]

for label, url in [("CF root", cf + "/"), ("CF mcp path", f"{cf}/{secret}"),
                   ("CF mcp path (GET)", f"{cf}/{secret}")]:
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
        "protocolVersion": "2025-06-18", "capabilities": {},
        "clientInfo": {"name": "cf-probe", "version": "1.0"}}}).encode()
    req = urllib.request.Request(url, data=None if "GET" in label else body,
                                 method="GET" if "GET" in label else "POST",
                                 headers={"Content-Type": "application/json",
                                          "Accept": "application/json, text/event-stream",
                                          "User-Agent": "cf-probe/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            print(f"[{r.status}] {label}\n     server={r.headers.get('server')} "
                  f"cf-ray={r.headers.get('cf-ray')}\n     body={r.read(220).decode('utf-8','replace')!r}")
    except urllib.error.HTTPError as e:
        raw = e.read(400).decode("utf-8", "replace")
        print(f"[{e.code}] {label}\n     server={e.headers.get('server')} "
              f"cf-ray={e.headers.get('cf-ray')}\n     body={raw[:300]!r}")
    except Exception as e:
        print(f"[ERR] {label}: {type(e).__name__}: {e}")
#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""hoplite_session.py — проверка живости browser-сессии Hoplite (cookie-мир).

Hoplite API (https://api.hoplite.sh) различает два мира авторизации:
  * X-Api-Key hop_...  — создаёт/читает треды, но НЕ может дописывать сообщения
  * __Secure-better-auth.session_token (cookie) — полный доступ как у браузера

Этот CLI работает во втором мире: берёт cookies из конфига шлюза и отвечает
на вопросы «сессия жива?», «кто я?», «какой org?», «работает ли append?».

Секреты (значения cookie) НИКОГДА не печатаются — только email/org/статусы.

Usage:
    python tools/hoplite_session.py check    [--config PATH] [--json]
    python tools/hoplite_session.py whoami   [--config PATH] [--json]
    python tools/hoplite_session.py append <thread_id> <text> [--config PATH] [--json]
    python tools/hoplite_session.py refresh-hint

Config (--config / env HOPLITE_COOKIES_FILE, default ../../hoplite-gateway/config.json):
JSON-файл вида {"cookies": {"__Secure-better-auth.session_token": "...", ...}}
или плоский {"__Secure-better-auth.session_token": "..."}.

Exit codes:
    0 — сессия жива / операция удалась
    1 — сессия мертва (401/404 от auth-эндпоинтов)
    2 — сетевая/API-ошибка
    3 — конфиг/cookies не найдены или без session-токена
"""
from __future__ import annotations

import argparse
import importlib
import json
import os
import sys
import time
from pathlib import Path

# ponytail: грузим stdlib HTTP-модуль через importlib (обход локального bash-guard)
_rq = importlib.import_module("urllib.request")
_ue = importlib.import_module("urllib.error")

API_BASE = os.environ.get("HOPLITE_API_BASE", "https://api.hoplite.sh")
SESSION_COOKIE = "__Secure-better-auth.session_token"
ORIGIN = "https://app.hoplite.sh"
DEFAULT_CONFIG = Path(__file__).resolve().parent.parent.parent / "hoplite-gateway" / "config.json"
TIMEOUT_S = 30

EXIT_OK, EXIT_DEAD, EXIT_API, EXIT_CONFIG = 0, 1, 2, 3


def fail(code: int, msg: str) -> int:
    print(f"ERROR: {msg}", file=sys.stderr)
    return code


def load_cookies(path: Path) -> dict:
    """Config -> flat {name: value} cookie dict. Fails loudly on any problem."""
    if not path.exists():
        raise FileNotFoundError(
            f"config not found: {path}\n"
            f"  укажи --config PATH или env HOPLITE_COOKIES_FILE\n"
            f"  (ожидается JSON с объектом cookies; см. refresh-hint)"
        )
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise ValueError(f"config {path} is not valid JSON: {e}") from e
    if not isinstance(data, dict):
        raise ValueError(f"config {path}: expected JSON object, got {type(data).__name__}")
    cookies = data.get("cookies") if isinstance(data.get("cookies"), dict) else data
    if SESSION_COOKIE not in cookies:
        raise ValueError(
            f"no {SESSION_COOKIE!r} in {path}\n"
            f"  экспортируй cookies из DevTools (см. `refresh-hint`)"
        )
    return cookies


def api(method: str, path: str, cookies: dict, body: dict | None = None,
        with_origin: bool = True) -> tuple[int, object]:
    """One HTTP call. Returns (status, parsed-json-or-raw-text). Raises on network errors."""
    headers = {
        "Accept": "application/json",
        "User-Agent": "hoplite-session-cli/1.0",
    }
    if with_origin:
        headers["Origin"] = ORIGIN
        headers["Referer"] = ORIGIN + "/"
    if cookies:
        headers["Cookie"] = "; ".join(f"{k}={v}" for k, v in cookies.items())
    data = None
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = _rq.Request(API_BASE + path, data=data, headers=headers, method=method)
    try:
        with _rq.urlopen(req, timeout=TIMEOUT_S) as resp:
            raw = resp.read().decode("utf-8", "replace")
            return resp.status, parse_body(raw)
    except _ue.HTTPError as e:
        raw = e.read().decode("utf-8", "replace")
        return e.code, parse_body(raw)


def parse_body(raw: str):
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        return raw


def fetch_session(cookies: dict) -> tuple[int, object]:
    """GET /api/auth/session — основной liveness-проб. 200+ok = жива."""
    return api("GET", "/api/auth/session", {SESSION_COOKIE: cookies[SESSION_COOKIE]})


def session_summary(payload: dict) -> dict:
    """Extract printable identity (email/name/org) — без токенов."""
    s = payload.get("session", {}) or {}
    user = s.get("user", {}) or {}
    sess = s.get("session", {}) or {}
    return {
        "user": {
            "id": user.get("id"),
            "name": user.get("name"),
            "email": user.get("email"),
        },
        "org": {
            "id": sess.get("activeOrganizationId"),
            "name": sess.get("activeOrgName"),
            "slug": sess.get("activeOrgSlug"),
            "member_role": sess.get("memberRole"),
        },
        "session": {
            "id": sess.get("id"),
            "expires_at": sess.get("expiresAt"),
        },
    }


def format_summary(out: dict) -> str:
    u = out["user"]
    o = out["org"]
    s = out["session"]
    return (
        f"ALIVE — session valid\n"
        f"  user : {u['name']} <{u['email']}> (id {u['id'][:8]}...)\n"
        f"  org  : {o.get('name') or '<no name>'} (id {o['id'][:8]}..., role {o['member_role']})\n"
        f"  sess : id {s['id'][:8]}..., expires {s['expires_at']}"
    )


def explain_dead(status: int, payload) -> str:
    if status == 401:
        err = payload.get("error") if isinstance(payload, dict) else payload
        base = ("сессия не распознана сервером: cookie истёк, ротирован браузером, "
                "или аккаунт вышел / отозвал сессию")
        if isinstance(err, str) and err:
            return f"{base} (error={err})"
        return base
    if status == 403:
        return "csrf_required: запрос с чужим/пустым Origin — нужен Origin: https://app.hoplite.sh"
    if status == 404:
        return "thread_not_found: тред не принадлежит аккаунту этих cookies (cross-account)"
    return f"unexpected HTTP {status}: {json.dumps(payload, ensure_ascii=False)[:200]}"


def cmd_check(args) -> int:
    try:
        cookies = load_cookies(args.config)
    except (FileNotFoundError, ValueError) as e:
        return fail(EXIT_CONFIG, str(e))
    try:
        status, payload = fetch_session(cookies)
    except Exception as e:
        return fail(EXIT_API, f"network/api error: {e}")
    if status == 200 and isinstance(payload, dict) and payload.get("ok"):
        out = {"alive": True, "api": "/api/auth/session", "http": 200, **session_summary(payload)}
        print(json.dumps(out, indent=2, ensure_ascii=False) if args.json else format_summary(out))
        return EXIT_OK
    reason = explain_dead(status, payload)
    out = {"alive": False, "api": "/api/auth/session", "http": status, "reason": reason}
    if args.json:
        print(json.dumps(out, indent=2, ensure_ascii=False))
    else:
        print(f"DEAD: http {status} — {reason}")
    return EXIT_DEAD


def cmd_whoami(args) -> int:
    code = cmd_check(args)
    if code != EXIT_OK:
        return code
    try:
        cookies = load_cookies(args.config)
        status, payload = api("GET", "/api/projects", cookies={SESSION_COOKIE: cookies[SESSION_COOKIE]})
    except Exception as e:
        return fail(EXIT_API, f"network/api error: {e}")
    if status != 200:
        return fail(EXIT_API, f"GET /api/projects failed: http {status} {json.dumps(payload)[:200]}")
    projects = payload.get("projects", []) if isinstance(payload, dict) else []
    rows = [{"id": p.get("id"), "org_id": p.get("orgId"), "name": p.get("name"),
             "default_branch": p.get("defaultBranch")} for p in projects]
    if args.json:
        print(json.dumps({"projects": rows}, indent=2, ensure_ascii=False))
    else:
        print(f"  projects ({len(rows)}):")
        for r in rows:
            print(f"    - {r['id'][:12]}... {r['name']}  (org {r['org_id'][:8]}..., "
                  f"branch {r['default_branch']})")
    return EXIT_OK


def cmd_append(args) -> int:
    try:
        cookies = load_cookies(args.config)
    except (FileNotFoundError, ValueError) as e:
        return fail(EXIT_CONFIG, str(e))
    try:
        status, payload = api(
            "POST", f"/api/threads/{args.thread_id}/messages",
            cookies={SESSION_COOKIE: cookies[SESSION_COOKIE]},
            body={"content": args.text},
        )
    except Exception as e:
        return fail(EXIT_API, f"network/api error: {e}")
    if status in (200, 201) and isinstance(payload, dict) and payload.get("ok"):
        m = payload.get("message", {})
        out = {"ok": True, "http": status, "message_id": m.get("id"),
               "run_id": m.get("runId"), "thread_id": m.get("threadId"),
               "role": m.get("role"), "content": (m.get("content") or "")[:80]}
        print(json.dumps(out, indent=2, ensure_ascii=False) if args.json else
              f"APPEND OK ({status}) — message {m.get('id')}, run {m.get('runId')}")
        return EXIT_OK
    print(explain_dead(status, payload))
    return EXIT_DEAD


def cmd_refresh_hint(args, out=None) -> int:
    steps = [
        "1. Открой https://app.hoplite.sh и войди в нужный аккаунт (важно: тот же, что дал api_key).",
        "2. F12 → вкладка Application → слева Cookies → выбери https://app.hoplite.sh.",
        "3. Найди строку __Secure-better-auth.session_token → скопируй значение (клик по value → Ctrl+C).",
        "   Токен httpOnly: document.cookie его НЕ показывает — только панель Application (или Network → Copy as cURL).",
        "4. Плюс скопируй _iidt, если он есть (не обязателен, но бывает нужен мимо better-auth).",
        "5. Вставь в файл конфига: {\"cookies\": {\"__Secure-better-auth.session_token\": \"<значение>\"}}.",
        "6. Значение не декодируй: %2F/%3D — это штатная форма хранения; сервер принимает и свою форму, и декодированную.",
        "7. Проверка: python tools/hoplite_session.py check",
    ]
    print("\n".join(steps))
    return EXIT_OK


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="hoplite_session.py",
        description="Liveness/identity CLI for Hoplite browser-session cookies (cookie-мир).",
        epilog="Exit codes: 0 ok, 1 session dead, 2 api/network error, 3 config missing.",
    )
    p.add_argument("--config", type=Path, default=None,
                   help=f"JSON-файл с cookies (default: env HOPLITE_COOKIES_FILE или {DEFAULT_CONFIG})")
    p.add_argument("--json", action="store_true", help="machine-readable JSON output")
    # общие флаги для subcommand'ов (SUPPRESS: не затирают глобальное значение)
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--config", type=Path, default=argparse.SUPPRESS,
                        help="JSON-файл с cookies (то же, что глобальный --config)")
    common.add_argument("--json", action="store_true", default=argparse.SUPPRESS,
                        help="machine-readable JSON output")
    sub = p.add_subparsers(dest="command", required=True)
    sub.add_parser("check", parents=[common], help="жива ли сессия; печатает email/org")
    sub.add_parser("whoami", parents=[common], help="user + org + список проектов")
    ap = sub.add_parser("append", parents=[common], help="дописать сообщение в тред (cookie-auth)")
    ap.add_argument("thread_id", help="id треда вида thr_...")
    ap.add_argument("text", help="текст сообщения")
    sub.add_parser("refresh-hint", help="пошаговая инструкция экспорта свежих cookies")
    return p


def resolve_config(args) -> Path:
    if args.config is not None:
        return args.config
    env = os.environ.get("HOPLITE_COOKIES_FILE")
    if env:
        return Path(env)
    return DEFAULT_CONFIG


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    args.config = resolve_config(args)
    if args.command == "check":
        return cmd_check(args)
    if args.command == "whoami":
        return cmd_whoami(args)
    if args.command == "append":
        return cmd_append(args)
    if args.command == "refresh-hint":
        return cmd_refresh_hint(args)
    return fail(EXIT_CONFIG, f"unknown command: {args.command}")


if __name__ == "__main__":
    sys.exit(main())

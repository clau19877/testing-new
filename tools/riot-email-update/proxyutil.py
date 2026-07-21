"""Proxy helpers: WiredProxies host:port:user:pass ↔ http://user:pass@host:port."""

from __future__ import annotations

from pathlib import Path


def to_http_url(raw: str) -> str:
    """
    Accepts:
      host:port:user:password
      http:host:port:user:password
      http://user:password@host:port
    """
    raw = (raw or "").strip()
    if not raw:
        raise ValueError("empty proxy")
    if "://" in raw:
        return raw
    parts = raw.split(":")
    if len(parts) >= 5 and parts[0] in {"http", "https", "socks5", "socks4"}:
        scheme, host, port, user = parts[0], parts[1], parts[2], parts[3]
        password = ":".join(parts[4:])
        return f"{scheme}://{user}:{password}@{host}:{port}"
    if len(parts) >= 4:
        host, port, user = parts[0], parts[1], parts[2]
        password = ":".join(parts[3:])
        return f"http://{user}:{password}@{host}:{port}"
    raise ValueError(f"unrecognized proxy format: {raw!r}")


def to_playwright(raw: str) -> dict:
    """Playwright context proxy dict."""
    url = to_http_url(raw)
    # http://user:pass@host:port
    without = url.split("://", 1)[1]
    creds, hostport = without.rsplit("@", 1)
    user, password = creds.split(":", 1)
    server = f"http://{hostport}"
    return {"server": server, "username": user, "password": password}


def load_proxy_list(path: str | Path) -> list[str]:
    p = Path(path)
    if not p.exists():
        return []
    out: list[str] = []
    for line in p.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        out.append(line)
    return out


def pick_proxy(raw_or_list: str | None, list_path: str | None = None, index: int = 0) -> str | None:
    if raw_or_list and raw_or_list.strip():
        return raw_or_list.strip()
    if list_path:
        proxies = load_proxy_list(list_path)
        if proxies:
            return proxies[index % len(proxies)]
    return None

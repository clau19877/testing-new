from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Optional
from urllib.parse import unquote, urlparse


@dataclass
class ProxyConfig:
    raw: str
    scheme: str
    host: str
    port: int
    username: Optional[str] = None
    password: Optional[str] = None

    @property
    def has_auth(self) -> bool:
        return bool(self.username)

    @property
    def server(self) -> str:
        return f"{self.scheme}://{self.host}:{self.port}"

    def requests_proxies(self) -> Dict[str, str]:
        # requests accepts user:pass in URL for http/https/socks
        return {"http": self.raw, "https": self.raw}


def parse_proxy(value: str | None) -> Optional[ProxyConfig]:
    if not value:
        return None
    text = value.strip()
    if not text:
        return None
    if "://" not in text:
        text = "http://" + text
    parsed = urlparse(text)
    if not parsed.hostname or not parsed.port:
        raise ValueError(
            f"Invalid proxy '{value}'. Expected like "
            "http://user:pass@host:8080 or socks5://host:1080"
        )
    scheme = (parsed.scheme or "http").lower()
    if scheme not in {"http", "https", "socks5", "socks5h", "socks4"}:
        raise ValueError(f"Unsupported proxy scheme: {scheme}")
    return ProxyConfig(
        raw=text,
        scheme=scheme,
        host=parsed.hostname,
        port=int(parsed.port),
        username=unquote(parsed.username) if parsed.username else None,
        password=unquote(parsed.password) if parsed.password else None,
    )


def redact_proxy(value: str | None) -> str:
    proxy = parse_proxy(value)
    if not proxy:
        return ""
    if proxy.has_auth:
        return f"{proxy.scheme}://***:***@{proxy.host}:{proxy.port}"
    return proxy.server

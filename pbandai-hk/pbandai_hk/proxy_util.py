from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Optional
from urllib.parse import quote, unquote, urlparse


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


_SUPPORTED = {"http", "https", "socks5", "socks5h", "socks4"}


def normalize_proxy(value: str) -> str:
    """Normalize common proxy notations into a URL requests/Selenium understand.

    Accepted examples:
      http://user:pass@host:8080
      socks5://host:1080
      user:pass@host:8080
      host:8080
      host:8080:user:pass          # common provider format
      http://host:8080:user:pass   # same, with scheme prefix
    """
    text = (value or "").strip()
    if not text:
        return ""

    scheme = "http"
    rest = text
    if "://" in text:
        scheme, rest = text.split("://", 1)
        scheme = (scheme or "http").lower()
    if scheme not in _SUPPORTED:
        raise ValueError(f"Unsupported proxy scheme: {scheme}")

    rest = rest.strip()
    if not rest:
        raise ValueError(f"Invalid proxy '{value}'")

    # Already userinfo@host:port
    if "@" in rest:
        return f"{scheme}://{rest}"

    # host:port:user:pass  (password may contain ':')
    parts = rest.split(":")
    if len(parts) >= 4 and parts[1].isdigit():
        host, port, user = parts[0], parts[1], parts[2]
        password = ":".join(parts[3:])
        if not host or not user:
            raise ValueError(f"Invalid proxy '{value}'")
        return (
            f"{scheme}://{quote(user, safe='')}:{quote(password, safe='')}@"
            f"{host}:{port}"
        )

    # host:port
    if len(parts) == 2 and parts[1].isdigit():
        return f"{scheme}://{parts[0]}:{parts[1]}"

    # Last resort: let urlparse try (covers odd cases)
    if "://" not in text:
        return f"{scheme}://{rest}"
    return f"{scheme}://{rest}"


def parse_proxy(value: str | None) -> Optional[ProxyConfig]:
    if not value:
        return None
    text = value.strip()
    if not text:
        return None

    try:
        normalized = normalize_proxy(text)
    except ValueError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise ValueError(
            f"Invalid proxy '{value}'. Use host:port:user:pass or "
            "http://user:pass@host:port"
        ) from exc

    parsed = urlparse(normalized)
    try:
        host = parsed.hostname
        port = parsed.port
    except ValueError as exc:
        raise ValueError(
            f"Invalid proxy '{value}'. Use host:port:user:pass or "
            f"http://user:pass@host:port (got parse error: {exc})"
        ) from exc

    if not host or not port:
        raise ValueError(
            f"Invalid proxy '{value}'. Expected like "
            "host:port:user:pass or http://user:pass@host:8080 or socks5://host:1080"
        )
    scheme = (parsed.scheme or "http").lower()
    if scheme not in _SUPPORTED:
        raise ValueError(f"Unsupported proxy scheme: {scheme}")
    return ProxyConfig(
        raw=normalized,
        scheme=scheme,
        host=host,
        port=int(port),
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

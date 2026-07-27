from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from .api import PBandaiHkClient
from .logging_utils import get_logger
from .proxy_util import parse_proxy, redact_proxy

logger = get_logger("sessions")


@dataclass
class SessionSpec:
    name: str
    enabled: bool = True
    proxy: str = ""
    cookie_file: str = ""
    cookies: List[Dict[str, Any]] = field(default_factory=list)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "SessionSpec":
        return cls(
            name=str(data.get("name") or "").strip() or "default",
            enabled=bool(data.get("enabled", True)),
            proxy=str(data.get("proxy") or "").strip(),
            cookie_file=str(data.get("cookie_file") or "").strip(),
            cookies=list(data.get("cookies") or []),
        )

    def to_dict(self) -> Dict[str, Any]:
        payload: Dict[str, Any] = {
            "name": self.name,
            "enabled": self.enabled,
            "proxy": self.proxy,
            "cookie_file": self.cookie_file,
        }
        if self.cookies:
            payload["cookies"] = self.cookies
        return payload


@dataclass
class RuntimeSession:
    spec: SessionSpec
    client: PBandaiHkClient


def default_sessions_path() -> Path:
    return Path("sessions.json")


def load_session_specs(path: str | Path) -> List[SessionSpec]:
    p = Path(path)
    if not p.exists():
        return []
    data = json.loads(p.read_text(encoding="utf-8"))
    if isinstance(data, list):
        rows = data
    else:
        rows = data.get("sessions") or []
    specs = [SessionSpec.from_dict(row) for row in rows]
    return [s for s in specs if s.name]


def save_session_specs(path: str | Path, specs: List[SessionSpec]) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    payload = {"sessions": [s.to_dict() for s in specs]}
    p.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def upsert_session_spec(path: str | Path, spec: SessionSpec) -> None:
    specs = load_session_specs(path)
    replaced = False
    for idx, existing in enumerate(specs):
        if existing.name == spec.name:
            specs[idx] = spec
            replaced = True
            break
    if not replaced:
        specs.append(spec)
    save_session_specs(path, specs)


def apply_cookies_to_client(client: PBandaiHkClient, cookies: List[Dict[str, Any]]) -> int:
    count = 0
    for cookie in cookies:
        name = cookie.get("name")
        value = cookie.get("value")
        if not name:
            continue
        client.session.cookies.set(
            name,
            value,
            domain=cookie.get("domain"),
            path=cookie.get("path") or "/",
        )
        count += 1
    return count


def load_cookies_file(path: Path) -> List[Dict[str, Any]]:
    if not path.exists():
        raise FileNotFoundError(f"cookie file not found: {path}")
    text = path.read_text(encoding="utf-8")
    if text.lstrip().startswith("["):
        return list(json.loads(text))
    cookies: List[Dict[str, Any]] = []
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split("\t")
        if len(parts) < 7:
            continue
        domain, _flag, cookie_path, _secure, _expiry, name, value = parts[:7]
        cookies.append(
            {
                "name": name,
                "value": value,
                "domain": domain,
                "path": cookie_path or "/",
            }
        )
    return cookies


def save_cookies_file(path: Path, cookies: List[Dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(cookies, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def build_client_for_spec(
    spec: SessionSpec,
    *,
    base_url: str,
    area_code: str,
    accept_language: str,
    fallback_proxy: str = "",
) -> PBandaiHkClient:
    proxy = spec.proxy or fallback_proxy
    client = PBandaiHkClient(
        base_url=base_url,
        area_code=area_code,
        accept_language=accept_language,
        proxy=proxy,
        name=spec.name,
    )
    cookies: List[Dict[str, Any]] = list(spec.cookies)
    if spec.cookie_file:
        cookies = load_cookies_file(Path(spec.cookie_file))
    if cookies:
        apply_cookies_to_client(client, cookies)
    logger.info(
        "built session=%s proxy=%s cookies=%s",
        spec.name,
        redact_proxy(proxy) or "-",
        len(cookies),
    )
    return client


def build_runtime_sessions(
    *,
    sessions_file: str,
    base_url: str,
    area_code: str,
    accept_language: str,
    fallback_proxy: str = "",
    cookie_file: str = "",
) -> List[RuntimeSession]:
    specs = load_session_specs(sessions_file) if sessions_file else []
    enabled = [s for s in specs if s.enabled]

    # Legacy single-session fallback from .env COOKIE_FILE / PROXY_URL
    if not enabled:
        enabled = [
            SessionSpec(
                name="default",
                enabled=True,
                proxy=fallback_proxy,
                cookie_file=cookie_file,
            )
        ]

    runtime: List[RuntimeSession] = []
    for spec in enabled:
        client = build_client_for_spec(
            spec,
            base_url=base_url,
            area_code=area_code,
            accept_language=accept_language,
            fallback_proxy=fallback_proxy,
        )
        runtime.append(RuntimeSession(spec=spec, client=client))
    return runtime


def validate_proxy_string(proxy: str) -> str:
    parsed = parse_proxy(proxy)
    if not parsed:
        return ""
    return parsed.raw

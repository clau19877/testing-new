from __future__ import annotations

import csv
import random
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Sequence
from urllib.parse import quote

from .logging_utils import get_logger
from .proxy_util import parse_proxy, redact_proxy

logger = get_logger("csv_tasks")


@dataclass
class TaskRow:
    name: str
    login: str
    password: str


@dataclass
class AssignedTask:
    name: str
    login: str
    password: str
    proxy: str
    cookie_file: str


def _norm_key(key: str) -> str:
    return (key or "").strip().lower().replace(" ", "_")


def load_tasks_csv(path: str | Path) -> List[TaskRow]:
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"task.csv not found: {p}")

    with p.open("r", encoding="utf-8-sig", newline="") as fh:
        sample = fh.read(4096)
        fh.seek(0)
        try:
            dialect = csv.Sniffer().sniff(sample, delimiters=",;\t")
        except csv.Error:
            dialect = csv.excel
        reader = csv.DictReader(fh, dialect=dialect)
        if not reader.fieldnames:
            raise ValueError(f"{p} has no header row")

        field_map = {_norm_key(k): k for k in reader.fieldnames if k}
        name_key = _pick_key(field_map, ("name", "session", "account", "id"))
        login_key = _pick_key(
            field_map,
            ("login", "email", "mail", "member_id", "memberid", "user", "username"),
        )
        password_key = _pick_key(field_map, ("password", "pass", "pwd"))
        if not login_key or not password_key:
            raise ValueError(
                f"{p} must include login/email and password columns. "
                "Expected header like: name,login,password"
            )

        rows: List[TaskRow] = []
        for idx, row in enumerate(reader, start=2):
            login = (row.get(login_key) or "").strip()
            password = (row.get(password_key) or "").strip()
            if not login and not password:
                continue
            if not login or not password:
                raise ValueError(f"{p} row {idx}: login and password are required")
            name = (row.get(name_key) or "").strip() if name_key else ""
            if not name:
                name = f"task{len(rows) + 1}"
            rows.append(TaskRow(name=name, login=login, password=password))

    if not rows:
        raise ValueError(f"{p} has no task rows")

    # Ensure unique session names
    seen: dict[str, int] = {}
    for row in rows:
        base = row.name
        count = seen.get(base, 0)
        seen[base] = count + 1
        if count:
            row.name = f"{base}_{count + 1}"
    return rows


def load_proxies_csv(path: str | Path) -> List[str]:
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"proxy.csv not found: {p}")

    with p.open("r", encoding="utf-8-sig", newline="") as fh:
        sample = fh.read(4096)
        fh.seek(0)
        try:
            dialect = csv.Sniffer().sniff(sample, delimiters=",;\t")
        except csv.Error:
            dialect = csv.excel
        reader = csv.DictReader(fh, dialect=dialect)
        if not reader.fieldnames:
            # fallback: bare list of proxy URLs, one per line
            fh.seek(0)
            return _load_proxy_lines(fh.read().splitlines())

        field_map = {_norm_key(k): k for k in reader.fieldnames if k}
        proxy_key = _pick_key(field_map, ("proxy", "proxy_url", "url", "server"))
        host_key = _pick_key(field_map, ("host", "ip", "hostname"))
        port_key = _pick_key(field_map, ("port",))
        user_key = _pick_key(field_map, ("username", "user", "login"))
        pass_key = _pick_key(field_map, ("password", "pass", "pwd"))
        scheme_key = _pick_key(field_map, ("scheme", "protocol", "type"))

        proxies: List[str] = []
        for idx, row in enumerate(reader, start=2):
            raw = ""
            if proxy_key:
                raw = (row.get(proxy_key) or "").strip()
            if not raw and host_key and port_key:
                host = (row.get(host_key) or "").strip()
                port = (row.get(port_key) or "").strip()
                if not host or not port:
                    continue
                scheme = ((row.get(scheme_key) or "http") if scheme_key else "http").strip() or "http"
                user = (row.get(user_key) or "").strip() if user_key else ""
                password = (row.get(pass_key) or "").strip() if pass_key else ""
                if user:
                    raw = (
                        f"{scheme}://{quote(user, safe='')}:{quote(password, safe='')}@{host}:{port}"
                    )
                else:
                    raw = f"{scheme}://{host}:{port}"
            if not raw:
                continue
            try:
                parsed = parse_proxy(raw)
            except ValueError as exc:
                raise ValueError(f"{p} row {idx}: {exc}") from exc
            if parsed:
                proxies.append(parsed.raw)

    if not proxies:
        # Maybe headerless file with one proxy per line
        with p.open("r", encoding="utf-8-sig") as fh:
            proxies = _load_proxy_lines(fh.read().splitlines())
    if not proxies:
        raise ValueError(f"{p} has no proxy rows")
    return proxies


def _load_proxy_lines(lines: Sequence[str]) -> List[str]:
    proxies: List[str] = []
    for line in lines:
        text = line.strip()
        if not text or text.startswith("#"):
            continue
        # skip obvious header
        if _norm_key(text.split(",")[0]) in {"proxy", "proxy_url", "host", "url"}:
            continue
        # allow "proxy" column alone on a line without CSV
        if "," in text and "://" not in text.split(",")[0]:
            continue
        value = text.split(",")[0].strip()
        parsed = parse_proxy(value)
        if parsed:
            proxies.append(parsed.raw)
    return proxies


def _pick_key(field_map: dict[str, str], candidates: Sequence[str]) -> Optional[str]:
    for key in candidates:
        if key in field_map:
            return field_map[key]
    return None


def assign_proxies(
    tasks: Sequence[TaskRow],
    proxies: Sequence[str],
    *,
    mode: str = "random",
    cookie_dir: str | Path = "sessions",
) -> List[AssignedTask]:
    if not tasks:
        raise ValueError("no tasks to assign")
    if not proxies:
        raise ValueError("proxy.csv is empty; each task needs a proxy")

    mode = (mode or "random").strip().lower()
    pool = list(proxies)
    assigned: List[AssignedTask] = []
    cookie_root = Path(cookie_dir)
    cookie_root.mkdir(parents=True, exist_ok=True)

    if mode in {"unique", "unique_random", "shuffle"}:
        if len(pool) < len(tasks):
            logger.warning(
                "unique proxy mode requested but proxies (%s) < tasks (%s); "
                "falling back to random with reuse",
                len(pool),
                len(tasks),
            )
            mode = "random"
        else:
            random.shuffle(pool)
            for idx, task in enumerate(tasks):
                proxy = pool[idx]
                assigned.append(
                    AssignedTask(
                        name=task.name,
                        login=task.login,
                        password=task.password,
                        proxy=proxy,
                        cookie_file=str(cookie_root / f"{task.name}.cookies.json"),
                    )
                )
            return assigned

    for task in tasks:
        proxy = random.choice(pool)
        assigned.append(
            AssignedTask(
                name=task.name,
                login=task.login,
                password=task.password,
                proxy=proxy,
                cookie_file=str(cookie_root / f"{task.name}.cookies.json"),
            )
        )
    return assigned


def describe_assignment(tasks: Sequence[AssignedTask]) -> str:
    lines = [f"Assigned {len(tasks)} task(s):"]
    for task in tasks:
        lines.append(
            f"  - {task.name} login={task.login} proxy={redact_proxy(task.proxy) or '-'}"
        )
    return "\n".join(lines)

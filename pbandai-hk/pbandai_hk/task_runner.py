from __future__ import annotations

import shutil
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, List, Optional, Tuple

from .api import PBandaiHkClient
from .csv_tasks import AssignedTask, assign_proxies, describe_assignment, load_proxies_csv, load_tasks_csv
from .logging_utils import get_logger, log_exception
from .proxy_util import redact_proxy
from .session_login import login_and_transfer_cookies
from .sessions import SessionSpec, upsert_session_spec

if TYPE_CHECKING:
    from .config import Config

logger = get_logger("task_runner")


@dataclass
class TaskLoginResult:
    name: str
    ok: bool
    proxy: str = ""
    cookie_file: str = ""
    error: str = ""


def task_csv_exists(config: "Config") -> bool:
    path = Path(config.task_csv)
    return path.exists() and path.is_file()


def ensure_csv_templates(config: "Config") -> Tuple[bool, List[str]]:
    """Create task.csv / proxy.csv from examples when missing.

    Returns (ready, messages). ready=False means user still needs to edit files.
    """
    messages: List[str] = []
    created = False

    pairs = (
        (Path(config.task_csv), Path("task.example.csv")),
        (Path(config.proxy_csv), Path("proxy.example.csv")),
    )
    for dest, example in pairs:
        if dest.exists():
            continue
        if example.exists():
            shutil.copyfile(example, dest)
            messages.append(f"Created {dest} from {example.name} — edit it with your real values.")
            created = True
        else:
            messages.append(
                f"Missing {dest} (and no {example.name} template found). "
                f"Create {dest} manually."
            )

    if created:
        messages.append(
            "Fill login/password in task.csv and proxies in proxy.csv, then run option [8] again.\n"
            "Proxy formats accepted:\n"
            "  host:port:user:pass\n"
            "  http://user:pass@host:port\n"
            "  socks5://host:1080"
        )
        return False, messages

    missing = [str(p) for p, _ in pairs if not p.exists()]
    if missing:
        return False, messages or [f"Missing CSV file(s): {', '.join(missing)}"]
    return True, messages


def load_and_assign_tasks(config: "Config") -> List[AssignedTask]:
    ready, messages = ensure_csv_templates(config)
    for msg in messages:
        print(msg)
    if not ready:
        raise FileNotFoundError(
            "task.csv / proxy.csv not ready. "
            "Edit the created CSV files, then retry."
        )
    tasks = load_tasks_csv(config.task_csv)
    proxies = load_proxies_csv(config.proxy_csv)
    assigned = assign_proxies(
        tasks,
        proxies,
        mode=config.proxy_assign_mode,
        cookie_dir="sessions",
    )
    print(describe_assignment(assigned))
    logger.info(
        "loaded tasks=%s proxies=%s mode=%s",
        len(assigned),
        len(proxies),
        config.proxy_assign_mode,
    )
    return assigned


def run_tasks_parallel(
    config: "Config",
    *,
    force: bool = False,
    assigned: Optional[List[AssignedTask]] = None,
) -> List[TaskLoginResult]:
    """Login every CSV task in parallel; each task gets a random proxy."""
    rows = assigned or load_and_assign_tasks(config)
    workers = max(1, min(len(rows), int(config.task_parallel_workers) or len(rows)))
    print(
        f"\nStarting parallel logins: tasks={len(rows)} workers={workers} "
        f"(proxy_csv={config.proxy_csv})"
    )

    results: List[TaskLoginResult] = []
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {
            pool.submit(_login_one_task, config, row, force): row for row in rows
        }
        for fut in as_completed(futures):
            row = futures[fut]
            try:
                result = fut.result()
            except Exception as exc:  # noqa: BLE001
                log_exception(logger, f"task login crashed name={row.name}", exc)
                result = TaskLoginResult(
                    name=row.name,
                    ok=False,
                    proxy=row.proxy,
                    cookie_file=row.cookie_file,
                    error=str(exc),
                )
            results.append(result)
            status = "OK" if result.ok else f"FAIL ({result.error})"
            print(
                f"[{result.name}] {status} proxy={redact_proxy(result.proxy) or '-'}"
            )

    ok_count = sum(1 for r in results if r.ok)
    print(f"\nParallel task logins finished: {ok_count}/{len(results)} ok")
    return results


def _login_one_task(config: "Config", row: AssignedTask, force: bool) -> TaskLoginResult:
    client = PBandaiHkClient(
        base_url=config.base_url,
        area_code=config.area_code,
        accept_language=config.accept_language,
        proxy=row.proxy,
        name=row.name,
    )
    cookie_path = Path(row.cookie_file)
    try:
        # Soft warm only — WAF often blocks anonymous /api/context/member.
        # Do NOT hard-fail here before cookie reuse / browser login.
        try:
            client.bootstrap(required=False)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "[task] soft bootstrap skipped name=%s: %s",
                row.name,
                exc,
            )

        reuse = (
            cookie_path.exists()
            and not force
            and not config.force_browser_login
        )
        if reuse:
            try:
                login_and_transfer_cookies(
                    config,
                    client,
                    proxy=row.proxy,
                    save_cookie_file=row.cookie_file,
                    force_browser=False,
                )
                _persist_session(config, row)
                return TaskLoginResult(
                    name=row.name,
                    ok=True,
                    proxy=row.proxy,
                    cookie_file=row.cookie_file,
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "[task] cookie reuse failed name=%s (%s); trying browser login",
                    row.name,
                    exc,
                )

        login_and_transfer_cookies(
            config,
            client,
            proxy=row.proxy,
            save_cookie_file=row.cookie_file,
            force_browser=True,
            login=row.login,
            password=row.password,
        )
        _persist_session(config, row)
        return TaskLoginResult(
            name=row.name,
            ok=True,
            proxy=row.proxy,
            cookie_file=row.cookie_file,
        )
    except Exception as exc:  # noqa: BLE001
        # Last chance: cookie file exists and looks usable offline for warm browsers.
        if cookie_path.exists() and _cookie_file_has_session(cookie_path):
            logger.warning(
                "[task] login API failed name=%s but cookie file has SESSION; "
                "continuing for warm/browser cart: %s",
                row.name,
                exc,
            )
            _persist_session(config, row)
            return TaskLoginResult(
                name=row.name,
                ok=True,
                proxy=row.proxy,
                cookie_file=row.cookie_file,
                error=f"soft-ok with cookies despite: {exc}",
            )
        log_exception(logger, f"task login failed name={row.name}", exc)
        return TaskLoginResult(
            name=row.name,
            ok=False,
            proxy=row.proxy,
            cookie_file=row.cookie_file,
            error=str(exc),
        )


def _persist_session(config: "Config", row: AssignedTask) -> None:
    upsert_session_spec(
        config.sessions_file,
        SessionSpec(
            name=row.name,
            enabled=True,
            proxy=row.proxy,
            cookie_file=row.cookie_file,
        ),
    )


def _cookie_file_has_session(path: Path) -> bool:
    try:
        import json

        data = json.loads(path.read_text(encoding="utf-8"))
        rows = data if isinstance(data, list) else data.get("cookies") or []
        for item in rows:
            if str(item.get("name") or "").upper() == "SESSION" and item.get("value"):
                return True
    except Exception:
        return False
    return False


def ensure_tasks_ready(config: "Config", *, force: bool = False) -> List[TaskLoginResult]:
    """If task.csv exists, assign proxies + login all tasks in parallel."""
    if not task_csv_exists(config):
        return []
    if not Path(config.proxy_csv).exists():
        raise FileNotFoundError(
            f"task.csv found ({config.task_csv}) but proxy.csv is missing: {config.proxy_csv}"
        )
    return run_tasks_parallel(config, force=force)

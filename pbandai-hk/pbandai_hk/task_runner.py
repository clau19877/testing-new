from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, List, Optional

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


def load_and_assign_tasks(config: "Config") -> List[AssignedTask]:
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
    try:
        client.bootstrap()
        # Reuse cookies when valid unless force/FORCE_BROWSER_LOGIN.
        if Path(row.cookie_file).exists() and not force and not config.force_browser_login:
            login_and_transfer_cookies(
                config,
                client,
                proxy=row.proxy,
                save_cookie_file=row.cookie_file,
                force_browser=False,
            )
        else:
            login_and_transfer_cookies(
                config,
                client,
                proxy=row.proxy,
                save_cookie_file=row.cookie_file,
                force_browser=True,
                login=row.login,
                password=row.password,
            )
        upsert_session_spec(
            config.sessions_file,
            SessionSpec(
                name=row.name,
                enabled=True,
                proxy=row.proxy,
                cookie_file=row.cookie_file,
            ),
        )
        return TaskLoginResult(
            name=row.name,
            ok=True,
            proxy=row.proxy,
            cookie_file=row.cookie_file,
        )
    except Exception as exc:  # noqa: BLE001
        log_exception(logger, f"task login failed name={row.name}", exc)
        return TaskLoginResult(
            name=row.name,
            ok=False,
            proxy=row.proxy,
            cookie_file=row.cookie_file,
            error=str(exc),
        )


def ensure_tasks_ready(config: "Config", *, force: bool = False) -> List[TaskLoginResult]:
    """If task.csv exists, assign proxies + login all tasks in parallel."""
    if not task_csv_exists(config):
        return []
    if not Path(config.proxy_csv).exists():
        raise FileNotFoundError(
            f"task.csv found ({config.task_csv}) but proxy.csv is missing: {config.proxy_csv}"
        )
    return run_tasks_parallel(config, force=force)

"""AYCD AutoSolve HTTP client for Riot hCaptcha.

Sends captcha token requests to AYCD AutoSolve
(https://autosolve-api.aycd.io). OneClick / AutoSolve AI / third-party
solvers configured in your AYCD dashboard actually mint the token.

Auth (HTTP API): Bot API Key only.
  GET  https://autosolve-dashboard-api.aycd.io/api/v1/auth/generate-token?apiKey=
  POST https://autosolve-api.aycd.io/api/v1/tasks/create
  GET  https://autosolve-api.aycd.io/api/v1/tasks

Version IDs (from autosolve-http-client 1.0.2; later clients dropped hCap consts):
  HCaptchaCheckbox   = 3
  HCaptchaInvisible  = 4

Env:
  AYCD_API_KEY              — required Bot API Key from AutoSolve dashboard
  AYCD_ACCESS_TOKEN         — optional (AMQP / other bots; unused by HTTP path)
  AYCD_HCAPTCHA_VERSION     — override version int (default 3, or 4 if invisible)
  AYCD_TIMEOUT              — solve timeout seconds (default 300)
  AYCD_DEBUG                — 1 to log AutoSolve HTTP chatter
"""

from __future__ import annotations

import os
import threading
import time
import uuid
from typing import Any

import requests

from captcha import CaptchaSolverError

AUTH_URL = "https://autosolve-dashboard-api.aycd.io/api/v1/auth/generate-token"
API_URL = "https://autosolve-api.aycd.io/api/v1"
TASKS_URL = f"{API_URL}/tasks"
TASKS_CREATE_URL = f"{TASKS_URL}/create"
TASKS_CANCEL_URL = f"{TASKS_URL}/cancel"

Success = "success"
Cancelled = "cancelled"

# Captcha version enum (AutoSolve)
ReCaptchaV2Checkbox = 0
ReCaptchaV2Invisible = 1
ReCaptchaV3 = 2
HCaptchaCheckbox = 3
HCaptchaInvisible = 4
GeeTest = 5
ReCaptchaV3Enterprise = 6
ReCaptchaV2Enterprise = 7
FunCaptcha = 8
GeeTestV4 = 9
TextImageCaptcha = 10


class _Session:
    """Minimal AutoSolve HTTP session (mirrors official autosolve-http-client)."""

    def __init__(self, api_key: str, *, debug: bool = False) -> None:
        self.api_key = api_key
        self.debug = debug
        self._auth_lock = threading.Lock()
        self._tasks_lock = threading.Lock()
        self.token: str | None = None
        self.token_expires_at = 0.0
        self.pending_tasks: set[str] = set()
        self.tasks: dict[str, dict[str, Any]] = {}
        self.tasks_fetch_at = 0.0
        self._http = requests.Session()

    def _log(self, msg: str) -> None:
        if self.debug:
            print(f"  [aycd] {msg}", flush=True)

    def _refresh_auth_token(self) -> None:
        self._log("refreshing auth token…")
        resp = self._http.get(AUTH_URL, params={"apiKey": self.api_key}, timeout=30)
        if not (200 <= resp.status_code < 300):
            raise CaptchaSolverError(
                f"AYCD auth failed HTTP {resp.status_code}: {resp.text[:300]}"
            )
        data = resp.json()
        self.token = data.get("token")
        self.token_expires_at = float(data.get("expiresAt") or 0)
        if not self.token:
            raise CaptchaSolverError(f"AYCD auth returned no token: {data}")
        self._log(f"auth token ok expires_at={self.token_expires_at}")

    def _do(self, method: str, url: str, body: str | None = None) -> requests.Response | None:
        with self._auth_lock:
            if self.token_expires_at <= time.time():
                try:
                    self._refresh_auth_token()
                except Exception as exc:
                    self.token_expires_at = time.time() + 60
                    self._log(f"auth refresh failed: {exc}")
                    raise
        if not self.token:
            raise CaptchaSolverError("AYCD auth token unavailable")
        headers = {"Authorization": f"Token {self.token}"}
        if body is not None:
            headers["Content-Type"] = "application/json"
        return self._http.request(method, url, data=body, headers=headers, timeout=60)

    def _send(self, obj: dict[str, Any], url: str) -> bool:
        import json

        body = json.dumps(obj)
        resp = self._do("POST", url, body)
        if resp is None or not (200 <= resp.status_code < 300):
            detail = resp.text[:400] if resp is not None else "no response"
            code = resp.status_code if resp is not None else "?"
            self._log(f"send failed status={code} body={detail}")
            return False
        return True

    def _fetch_tasks(self) -> int:
        next_fetch_delay = 5
        with self._tasks_lock:
            if self.tasks_fetch_at <= time.time():
                resp = self._do("GET", TASKS_URL, None)
                if resp is None:
                    self._log("failed to fetch tasks")
                elif 200 <= resp.status_code < 300:
                    data = resp.json()
                    if not isinstance(data, list):
                        self._log(f"unexpected tasks payload: {type(data)}")
                        data = []
                    for task in data:
                        tid = task.get("taskId")
                        if tid:
                            self.tasks[str(tid)] = task
                    if len(data) >= 100:
                        next_fetch_delay = 1
                    self.tasks_fetch_at = time.time() + next_fetch_delay
                    self._log(
                        f"fetched {len(data)} task(s); next delay {next_fetch_delay}s"
                    )
                else:
                    self._log(f"fetch tasks HTTP {resp.status_code}")
        return next_fetch_delay

    def cancel_many(self, task_ids: list[str]) -> bool:
        for tid in task_ids:
            self.pending_tasks.discard(tid)
        return self._send({"taskIds": task_ids}, TASKS_CANCEL_URL)

    def solve(self, task_req: dict[str, Any], timeout: float) -> dict[str, Any]:
        if not self._send(task_req, TASKS_CREATE_URL):
            raise CaptchaSolverError(
                "AYCD createTask rejected request "
                "(check AYCD_API_KEY / captcha type still enabled for AutoSolve)"
            )
        task_id = str(task_req["taskId"])
        self.pending_tasks.add(task_id)
        created_at = round(time.time())
        time.sleep(5)
        start = time.time()
        delay = 5
        while task_id in self.pending_tasks:
            try:
                delay = self._fetch_tasks()
                if task_id in self.tasks:
                    task_resp = self.tasks.pop(task_id)
                    self.pending_tasks.discard(task_id)
                    return task_resp
            except Exception as exc:
                self._log(f"poll error: {exc}")
            if time.time() - start > timeout:
                if not self.cancel_many([task_id]):
                    self._log(f"failed to cancel task {task_id}")
                break
            time.sleep(delay)
        return {"taskId": task_id, "createdAt": created_at, "status": Cancelled}


_sessions: dict[str, _Session] = {}
_sessions_lock = threading.Lock()


def _session(api_key: str, *, debug: bool = False) -> _Session:
    with _sessions_lock:
        sess = _sessions.get(api_key)
        if sess is None:
            sess = _Session(api_key, debug=debug)
            _sessions[api_key] = sess
        else:
            sess.debug = debug or sess.debug
        return sess


def _proxy_for_aycd(proxy: str | None) -> str | None:
    if not proxy:
        return None
    try:
        from proxyutil import to_http_url

        return to_http_url(proxy)
    except Exception:
        return proxy.strip() or None


def resolve_hcaptcha_version(*, is_invisible: bool = False) -> int:
    override = (os.getenv("AYCD_HCAPTCHA_VERSION") or "").strip()
    if override:
        try:
            return int(override)
        except ValueError as exc:
            raise CaptchaSolverError(
                f"AYCD_HCAPTCHA_VERSION must be an int, got {override!r}"
            ) from exc
    return HCaptchaInvisible if is_invisible else HCaptchaCheckbox


def solve_aycd(
    api_key: str,
    *,
    website_url: str,
    website_key: str,
    rqdata: str | None = None,
    user_agent: str | None = None,
    proxy: str | None = None,
    is_invisible: bool = False,
    timeout: float | None = None,
) -> str:
    """Create an AutoSolve hCaptcha task and return the solved token."""
    api_key = (api_key or "").strip()
    if not api_key:
        raise CaptchaSolverError(
            "AYCD requires AYCD_API_KEY (AutoSolve Bot API Key from the dashboard)"
        )

    timeout_s = float(
        timeout
        if timeout is not None
        else (os.getenv("AYCD_TIMEOUT") or "300")
    )
    debug = os.getenv("AYCD_DEBUG", "").strip().lower() in ("1", "true", "yes")
    version = resolve_hcaptcha_version(is_invisible=is_invisible)
    proxy_url = _proxy_for_aycd(proxy)
    task_id = f"riot-{uuid.uuid4().hex[:16]}"

    render_parameters: dict[str, str] = {}
    metadata: dict[str, str] = {"enterprise": "true"}
    if rqdata:
        render_parameters["rqdata"] = rqdata
        metadata["rqdata"] = rqdata

    task_req: dict[str, Any] = {
        "taskId": task_id,
        "url": website_url,
        "siteKey": website_key,
        "version": version,
        "proxyRequired": bool(proxy_url),
    }
    if user_agent:
        task_req["userAgent"] = user_agent
    if proxy_url:
        task_req["proxy"] = proxy_url
    if render_parameters:
        task_req["renderParameters"] = render_parameters
    if metadata:
        task_req["metadata"] = metadata

    print(
        f"  AYCD AutoSolve task {task_id} version={version} "
        f"proxy={'yes' if proxy_url else 'no'} rqdata={'yes' if rqdata else 'no'}",
        flush=True,
    )
    print(
        "  → Ensure OneClick AutoSolve is running (Start AutoSolve on solvers) "
        "or a 3rd-party route is active in the AYCD dashboard.",
        flush=True,
    )

    sess = _session(api_key, debug=debug)
    resp = sess.solve(task_req, timeout_s)
    status = (resp.get("status") or "").lower()
    token = resp.get("token") or resp.get("gRecaptchaResponse") or resp.get("response")

    if status == Cancelled or not token:
        raise CaptchaSolverError(
            f"AYCD solve failed/cancelled for {task_id}: "
            f"status={status or '?'} keys={list(resp.keys())}"
        )
    if status and status not in (Success, "ok", "ready", ""):
        # Some responses omit status and only return token
        if not token:
            raise CaptchaSolverError(f"AYCD unexpected status={status}: {resp}")

    print(f"  AYCD token received ({len(token)} chars) status={status or 'ok'}", flush=True)
    return str(token)

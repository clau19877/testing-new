from __future__ import annotations

import json
import os
import tempfile
import time
import zipfile
from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, List, Optional

from .logging_utils import get_logger, log_exception
from .proxy_util import parse_proxy, redact_proxy

if TYPE_CHECKING:
    from .api import PBandaiHkClient
    from .config import Config

logger = get_logger("session_login")


def login_and_transfer_cookies(
    config: "Config",
    client: "PBandaiHkClient",
    *,
    proxy: str | None = None,
    save_cookie_file: str | Path | None = None,
    force_browser: bool = False,
    login: str = "",
    password: str = "",
    wait_seconds: int = 45,
) -> List[Dict[str, Any]]:
    """Open a browser for login, then copy cookies into the API session.

    If login+password are provided, fills the HK login form automatically.
    Otherwise waits for manual login (Enter in the console).
    """
    cookie_file = (
        save_cookie_file
        or getattr(config, "cookie_file", "")
        or os.getenv("COOKIE_FILE", "")
    )
    force_browser = force_browser or bool(getattr(config, "force_browser_login", False))

    # Cookie-file path skips Selenium when present (unless force_browser).
    if cookie_file and Path(str(cookie_file)).exists() and not force_browser:
        from .sessions import apply_cookies_to_client, load_cookies_file

        cookies = load_cookies_file(Path(str(cookie_file)))
        apply_cookies_to_client(client, cookies)
        client.refresh_csrf()
        summary = client.cart_summary()
        print(f"[{client.name}] Session loaded from cookie file. cart summary={summary}")
        logger.info(
            "loaded cookies session=%s file=%s summary=%s",
            client.name,
            cookie_file,
            summary,
        )
        return cookies

    effective_proxy = (
        proxy
        if proxy is not None
        else (getattr(config, "proxy_url", "") or client.proxy or "")
    )
    driver = _create_webdriver(config, proxy=effective_proxy)
    try:
        driver.get(config.login_url)
        if login and password:
            print(
                f"[{client.name}] Auto-login via form "
                f"(proxy={redact_proxy(effective_proxy) or '-'})"
            )
            _autofill_login(driver, login=login, password=password, timeout=wait_seconds)
            _wait_for_session_cookie(driver, timeout=wait_seconds)
        else:
            print()
            print("=================================================")
            print(f" Browser login ready for session: {client.name}")
            print(" (ignore Chrome ERROR spam)")
            if effective_proxy:
                print(f" Proxy: {redact_proxy(effective_proxy)}")
            print("=================================================")
            print("1) In the Chrome/Edge window, log in to P-Bandai HK")
            print("2) Confirm you are logged in (account/cart icon visible)")
            print("3) Come back to THIS black console window")
            print("4) Press Enter here to continue")
            print("=================================================")
            print()
            input(f">>> [{client.name}] Press Enter after login is complete... ")

        cookies = _driver_cookies(driver)
        _apply_driver_cookies(client, cookies)
        client.refresh_csrf()
        summary = client.cart_summary()
        print(
            f"[{client.name}] Session transferred ({len(cookies)} cookies). "
            f"cart summary={summary}"
        )
        logger.info(
            "transferred cookies session=%s count=%s summary=%s",
            client.name,
            len(cookies),
            summary,
        )
        if save_cookie_file:
            path = Path(str(save_cookie_file))
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(
                json.dumps(cookies, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            print(f"[{client.name}] Saved cookies -> {path}")
        return cookies
    finally:
        try:
            driver.quit()
        except Exception as exc:  # noqa: BLE001
            log_exception(logger, "driver.quit failed", exc)


def _autofill_login(driver: Any, *, login: str, password: str, timeout: int = 60) -> None:
    from selenium.webdriver.common.by import By
    from selenium.webdriver.common.keys import Keys

    email_css = (
        "input#e-mail_address, input#loginId, input[name='mail'], input[name='memberId'], "
        "input[name='email'], input[type='email'], input[autocomplete='email'], "
        "input[autocomplete='username'], "
        "input[placeholder*='mail'], input[placeholder*='Mail'], "
        "input[placeholder*='E-mail'], input[placeholder*='Email'], "
        "input[placeholder*='ID'], input[placeholder*='Id']"
    )
    password_css = (
        "input#password, input[name='password'], input[type='password'], "
        "input[autocomplete='current-password']"
    )
    submit_selectors = [
        "button[type='submit']",
        "input[type='submit']",
        "button.login",
        "button[class*='login']",
        "button[class*='Login']",
        "form button",
        "//button[contains(., 'Log') or contains(., 'Sign') or contains(., 'ログイン')]",
    ]

    print("  waiting for login form (SPA)...")
    email_el = _wait_visible(driver, By.CSS_SELECTOR, email_css, timeout=timeout)
    pass_el = _wait_visible(driver, By.CSS_SELECTOR, password_css, timeout=timeout)
    _set_input(driver, email_el, login)
    _set_input(driver, pass_el, password)
    print("  submitting login...")

    submitted = False
    for selector in submit_selectors:
        try:
            by = By.XPATH if selector.startswith("//") else By.CSS_SELECTOR
            btn = driver.find_element(by, selector)
            if btn.is_displayed() and btn.is_enabled():
                try:
                    btn.click()
                except Exception:  # noqa: BLE001
                    driver.execute_script("arguments[0].click();", btn)
                submitted = True
                break
        except Exception:  # noqa: BLE001
            continue
    if not submitted:
        pass_el.send_keys(Keys.ENTER)

    # Give the SPA a moment to process the login POST.
    time.sleep(2.0)


def _set_input(driver: Any, element: Any, value: str) -> None:
    try:
        element.clear()
    except Exception:  # noqa: BLE001
        pass
    try:
        element.click()
    except Exception:  # noqa: BLE001
        pass
    element.send_keys(value)
    # Vue/React controlled inputs sometimes ignore send_keys; force value + events.
    try:
        current = element.get_attribute("value") or ""
        if current != value:
            driver.execute_script(
                """
                const el = arguments[0];
                const val = arguments[1];
                el.focus();
                el.value = val;
                el.dispatchEvent(new Event('input', { bubbles: true }));
                el.dispatchEvent(new Event('change', { bubbles: true }));
                """,
                element,
                value,
            )
    except Exception:  # noqa: BLE001
        pass


def _wait_visible(driver: Any, by: Any, selector: str, timeout: int = 60) -> Any:
    deadline = time.time() + timeout
    last_exc: Optional[Exception] = None
    while time.time() < deadline:
        try:
            elements = driver.find_elements(by, selector)
            for el in elements:
                try:
                    if el.is_displayed() and el.is_enabled():
                        return el
                except Exception as exc:  # noqa: BLE001
                    last_exc = exc
                    continue
        except Exception as exc:  # noqa: BLE001
            last_exc = exc
        time.sleep(0.25)
    raise RuntimeError(
        "Could not find login form fields on the page within "
        f"{timeout}s (selector={selector!r}). "
        "Check proxy/captcha, or login manually with menu [7]."
    ) from last_exc


def _wait_for_session_cookie(driver: Any, timeout: int = 60) -> None:
    print("  waiting for login to complete...")
    deadline = time.time() + timeout
    saw_session = False
    left_login = False
    while time.time() < deadline:
        try:
            names = {c.get("name", "").upper() for c in driver.get_cookies()}
            if "SESSION" in names:
                saw_session = True
        except Exception:  # noqa: BLE001
            pass
        try:
            current = (driver.current_url or "").lower()
            if "/login" not in current and "p-bandai.com" in current:
                left_login = True
        except Exception:  # noqa: BLE001
            pass
        # Strong success: left /login with a SESSION cookie.
        if saw_session and left_login:
            print("  login redirect detected")
            return
        # Soft success after enough time with SESSION only.
        if saw_session and time.time() + 15 > deadline:
            print("  SESSION cookie found")
            return
        time.sleep(0.5)
    if saw_session:
        print("  SESSION cookie found (still on login page; verify credentials)")
        return
    raise RuntimeError(
        "Login did not produce a SESSION cookie in time. "
        "Check credentials / proxy / captcha, then retry."
    )


def _driver_cookies(driver: Any) -> List[Dict[str, Any]]:
    cookies: List[Dict[str, Any]] = []
    for cookie in driver.get_cookies():
        cookies.append(
            {
                "name": cookie.get("name"),
                "value": cookie.get("value"),
                "domain": cookie.get("domain"),
                "path": cookie.get("path") or "/",
            }
        )
    return cookies


def _apply_driver_cookies(client: "PBandaiHkClient", cookies: List[Dict[str, Any]]) -> None:
    for cookie in cookies:
        if not cookie.get("name"):
            continue
        client.session.cookies.set(
            cookie["name"],
            cookie["value"],
            domain=cookie.get("domain"),
            path=cookie.get("path") or "/",
        )


def _create_webdriver(config: "Config", proxy: str = "") -> Any:
    browser = (getattr(config, "browser", None) or os.getenv("BROWSER") or "auto").lower()
    errors: list[str] = []

    if browser in {"chrome", "edge"}:
        order = [browser]
    else:
        order = ["chrome", "edge"]

    for name in order:
        try:
            if name == "chrome":
                driver = _create_chrome(config, proxy=proxy)
            else:
                driver = _create_edge(config, proxy=proxy)
            logger.info("started browser=%s proxy=%s", name, redact_proxy(proxy) or "-")
            print(f"Using browser: {name}")
            return driver
        except Exception as exc:  # noqa: BLE001
            msg = f"{name} launch failed: {exc}"
            errors.append(msg)
            log_exception(logger, msg, exc)

    joined = "\n".join(f"- {e}" for e in errors)
    raise RuntimeError(
        "Could not start a browser for login.\n"
        "Install Google Chrome and/or Microsoft Edge, then retry.\n"
        "If this keeps failing, set COOKIE_FILE / sessions cookie_file instead.\n"
        f"Details:\n{joined}"
    )


def _common_options(options: Any, config: "Config", proxy: str = "") -> Any:
    if config.background_mode:
        options.add_argument("--headless=new")
    options.add_argument("--start-maximized")
    options.add_argument("--disable-gpu")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--log-level=3")
    options.add_argument("--disable-notifications")
    options.add_argument("--disable-background-networking")
    options.add_argument(
        "--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/134.0.0.0 Safari/537.36"
    )
    try:
        options.add_experimental_option(
            "excludeSwitches",
            ["enable-automation", "enable-logging"],
        )
        options.add_experimental_option("useAutomationExtension", False)
    except Exception:  # noqa: BLE001
        pass
    options.add_argument("--disable-blink-features=AutomationControlled")
    _apply_proxy_options(options, proxy)
    return options


def _apply_proxy_options(options: Any, proxy: str) -> Optional[str]:
    """Apply proxy to Chromium options.

    For HTTP(S) proxies with username/password, start a local auth bridge so
    Chrome never shows the native proxy password popup (extensions often fail
    on modern Chrome).
    """
    parsed = parse_proxy(proxy)
    if not parsed:
        return None

    if not parsed.has_auth:
        options.add_argument(f"--proxy-server={parsed.server}")
        return None

    if parsed.scheme.startswith("socks"):
        # Try extension path for socks; Chromium has no native socks auth.
        ext_dir = _install_proxy_auth_extension(
            options,
            parsed.scheme,
            parsed.host,
            parsed.port,
            parsed.username or "",
            parsed.password or "",
        )
        options.add_argument(f"--proxy-server={parsed.server}")
        logger.warning(
            "SOCKS proxy auth via browser is best-effort; "
            "API requests still use full proxy auth."
        )
        return ext_dir

    # HTTP(S) + auth: local bridge (no popup)
    try:
        from .local_proxy import start_local_auth_proxy

        bridge = start_local_auth_proxy(parsed)
        options.add_argument(f"--proxy-server={bridge.local_url}")
        print(f"  proxy auth bridge: {bridge.local_url} -> {redact_proxy(proxy)}")
        logger.info(
            "using local proxy auth bridge %s for %s",
            bridge.local_url,
            redact_proxy(proxy),
        )
        return None
    except Exception as exc:  # noqa: BLE001
        log_exception(logger, "local proxy auth bridge failed; trying extension", exc)

    ext_dir = _install_proxy_auth_extension(
        options,
        parsed.scheme,
        parsed.host,
        parsed.port,
        parsed.username or "",
        parsed.password or "",
    )
    options.add_argument(f"--proxy-server={parsed.scheme}://{parsed.host}:{parsed.port}")
    return ext_dir


def _install_proxy_auth_extension(
    options: Any,
    scheme: str,
    host: str,
    port: int,
    username: str,
    password: str,
) -> str:
    """Best-effort Chrome extension install for proxy auth (fallback path)."""
    ext_dir = _write_proxy_auth_extension(scheme, host, port, username, password)
    # Chrome 137+ disables --load-extension unless this feature flag is off.
    options.add_argument("--disable-features=DisableLoadExtensionCommandLineSwitch")
    options.add_argument(f"--load-extension={ext_dir}")
    try:
        # Packed zip via add_extension is more reliable than unpacked on some builds.
        zip_path = Path(ext_dir) / "proxy_auth.zip"
        if zip_path.exists():
            options.add_extension(str(zip_path))
    except Exception as exc:  # noqa: BLE001
        logger.debug("add_extension failed: %s", exc)
    return ext_dir


def _write_proxy_auth_extension(
    scheme: str,
    host: str,
    port: int,
    username: str,
    password: str,
) -> str:
    """Create a temporary Chrome extension for proxy credentials."""
    # Prefer MV3 declarativeNetRequest-unrelated auth provider where available;
    # keep MV2 blocking listener for older Chromium / Edge builds.
    manifest_v2 = {
        "version": "1.0.0",
        "manifest_version": 2,
        "name": "Proxy Auth",
        "permissions": [
            "proxy",
            "tabs",
            "unlimitedStorage",
            "storage",
            "<all_urls>",
            "webRequest",
            "webRequestAuthProvider",
        ],
        "background": {"scripts": ["background.js"]},
    }
    background = f"""
var config = {{
  mode: "fixed_servers",
  rules: {{
    singleProxy: {{
      scheme: "{scheme}",
      host: "{host}",
      port: {int(port)}
    }},
    bypassList: ["localhost", "127.0.0.1"]
  }}
}};
chrome.proxy.settings.set({{value: config, scope: "regular"}}, function(){{}});
function callbackFn(details) {{
  return {{
    authCredentials: {{
      username: {json.dumps(username)},
      password: {json.dumps(password)}
    }}
  }};
}}
chrome.webRequest.onAuthRequired.addListener(
  callbackFn,
  {{urls: ["<all_urls>"]}},
  ["blocking"]
);
"""
    temp_dir = Path(tempfile.mkdtemp(prefix="pbandai_proxy_ext_"))
    (temp_dir / "manifest.json").write_text(json.dumps(manifest_v2), encoding="utf-8")
    (temp_dir / "background.js").write_text(background, encoding="utf-8")
    zip_path = temp_dir / "proxy_auth.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.write(temp_dir / "manifest.json", "manifest.json")
        zf.write(temp_dir / "background.js", "background.js")
    return str(temp_dir)


def _create_chrome(config: "Config", proxy: str = "") -> Any:
    from selenium import webdriver
    from selenium.webdriver.chrome.options import Options
    from selenium.webdriver.chrome.service import Service

    options = _common_options(Options(), config, proxy=proxy)
    try:
        return webdriver.Chrome(options=options)
    except Exception as first:  # noqa: BLE001
        logger.warning("Selenium Manager Chrome failed: %s", first)

    from webdriver_manager.chrome import ChromeDriverManager

    raw_path = ChromeDriverManager().install()
    driver_path = _resolve_driver_binary(raw_path, "chromedriver")
    logger.info("ChromeDriverManager path raw=%s resolved=%s", raw_path, driver_path)
    return webdriver.Chrome(service=Service(executable_path=driver_path), options=options)


def _create_edge(config: "Config", proxy: str = "") -> Any:
    from selenium import webdriver
    from selenium.webdriver.edge.options import Options
    from selenium.webdriver.edge.service import Service

    options = _common_options(Options(), config, proxy=proxy)
    try:
        return webdriver.Edge(options=options)
    except Exception as first:  # noqa: BLE001
        logger.warning("Selenium Manager Edge failed: %s", first)

    from webdriver_manager.microsoft import EdgeChromiumDriverManager

    raw_path = EdgeChromiumDriverManager().install()
    driver_path = _resolve_driver_binary(raw_path, "msedgedriver")
    logger.info("EdgeDriverManager path raw=%s resolved=%s", raw_path, driver_path)
    return webdriver.Edge(service=Service(executable_path=driver_path), options=options)


def _resolve_driver_binary(path: str, base_name: str) -> str:
    """Fix webdriver-manager returning THIRD_PARTY_NOTICES instead of the exe."""
    p = Path(path)
    wanted = f"{base_name}.exe" if os.name == "nt" else base_name

    def is_driver(candidate: Path) -> bool:
        if not candidate.is_file():
            return False
        name = candidate.name.lower()
        if "third_party_notices" in name:
            return False
        if os.name == "nt":
            return name == wanted.lower()
        return name == base_name and os.access(candidate, os.X_OK)

    if is_driver(p):
        return str(p)

    search_roots = [p.parent, p.parent.parent if p.parent else p]
    for root in search_roots:
        if not root or not root.exists():
            continue
        for candidate in root.rglob(wanted):
            if is_driver(candidate):
                return str(candidate)

    for root in search_roots:
        if not root or not root.exists():
            continue
        for candidate in root.rglob(f"{base_name}*"):
            if is_driver(candidate):
                return str(candidate)

    raise RuntimeError(
        f"Could not resolve a valid {wanted} from webdriver-manager path: {path}\n"
        "This usually causes WinError 193 on Windows. Delete the cache folder "
        f"{Path.home() / '.wdm'} and retry, or install Chrome/Edge and use Selenium Manager."
    )

from __future__ import annotations

import json
import os
import tempfile
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
) -> List[Dict[str, Any]]:
    """Open a browser for manual login, then copy cookies into the API session."""
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
        print(f"Session loaded from cookie file. cart summary={summary}")
        logger.info("loaded cookies from %s summary=%s", cookie_file, summary)
        return cookies

    effective_proxy = (
        proxy
        if proxy is not None
        else (getattr(config, "proxy_url", "") or client.proxy or "")
    )
    driver = _create_webdriver(config, proxy=effective_proxy)
    try:
        driver.get(config.login_url)
        print()
        print("=================================================")
        print(" Browser login is ready (ignore Chrome ERROR spam)")
        if effective_proxy:
            print(f" Proxy: {redact_proxy(effective_proxy)}")
        print("=================================================")
        print("1) In the Chrome/Edge window, log in to P-Bandai HK")
        print("2) Confirm you are logged in (account/cart icon visible)")
        print("3) Come back to THIS black console window")
        print("4) Press Enter here to continue")
        print("=================================================")
        print()
        input(">>> Press Enter after login is complete... ")
        cookies = _driver_cookies(driver)
        _apply_driver_cookies(client, cookies)
        client.refresh_csrf()
        summary = client.cart_summary()
        print(f"Session transferred ({len(cookies)} cookies). cart summary={summary}")
        logger.info("transferred %s cookies summary=%s", len(cookies), summary)
        if save_cookie_file:
            path = Path(str(save_cookie_file))
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(cookies, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            print(f"Saved cookies -> {path}")
        return cookies
    finally:
        try:
            driver.quit()
        except Exception as exc:  # noqa: BLE001
            log_exception(logger, "driver.quit failed", exc)


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
    """Apply proxy to Chromium options. Returns temp extension dir if created."""
    parsed = parse_proxy(proxy)
    if not parsed:
        return None
    # socks / http without auth
    if not parsed.has_auth:
        options.add_argument(f"--proxy-server={parsed.server}")
        return None
    if parsed.scheme.startswith("socks"):
        # Chromium cannot natively do socks auth via --proxy-server userinfo.
        options.add_argument(f"--proxy-server={parsed.server}")
        logger.warning(
            "SOCKS proxy auth may be ignored by the browser; "
            "API requests still use full proxy auth."
        )
        return None
    # HTTP(S) proxy with auth via temporary extension
    ext_dir = _write_proxy_auth_extension(parsed.scheme, parsed.host, parsed.port, parsed.username or "", parsed.password or "")
    options.add_argument(f"--load-extension={ext_dir}")
    options.add_argument(f"--proxy-server={parsed.scheme}://{parsed.host}:{parsed.port}")
    return ext_dir


def _write_proxy_auth_extension(
    scheme: str,
    host: str,
    port: int,
    username: str,
    password: str,
) -> str:
    """Create a temporary Chrome extension for proxy credentials."""
    manifest = {
        "version": "1.0.0",
        "manifest_version": 3,
        "name": "Proxy Auth",
        "permissions": ["proxy", "storage", "webRequest", "webRequestAuthProvider"],
        "host_permissions": ["<all_urls>"],
        "background": {"service_worker": "background.js"},
    }
    # MV3 service worker proxy auth support varies; use MV2-compatible packed style for broader Chrome.
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
    bypassList: ["localhost"]
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
    # Also keep a zip for debugging if needed
    zip_path = temp_dir / "proxy_auth.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.write(temp_dir / "manifest.json", "manifest.json")
        zf.write(temp_dir / "background.js", "background.js")
    _ = manifest  # reserved if we switch to MV3 later
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

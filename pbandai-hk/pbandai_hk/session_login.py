from __future__ import annotations

import os
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .logging_utils import get_logger, log_exception

if TYPE_CHECKING:
    from .api import PBandaiHkClient
    from .config import Config

logger = get_logger("session_login")


def login_and_transfer_cookies(config: "Config", client: "PBandaiHkClient") -> None:
    """Open a browser for manual login, then copy cookies into the API session."""
    # Optional cookie-file path skips Selenium entirely.
    cookie_file = getattr(config, "cookie_file", "") or os.getenv("COOKIE_FILE", "")
    if cookie_file:
        _load_cookie_file(Path(cookie_file), client)
        client.refresh_csrf()
        summary = client.cart_summary()
        print(f"Session loaded from cookie file. cart summary={summary}")
        logger.info("loaded cookies from %s summary=%s", cookie_file, summary)
        return

    driver = _create_webdriver(config)
    try:
        driver.get(config.login_url)
        print()
        print("=================================================")
        print(" Browser login is ready (ignore Chrome ERROR spam)")
        print("=================================================")
        print("1) In the Chrome/Edge window, log in to P-Bandai HK")
        print("2) Confirm you are logged in (account/cart icon visible)")
        print("3) Come back to THIS black console window")
        print("4) Press Enter here to continue")
        print("=================================================")
        print()
        input(">>> Press Enter after login is complete... ")
        count = 0
        for cookie in driver.get_cookies():
            client.session.cookies.set(
                cookie["name"],
                cookie["value"],
                domain=cookie.get("domain"),
                path=cookie.get("path") or "/",
            )
            count += 1
        client.refresh_csrf()
        summary = client.cart_summary()
        print(f"Session transferred ({count} cookies). cart summary={summary}")
        logger.info("transferred %s cookies summary=%s", count, summary)
    finally:
        try:
            driver.quit()
        except Exception as exc:  # noqa: BLE001
            log_exception(logger, "driver.quit failed", exc)


def _create_webdriver(config: "Config") -> Any:
    browser = (getattr(config, "browser", None) or os.getenv("BROWSER") or "auto").lower()
    errors: list[str] = []

    order: list[str]
    if browser in {"chrome", "edge"}:
        order = [browser]
    else:
        # Edge is often more reliable on Windows corporate images.
        order = ["chrome", "edge"] if os.name != "nt" else ["chrome", "edge"]

    for name in order:
        try:
            if name == "chrome":
                driver = _create_chrome(config)
            else:
                driver = _create_edge(config)
            logger.info("started browser=%s", name)
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
        "If this keeps failing, set COOKIE_FILE to a exported cookie file instead.\n"
        f"Details:\n{joined}"
    )


def _common_options(options: Any, config: "Config") -> Any:
    if config.background_mode:
        options.add_argument("--headless=new")
    options.add_argument("--start-maximized")
    options.add_argument("--disable-gpu")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    # Reduce noisy Chrome/DevTools console spam on Windows.
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
    return options


def _create_chrome(config: "Config") -> Any:
    from selenium import webdriver
    from selenium.webdriver.chrome.options import Options
    from selenium.webdriver.chrome.service import Service

    options = _common_options(Options(), config)

    # 1) Preferred: Selenium Manager (built into Selenium 4.6+)
    try:
        return webdriver.Chrome(options=options)
    except Exception as first:  # noqa: BLE001
        logger.warning("Selenium Manager Chrome failed: %s", first)

    # 2) Fallback: webdriver-manager with Win32 path correction
    from webdriver_manager.chrome import ChromeDriverManager

    raw_path = ChromeDriverManager().install()
    driver_path = _resolve_driver_binary(raw_path, "chromedriver")
    logger.info("ChromeDriverManager path raw=%s resolved=%s", raw_path, driver_path)
    return webdriver.Chrome(service=Service(executable_path=driver_path), options=options)


def _create_edge(config: "Config") -> Any:
    from selenium import webdriver
    from selenium.webdriver.edge.options import Options
    from selenium.webdriver.edge.service import Service

    options = _common_options(Options(), config)
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
        exact = list(root.rglob(wanted))
        for candidate in exact:
            if is_driver(candidate):
                return str(candidate)

    # Last resort: any file named chromedriver / msedgedriver nearby
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


def _load_cookie_file(path: Path, client: "PBandaiHkClient") -> None:
    if not path.exists():
        raise FileNotFoundError(f"COOKIE_FILE not found: {path}")
    text = path.read_text(encoding="utf-8")
    # JSON list support: [{"name":"...","value":"...","domain":"...","path":"/"}]
    if text.lstrip().startswith("["):
        import json

        cookies = json.loads(text)
        for cookie in cookies:
            client.session.cookies.set(
                cookie["name"],
                cookie["value"],
                domain=cookie.get("domain"),
                path=cookie.get("path") or "/",
            )
        return

    # Netscape cookie file
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split("\t")
        if len(parts) < 7:
            continue
        domain, _flag, cookie_path, _secure, _expiry, name, value = parts[:7]
        client.session.cookies.set(name, value, domain=domain, path=cookie_path or "/")

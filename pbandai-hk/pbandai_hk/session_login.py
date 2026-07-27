from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .api import PBandaiHkClient
    from .config import Config


def login_and_transfer_cookies(config: "Config", client: "PBandaiHkClient") -> None:
    """Open a browser for manual login, then copy cookies into the API session."""
    from selenium import webdriver
    from selenium.webdriver.chrome.options import Options
    from selenium.webdriver.chrome.service import Service
    from webdriver_manager.chrome import ChromeDriverManager

    chrome_options = Options()
    if config.background_mode:
        # Headless login is usually impractical; keep option for advanced setups.
        chrome_options.add_argument("--headless=new")
    chrome_options.add_argument("--start-maximized")
    chrome_options.add_argument(
        "--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/134.0.0.0 Safari/537.36"
    )
    chrome_options.add_experimental_option("excludeSwitches", ["enable-automation"])
    chrome_options.add_experimental_option("useAutomationExtension", False)
    chrome_options.add_argument("--disable-blink-features=AutomationControlled")

    driver = webdriver.Chrome(
        service=Service(ChromeDriverManager().install()),
        options=chrome_options,
    )
    try:
        driver.get(config.login_url)
        input(
            "Log in to P-Bandai HK in the opened browser, then return here and press Enter...\n"
        )
        for cookie in driver.get_cookies():
            client.session.cookies.set(
                cookie["name"],
                cookie["value"],
                domain=cookie.get("domain"),
                path=cookie.get("path") or "/",
            )
        client.refresh_csrf()
        summary = client.cart_summary()
        print(f"Session transferred. cart summary={summary}")
    finally:
        driver.quit()

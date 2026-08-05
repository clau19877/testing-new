import os
from dataclasses import dataclass, field
from typing import List

from dotenv import load_dotenv

from .discord_util import normalize_webhook, resolve_discord_webhook
from .links import extract_product_codes


def _split_csv(value: str | None) -> List[str]:
    if not value:
        return []
    return [part.strip() for part in value.split(",") if part.strip()]


def _as_bool(value: str | None, default: bool = False) -> bool:
    if value is None or value == "":
        return default
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


@dataclass
class Config:
    area_code: str = "hk"
    base_url: str = "https://p-bandai.com"
    accept_language: str = "en"
    search_keywords: List[str] = field(default_factory=list)
    product_links: List[str] = field(default_factory=list)
    product_codes: List[str] = field(default_factory=list)
    precheck_list: List[str] = field(default_factory=list)
    target_list: List[str] = field(default_factory=list)
    sale_statuses: List[str] = field(default_factory=lambda: ["On", "Waiting"])
    enable_add_to_cart: bool = False
    cart_qty: int = 1
    add_cart_retry_count: int = 5
    background_mode: bool = False
    login_url: str = "https://p-bandai.com/hk/login"
    browser: str = "auto"
    cookie_file: str = ""
    proxy_url: str = ""
    sessions_file: str = "sessions.json"
    cart_mode: str = "first"  # first | all | round_robin
    cart_method: str = "auto"  # auto | api | browser | warm
    # Pre-open browsers on product page before drop (survives HTML crashes).
    # auto cart_method will prefer warm pool when PREWARM_BROWSERS=1.
    prewarm_browsers: bool = False
    # Soft "clicked verify on site" without cart count increase = failure.
    require_cart_increase: bool = True
    # When waiting for orderStartDate, wake this many seconds early then fast-poll.
    drop_lead_seconds: int = 30
    # Parallel cart attempts when CART_MODE=all (important for low-stock drops).
    cart_parallel: bool = True
    # Guest click farm (no login): N browsers click PLACE PRE-ORDER on schedule.
    click_farm: bool = True
    browser_instances: int = 20
    # Wall-clock second within each minute to ATC (0 = :00). Set -1 to use interval instead.
    click_at_second: int = 0
    click_interval_seconds: float = 5.0
    # Keep all instances running after a cart success (Discord still notified).
    stop_on_first_cart: bool = False
    # Stagger Chrome launch + PDP (seconds * instance index + jitter) to cut PNA/WAF.
    open_stagger_seconds: float = 1.5
    # Retries when first PDP visit returns 500 / "page not available".
    open_pdp_retries: int = 5
    open_pdp_retry_wait: float = 3.0
    # While waiting for :00, hard-refresh if UI shows OUT OF STOCK / PNA (soft/stale).
    oos_refresh_seconds: float = 18.0
    # Cap how many instances may navigate PDP/home at once (cuts heal stampede).
    pdp_max_concurrent: int = 3
    # Human-like idle (scroll / blank click) while waiting; 0 disables.
    idle_activity_seconds: float = 8.0
    # Per-instance Chrome profile dir + UA/viewport/locale (cuts identical fingerprints).
    unique_browser_profiles: bool = True
    # Seconds to linger on /{area}/ home (scroll) before first PDP open.
    home_warmup_seconds: float = 3.0
    # Optional logged-in cookie files (Cookie-Editor JSON), one per instance,
    # round-robined. Bandai only creates the checkout hold for signed-in members,
    # so portable /orderdetails?confirmationCartToken links need these.
    farm_cookie_files: List[str] = field(default_factory=list)
    discord_webhook_url: str = ""
    task_csv: str = "task.csv"
    proxy_csv: str = "proxy.csv"
    proxy_assign_mode: str = "random"  # random | unique
    task_parallel_workers: int = 0  # 0 = one worker per task
    schedule_mode: bool = False
    execute_times: List[str] = field(default_factory=list)
    schedule_polling_period: int = 60
    retry_wait: int = 60
    search_limit: int = 40
    search_max_pages: int = 3
    email_user: str = ""
    email_password: str = ""
    receiver_email: str = ""
    smtp_server: str = "smtp.gmail.com"
    smtp_port: int = 587
    log_file: str = "logs/pbandai_hk.log"
    log_level: str = "INFO"
    force_browser_login: bool = False

    @classmethod
    def from_env(cls, dotenv_path: str | None = None) -> "Config":
        # override=True so a local .env wins over empty shell exports.
        load_dotenv(dotenv_path, override=True)
        precheck = _split_csv(os.getenv("PRECHECK_LIST"))
        target = _split_csv(os.getenv("TARGET_LIST"))
        product_links = _split_csv(os.getenv("PRODUCT_LINKS"))
        explicit_codes = _split_csv(os.getenv("PRODUCT_CODES"))
        product_codes = extract_product_codes([*product_links, *explicit_codes])
        # Prefer .env value; fall back to discord_webhook.txt / aliases.
        env_hook = normalize_webhook(
            os.getenv("DISCORD_WEBHOOK_URL")
            or os.getenv("DISCORD_WEBHOOK")
            or os.getenv("DISCORD_HOOK")
        )
        hook, hook_src = resolve_discord_webhook(config_value=env_hook, reload_env=False)
        if hook and hook_src and hook_src != "config":
            # Surface non-.env sources for prepare() logging.
            os.environ["DISCORD_WEBHOOK_SOURCE"] = hook_src
        elif hook:
            os.environ["DISCORD_WEBHOOK_SOURCE"] = "env"
        else:
            os.environ.pop("DISCORD_WEBHOOK_SOURCE", None)
        return cls(
            area_code=(os.getenv("AREA_CODE") or "hk").lower(),
            base_url=(os.getenv("BASE_URL") or "https://p-bandai.com").rstrip("/"),
            accept_language=os.getenv("ACCEPT_LANGUAGE") or "en",
            search_keywords=_split_csv(os.getenv("SEARCH_KEYWORDS")),
            product_links=product_links,
            product_codes=product_codes,
            precheck_list=precheck,
            target_list=target or list(precheck),
            sale_statuses=_split_csv(os.getenv("SALE_STATUSES")) or ["On", "Waiting"],
            enable_add_to_cart=_as_bool(os.getenv("ENABLE_ADD_TO_CART"), False),
            cart_qty=int(os.getenv("CART_QTY") or "1"),
            add_cart_retry_count=int(os.getenv("ADD_CART_RETRY_COUNT") or "5"),
            background_mode=_as_bool(os.getenv("BACKGROUND_MODE"), False),
            login_url=os.getenv("LOGIN_URL") or "https://p-bandai.com/hk/login",
            browser=(os.getenv("BROWSER") or "auto").lower(),
            cookie_file=os.getenv("COOKIE_FILE") or "",
            proxy_url=(os.getenv("PROXY_URL") or "").strip(),
            sessions_file=os.getenv("SESSIONS_FILE") or "sessions.json",
            cart_mode=(os.getenv("CART_MODE") or "first").strip().lower(),
            cart_method=(os.getenv("CART_METHOD") or "auto").strip().lower(),
            prewarm_browsers=_as_bool(os.getenv("PREWARM_BROWSERS"), False),
            require_cart_increase=_as_bool(os.getenv("REQUIRE_CART_INCREASE"), True),
            drop_lead_seconds=int(os.getenv("DROP_LEAD_SECONDS") or "30"),
            cart_parallel=_as_bool(os.getenv("CART_PARALLEL"), True),
            click_farm=_as_bool(os.getenv("CLICK_FARM"), True),
            browser_instances=int(os.getenv("BROWSER_INSTANCES") or "20"),
            click_at_second=int(os.getenv("CLICK_AT_SECOND") or "0"),
            click_interval_seconds=float(os.getenv("CLICK_INTERVAL_SECONDS") or "5"),
            stop_on_first_cart=_as_bool(os.getenv("STOP_ON_FIRST_CART"), False),
            open_stagger_seconds=float(os.getenv("OPEN_STAGGER_SECONDS") or "1.5"),
            open_pdp_retries=int(os.getenv("OPEN_PDP_RETRIES") or "5"),
            open_pdp_retry_wait=float(os.getenv("OPEN_PDP_RETRY_WAIT") or "3"),
            oos_refresh_seconds=float(os.getenv("OOS_REFRESH_SECONDS") or "18"),
            pdp_max_concurrent=int(os.getenv("PDP_MAX_CONCURRENT") or "3"),
            idle_activity_seconds=float(os.getenv("IDLE_ACTIVITY_SECONDS") or "8"),
            unique_browser_profiles=_as_bool(os.getenv("UNIQUE_BROWSER_PROFILES"), True),
            home_warmup_seconds=float(os.getenv("HOME_WARMUP_SECONDS") or "3"),
            farm_cookie_files=_split_csv(
                os.getenv("FARM_COOKIE_FILES") or os.getenv("COOKIE_FILE")
            ),
            discord_webhook_url=hook,
            task_csv=os.getenv("TASK_CSV") or "task.csv",
            proxy_csv=os.getenv("PROXY_CSV") or "proxy.csv",
            proxy_assign_mode=(os.getenv("PROXY_ASSIGN_MODE") or "random").strip().lower(),
            task_parallel_workers=int(os.getenv("TASK_PARALLEL_WORKERS") or "0"),
            schedule_mode=_as_bool(os.getenv("SCHEDULE_MODE"), False),
            execute_times=_split_csv(os.getenv("EXECUTE_TIME")),
            schedule_polling_period=int(os.getenv("EXECUTE_SCHEDULE_POLLING_PERIOD") or "60"),
            retry_wait=int(os.getenv("RETRY_WAIT") or "60"),
            search_limit=int(os.getenv("SEARCH_LIMIT") or "40"),
            search_max_pages=int(os.getenv("SEARCH_MAX_PAGES") or "3"),
            email_user=os.getenv("EMAIL_USER") or "",
            email_password=os.getenv("EMAIL_PASSWORD") or "",
            receiver_email=os.getenv("RECEIVER_EMAIL") or "",
            smtp_server=os.getenv("SMTP_SERVER") or "smtp.gmail.com",
            smtp_port=int(os.getenv("SMTP_PORT") or "587"),
            log_file=os.getenv("LOG_FILE") or "logs/pbandai_hk.log",
            log_level=(os.getenv("LOG_LEVEL") or "INFO").upper(),
            force_browser_login=_as_bool(os.getenv("FORCE_BROWSER_LOGIN"), False),
        )

    def validate(self) -> None:
        has_links = bool(self.product_codes)
        has_search = bool(self.search_keywords)
        if not has_links and not has_search:
            raise ValueError("Set PRODUCT_LINKS/PRODUCT_CODES and/or SEARCH_KEYWORDS")
        if has_search and not self.precheck_list and not self.target_list:
            raise ValueError(
                "SEARCH_KEYWORDS requires PRECHECK_LIST and/or TARGET_LIST "
                "(not needed for direct PRODUCT_LINKS)"
            )
        if self.cart_mode not in {"first", "all", "round_robin"}:
            raise ValueError("CART_MODE must be one of: first, all, round_robin")
        if self.cart_method not in {"auto", "api", "browser", "warm"}:
            raise ValueError("CART_METHOD must be one of: auto, api, browser, warm")
        if self.browser_instances < 1:
            raise ValueError("BROWSER_INSTANCES must be >= 1")
        if self.click_at_second < -1 or self.click_at_second > 59:
            raise ValueError("CLICK_AT_SECOND must be -1 (interval mode) or 0..59")
        if self.click_interval_seconds <= 0:
            raise ValueError("CLICK_INTERVAL_SECONDS must be > 0")
        if self.open_stagger_seconds < 0:
            raise ValueError("OPEN_STAGGER_SECONDS must be >= 0")
        if self.open_pdp_retries < 1:
            raise ValueError("OPEN_PDP_RETRIES must be >= 1")
        if self.open_pdp_retry_wait < 0:
            raise ValueError("OPEN_PDP_RETRY_WAIT must be >= 0")
        if self.oos_refresh_seconds < 0:
            raise ValueError("OOS_REFRESH_SECONDS must be >= 0")
        if self.pdp_max_concurrent < 1:
            raise ValueError("PDP_MAX_CONCURRENT must be >= 1")
        if self.idle_activity_seconds < 0:
            raise ValueError("IDLE_ACTIVITY_SECONDS must be >= 0")
        if self.home_warmup_seconds < 0:
            raise ValueError("HOME_WARMUP_SECONDS must be >= 0")
        if self.proxy_assign_mode not in {"random", "unique", "unique_random", "shuffle"}:
            raise ValueError(
                "PROXY_ASSIGN_MODE must be one of: random, unique, unique_random, shuffle"
            )

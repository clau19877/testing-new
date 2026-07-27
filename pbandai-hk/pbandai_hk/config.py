import os
from dataclasses import dataclass, field
from typing import List

from dotenv import load_dotenv

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
    cart_method: str = "auto"  # auto | api | browser
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
        load_dotenv(dotenv_path)
        precheck = _split_csv(os.getenv("PRECHECK_LIST"))
        target = _split_csv(os.getenv("TARGET_LIST"))
        product_links = _split_csv(os.getenv("PRODUCT_LINKS"))
        explicit_codes = _split_csv(os.getenv("PRODUCT_CODES"))
        product_codes = extract_product_codes([*product_links, *explicit_codes])
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
        if self.cart_method not in {"auto", "api", "browser"}:
            raise ValueError("CART_METHOD must be one of: auto, api, browser")
        if self.proxy_assign_mode not in {"random", "unique", "unique_random", "shuffle"}:
            raise ValueError(
                "PROXY_ASSIGN_MODE must be one of: random, unique, unique_random, shuffle"
            )

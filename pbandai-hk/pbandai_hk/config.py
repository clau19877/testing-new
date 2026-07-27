import os
from dataclasses import dataclass, field
from typing import List

from dotenv import load_dotenv


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
    precheck_list: List[str] = field(default_factory=list)
    target_list: List[str] = field(default_factory=list)
    sale_statuses: List[str] = field(default_factory=lambda: ["On", "Waiting"])
    enable_add_to_cart: bool = False
    cart_qty: int = 1
    add_cart_retry_count: int = 5
    background_mode: bool = False
    login_url: str = "https://p-bandai.com/hk/login"
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

    @classmethod
    def from_env(cls, dotenv_path: str | None = None) -> "Config":
        load_dotenv(dotenv_path)
        precheck = _split_csv(os.getenv("PRECHECK_LIST"))
        target = _split_csv(os.getenv("TARGET_LIST"))
        return cls(
            area_code=(os.getenv("AREA_CODE") or "hk").lower(),
            base_url=(os.getenv("BASE_URL") or "https://p-bandai.com").rstrip("/"),
            accept_language=os.getenv("ACCEPT_LANGUAGE") or "en",
            search_keywords=_split_csv(os.getenv("SEARCH_KEYWORDS")),
            precheck_list=precheck,
            target_list=target or list(precheck),
            sale_statuses=_split_csv(os.getenv("SALE_STATUSES")) or ["On", "Waiting"],
            enable_add_to_cart=_as_bool(os.getenv("ENABLE_ADD_TO_CART"), False),
            cart_qty=int(os.getenv("CART_QTY") or "1"),
            add_cart_retry_count=int(os.getenv("ADD_CART_RETRY_COUNT") or "5"),
            background_mode=_as_bool(os.getenv("BACKGROUND_MODE"), False),
            login_url=os.getenv("LOGIN_URL") or "https://p-bandai.com/hk/login",
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
        )

    def validate(self) -> None:
        if not self.search_keywords:
            raise ValueError("SEARCH_KEYWORDS is required")
        if not self.precheck_list and not self.target_list:
            raise ValueError("Set PRECHECK_LIST and/or TARGET_LIST")

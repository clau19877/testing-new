from __future__ import annotations

import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Optional

from .config import Config


def send_email(config: Config, subject: str, body: str) -> bool:
    if not config.email_user:
        return False
    receiver = config.receiver_email or config.email_user
    msg = MIMEMultipart()
    msg["From"] = config.email_user
    msg["To"] = receiver
    msg["Subject"] = subject
    msg.attach(MIMEText(body, "plain", "utf-8"))
    with smtplib.SMTP(config.smtp_server, config.smtp_port) as server:
        server.starttls()
        server.login(config.email_user, config.email_password)
        server.send_message(msg)
    return True


def maybe_send_email(config: Config, subject: str, body: str) -> Optional[str]:
    try:
        if send_email(config, subject, body):
            return "sent"
        return "skipped"
    except Exception as exc:  # noqa: BLE001 - surface notify failures without crashing loop
        return f"error: {exc}"

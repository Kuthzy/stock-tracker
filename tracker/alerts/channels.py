"""Alert delivery channels. A channel without credentials is skipped automatically."""

from __future__ import annotations

import logging
import smtplib
import time
from email.message import EmailMessage

import requests

from .. import plugins
from ..config import Config
from ..plugins import register

log = logging.getLogger(__name__)


def post_with_retry(url: str, *, name: str, tries: int = 3, redact: str | None = None, **kwargs) -> bool:
    """POST with up to `tries` attempts on network errors, 5xx and 429 (honours Retry-After)."""
    delay = 2.0
    for attempt in range(tries):
        try:
            resp = requests.post(url, timeout=15, **kwargs)
            if resp.status_code < 300:
                return True
            if resp.status_code == 429 or resp.status_code >= 500:
                wait = delay
                try:
                    wait = float(resp.headers.get("Retry-After")
                                 or resp.json().get("parameters", {}).get("retry_after") or delay)
                except (ValueError, AttributeError):
                    pass
                time.sleep(min(wait, 30))
                delay *= 2
                continue
            body = resp.text[:200]
            if redact:
                body = body.replace(redact, "***")
            log.warning("%s send failed: HTTP %s %s", name, resp.status_code, body)
            return False
        except requests.RequestException as exc:
            msg = str(exc).replace(redact, "***") if redact else str(exc)
            if attempt == tries - 1:
                log.warning("%s send failed: %s", name, msg[:200])
                return False
            time.sleep(delay)
            delay *= 2
    log.warning("%s send failed after %d tries", name, tries)
    return False


class Channel:
    """Base class for an alert channel. Subclass it, decorate with @register("channels", "name"),
    implement send(text) -> bool, and add a from_config(cfg) classmethod that returns None
    when the channel's settings are missing (so it is skipped quietly)."""

    name = "base"

    def configured(self) -> bool:
        return True

    @classmethod
    def from_config(cls, cfg):
        return cls()

    def send(self, text: str) -> bool:
        raise NotImplementedError


@register("channels", "console")
class ConsoleChannel(Channel):
    name = "console"

    def send(self, text: str) -> bool:
        print("\n" + "=" * 60 + "\n" + text + "\n" + "=" * 60, flush=True)
        return True


@register("channels", "telegram")
class TelegramChannel(Channel):
    name = "telegram"

    def __init__(self, token: str | None, chat_id: str | None):
        self.token, self.chat_id = token, chat_id

    @classmethod
    def from_config(cls, cfg):
        ch = cls(cfg.env("TELEGRAM_BOT_TOKEN"), cfg.env("TELEGRAM_CHAT_ID"))
        return ch if ch.configured() else None

    def configured(self) -> bool:
        return bool(self.token and self.chat_id)

    def send(self, text: str) -> bool:
        return post_with_retry(f"https://api.telegram.org/bot{self.token}/sendMessage", name="telegram",
                               redact=self.token,
                               json={"chat_id": self.chat_id, "text": text[:4000],
                                     "disable_web_page_preview": True})


@register("channels", "discord")
class DiscordChannel(Channel):
    name = "discord"

    def __init__(self, webhook: str | None):
        self.webhook = webhook

    @classmethod
    def from_config(cls, cfg):
        ch = cls(cfg.env("DISCORD_WEBHOOK_URL"))
        return ch if ch.configured() else None

    def configured(self) -> bool:
        return bool(self.webhook)

    def send(self, text: str) -> bool:
        return post_with_retry(self.webhook, name="discord", redact=self.webhook,
                               json={"content": text[:1990]})


@register("channels", "email")
class EmailChannel(Channel):
    name = "email"

    def __init__(self, host, port, user, password, to):
        self.host, self.port, self.user, self.password, self.to = host, int(port or 587), user, password, to

    @classmethod
    def from_config(cls, cfg):
        env = cfg.env
        ch = cls(env("SMTP_HOST"), env("SMTP_PORT"), env("SMTP_USER"), env("SMTP_PASSWORD"), env("ALERT_EMAIL_TO"))
        return ch if ch.configured() else None

    def configured(self) -> bool:
        return bool(self.host and self.user and self.password and self.to)

    def send(self, text: str) -> bool:
        msg = EmailMessage()
        msg["Subject"] = "Stock Tracker: " + text.splitlines()[0][:120]
        msg["From"], msg["To"] = self.user, self.to
        msg.set_content(text)
        try:
            with smtplib.SMTP(self.host, self.port, timeout=20) as smtp:
                smtp.starttls()
                smtp.login(self.user, self.password)
                smtp.send_message(msg)
            return True
        except (smtplib.SMTPException, OSError) as exc:
            log.warning("email send failed: %s", exc)
            return False


def build_channels(cfg: Config) -> list[Channel]:
    active = plugins.build("channels", cfg["alerts"]["channels"], cfg)
    log.info("alert channels active: %s", ", ".join(c.name for c in active) or "none")
    return active

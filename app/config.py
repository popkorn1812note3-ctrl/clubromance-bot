"""Конфигурация из переменных окружения (.env)."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

ROOT = Path(__file__).resolve().parent.parent


def _clean_username(raw: str) -> str:
    """Очищает юзернейм бота от полного URL/@: 'https://max.ru/@bot' -> 'bot'."""
    u = (raw or "").strip()
    for prefix in ("https://max.ru/", "http://max.ru/", "max.ru/"):
        if u.startswith(prefix):
            u = u[len(prefix):]
    return u.lstrip("@").strip()


@dataclass(frozen=True)
class Config:
    token: str
    bot_username: str
    db_path: str
    webhook_base_url: str
    webhook_secret: str
    webhook_host: str
    webhook_port: int
    log_level: str
    admin_ids: frozenset[int]
    admin_user: str
    admin_password: str
    admin_host: str
    admin_port: int

    @property
    def db_abspath(self) -> str:
        p = Path(self.db_path)
        return str(p if p.is_absolute() else ROOT / p)


def _parse_ids(raw: str) -> frozenset[int]:
    out: set[int] = set()
    for chunk in (raw or "").replace(" ", "").split(","):
        if chunk.lstrip("-").isdigit():
            out.add(int(chunk))
    return frozenset(out)


def load_config() -> Config:
    token = os.getenv("MAX_BOT_TOKEN", "").strip()
    return Config(
        token=token,
        bot_username=_clean_username(os.getenv("BOT_USERNAME", "")),
        db_path=os.getenv("DB_PATH", "clubromance.db").strip() or "clubromance.db",
        webhook_base_url=os.getenv("WEBHOOK_BASE_URL", "").strip().rstrip("/"),
        webhook_secret=os.getenv("WEBHOOK_SECRET", "").strip(),
        webhook_host=os.getenv("WEBHOOK_HOST", "127.0.0.1").strip(),
        webhook_port=int(os.getenv("WEBHOOK_PORT", "8080") or "8080"),
        log_level=os.getenv("LOG_LEVEL", "INFO").strip().upper(),
        # Тестовые админы (для команды /give). Переопределяется ADMIN_IDS в .env.
        admin_ids=_parse_ids(os.getenv("ADMIN_IDS", "5479775,3958992")),
        admin_user=os.getenv("ADMIN_USER", "admin").strip() or "admin",
        admin_password=os.getenv("ADMIN_PASSWORD", "").strip(),
        admin_host=os.getenv("ADMIN_HOST", "0.0.0.0").strip(),
        admin_port=int(os.getenv("ADMIN_PORT", "8080") or "8080"),
    )

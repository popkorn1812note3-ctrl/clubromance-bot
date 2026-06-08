"""Сборка рантайма: конфиг → БД → API-клиент → реестр историй → Context."""
from __future__ import annotations

import logging

from .config import Config, load_config
from .db import DB
from .max_client import MaxClient, MaxError
from .runtime import Context
from .stories import registry


def setup_logging(level: str = "INFO") -> None:
    # На русской Windows консоль часто в cp1251 — эмодзи в логах роняют процесс.
    # Переключаем потоки на UTF-8 (Windows Terminal это рендерит корректно).
    import sys

    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
        except (AttributeError, ValueError):
            pass

    logging.basicConfig(
        level=getattr(logging, level, logging.INFO),
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )


async def build_context(cfg: Config | None = None, *, fetch_identity: bool = True) -> Context:
    cfg = cfg or load_config()
    db = DB(cfg.db_abspath)
    await db.connect()

    registry.load()

    api = MaxClient(cfg.token)
    bot_username = cfg.bot_username

    if fetch_identity and cfg.token:
        try:
            me = await api.get_me()
            bot_username = bot_username or (me.get("username") or "")
            logging.getLogger("boot").info(
                "Бот: %s (@%s), id=%s", me.get("name"), me.get("username"), me.get("user_id")
            )
        except MaxError as e:
            logging.getLogger("boot").warning("GET /me не удался: %s", e)

    return Context(db=db, api=api, registry=registry, bot_username=bot_username, admin_ids=cfg.admin_ids)

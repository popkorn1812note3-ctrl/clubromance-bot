#!/usr/bin/env python
"""Запуск бота ClubRomance в режиме polling (для разработки/малой нагрузки).

  python run.py

Требуется MAX_BOT_TOKEN в .env. Для продакшна используйте webhook_server.py
(polling MAX дросселит после ~10 RPS — см. базу знаний MAX Bot API).
"""
from __future__ import annotations

import asyncio
import logging

from app.bootstrap import build_context, setup_logging
from app.config import load_config
from app.dispatcher import dispatch
from app.max_client import TransientError
from app.tasks import retention_loop

log = logging.getLogger("run")


async def main() -> None:
    cfg = load_config()
    setup_logging(cfg.log_level)

    if not cfg.token:
        raise SystemExit("❌ MAX_BOT_TOKEN не задан. Скопируйте .env.example в .env и впишите токен.")

    ctx = await build_context(cfg)

    # Чтобы polling получал апдейты, снимаем все webhook-подписки (свои и чужие).
    try:
        for sub in await ctx.api.list_webhooks():
            url = sub.get("url")
            if url:
                log.info("Снимаю webhook: %s", url)
                await ctx.api.delete_webhook(url)
    except Exception as e:  # noqa: BLE001
        log.warning("Не удалось проверить подписки: %s", e)

    # Фоновый отзыв награды за подписку у отписавшихся (раз в час).
    # Ссылку держим до конца main — иначе Python может собрать task (грабля Г12 OPBOT).
    retention_task = asyncio.create_task(retention_loop(ctx))

    log.info("🚀 ClubRomance запущен (polling). Ctrl+C для остановки.")
    marker: int | None = None
    try:
        while True:
            try:
                resp = await ctx.api.get_updates(marker=marker, timeout=30, limit=100)
            except TransientError as e:
                log.warning("updates transient: %s", e)
                await asyncio.sleep(2)
                continue
            updates = resp.get("updates", []) or []
            for upd in updates:
                await dispatch(ctx, upd)
            new_marker = resp.get("marker")
            if new_marker is not None:
                marker = new_marker
    except (KeyboardInterrupt, asyncio.CancelledError):
        log.info("Остановка…")
    finally:
        retention_task.cancel()
        await ctx.api.close()
        await ctx.db.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass

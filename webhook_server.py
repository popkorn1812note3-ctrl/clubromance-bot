#!/usr/bin/env python
"""Запуск бота ClubRomance в режиме webhook (продакшн).

  uvicorn webhook_server:app --host 0.0.0.0 --port 8080
  # или просто:  python webhook_server.py

Нужны переменные в .env: MAX_BOT_TOKEN, WEBHOOK_BASE_URL (публичный HTTPS),
WEBHOOK_SECRET. Бот регистрирует подписку BASE_URL/webhook/<secret> и снимает
чужие webhook'и при старте (см. базу знаний — боты «из чужих рук» приходят
с webhook на скам-панели).
"""
from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request

from app.bootstrap import build_context, setup_logging
from app.config import load_config
from app.dispatcher import dispatch
from app.runtime import Context
from app.tasks import retention_loop

log = logging.getLogger("webhook")

UPDATE_TYPES = ["bot_started", "message_created", "message_callback", "bot_added", "bot_removed"]

cfg = load_config()
setup_logging(cfg.log_level)
_ctx: Context | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _ctx
    if not cfg.token:
        raise SystemExit("❌ MAX_BOT_TOKEN не задан.")
    _ctx = await build_context(cfg)

    # Снимаем чужие/старые подписки.
    our_url = f"{cfg.webhook_base_url}/webhook/{cfg.webhook_secret}" if cfg.webhook_base_url else ""
    try:
        for sub in await _ctx.api.list_webhooks():
            url = sub.get("url")
            if url and url != our_url:
                log.info("Снимаю стороннюю подписку: %s", url)
                await _ctx.api.delete_webhook(url)
    except Exception as e:  # noqa: BLE001
        log.warning("Не удалось проверить подписки: %s", e)

    if our_url:
        try:
            await _ctx.api.register_webhook(our_url, UPDATE_TYPES)
            log.info("Webhook зарегистрирован: %s", our_url)
        except Exception as e:  # noqa: BLE001
            log.error("Не удалось зарегистрировать webhook: %s", e)
    else:
        log.warning("WEBHOOK_BASE_URL не задан — подписка не зарегистрирована (зарегистрируйте вручную).")

    # Фоновый отзыв награды за подписку у отписавшихся (раз в час).
    retention_task = asyncio.create_task(retention_loop(_ctx))

    yield

    retention_task.cancel()
    await _ctx.api.close()
    await _ctx.db.close()


app = FastAPI(title="ClubRomance MAX bot", lifespan=lifespan)


@app.get("/")
async def health() -> dict[str, str]:
    return {"status": "ok", "service": "clubromance"}


@app.post("/webhook/{secret}")
async def webhook(secret: str, request: Request) -> dict[str, bool]:
    if secret != cfg.webhook_secret:
        raise HTTPException(status_code=403, detail="forbidden")
    if _ctx is None:
        raise HTTPException(status_code=503, detail="not ready")
    update = await request.json()
    # Быстро отдаём 200, обработку — в фоне (fire-and-forget).
    asyncio.create_task(dispatch(_ctx, update))
    return {"ok": True}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host=cfg.webhook_host, port=cfg.webhook_port)

"""Фоновые задачи бота.

retention_sweep — отзыв награды за подписку: когда у выдачи наступает срок проверки
(claimed_at + hold_days), смотрим, остался ли юзер подписан. Если отписался — списываем
награду (не уводя баланс в минус). Если ещё подписан — закрепляем награду (больше не
проверяем). Запускается периодически из run.py / webhook_server.py.
"""
from __future__ import annotations

import asyncio
import logging
import time

from . import gate
from .runtime import Context

log = logging.getLogger("tasks")

RETENTION_INTERVAL = 3600  # как часто сканировать «созревшие» выдачи (сек)


async def retention_sweep(ctx: Context) -> int:
    """Один проход проверки удержания. Возвращает число отозванных наград."""
    now = int(time.time())
    due = await ctx.db.list_due_claims(now)
    if not due:
        return 0
    log.info("retention: к проверке %d выдач", len(due))
    revoked = 0
    for claim in due:
        uid, chat_id = claim["user_id"], claim["chat_id"]
        sub = await gate.is_subscribed(ctx, chat_id, uid)
        if sub is None:
            continue  # не смогли проверить (бот не админ/ошибка) — оставим на следующий проход
        if sub:
            await ctx.db.mark_claim_kept(uid, chat_id)  # удержал срок → награда закреплена
            continue
        taken = await ctx.db.revoke_subscription(uid, chat_id)
        revoked += 1
        if taken > 0:
            ch = await ctx.db.get_channel(chat_id)
            title = (ch.get("title") if ch else "") or "канал"
            u = await ctx.db.get_user(uid)
            if u and u.get("notifications"):
                await ctx.send(
                    uid,
                    f"↩️ Награда за подписку на «{title}» отозвана — ты отписался. −{taken} 💎",
                )
    if revoked:
        log.info("retention: отозвано наград: %d", revoked)
    return revoked


async def retention_loop(ctx: Context, interval: int = RETENTION_INTERVAL) -> None:
    """Бесконечный цикл retention-проверки. Запускать через asyncio.create_task."""
    while True:
        try:
            await retention_sweep(ctx)
        except Exception:  # noqa: BLE001 — цикл не должен падать
            log.exception("retention sweep упал")
        await asyncio.sleep(interval)

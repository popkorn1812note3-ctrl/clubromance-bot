"""Гейт обязательной подписки (ОП): пускаем в бота только подписанных на каналы.

Канал считается требуемым, когда бота добавили в него админом (`bot_added`).
Проверка подписки работает, только если бот — АДМИН канала (иначе MAX не отдаёт
участников). Каналы, где бот не админ, не блокируют вход (чтобы не запереть всех).
Флаг users.gate_passed кеширует «прошёл», чтобы не дёргать API на каждое действие.
"""
from __future__ import annotations

import logging

from .max_client import ChatDenied, MaxError
from .runtime import Context

log = logging.getLogger("gate")


async def _verify_all(ctx: Context, user_id: int) -> tuple[bool, list[dict]]:
    """Проверяет подписку по API. (ok, список не-подписанных каналов)."""
    channels = await ctx.db.list_required_channels()
    if not channels:
        return True, []
    missing: list[dict] = []
    for ch in channels:
        try:
            ok = await ctx.api.is_member(ch["chat_id"], user_id)
        except ChatDenied:
            log.warning("канал %s: бот не админ — проверку пропускаем", ch["chat_id"])
            continue
        except MaxError as e:
            log.warning("канал %s: ошибка проверки: %s", ch["chat_id"], e)
            continue
        if not ok:
            missing.append(ch)
    return (not missing), missing


async def passed(ctx: Context, user_id: int) -> bool:
    """Быстрый проход гейта. Если флаг стоит — без API. Иначе проверяем и кешируем."""
    channels = await ctx.db.list_required_channels()
    if not channels:
        return True  # гейт не настроен
    u = await ctx.db.get_user(user_id)
    if u and u.get("gate_passed"):
        return True
    ok, _ = await _verify_all(ctx, user_id)
    if ok:
        await ctx.db.set_gate_passed(user_id, 1)
    return ok


async def recheck(ctx: Context, user_id: int) -> tuple[bool, list[dict]]:
    """Перепроверка по кнопке «Я подписался». Обновляет флаг."""
    ok, missing = await _verify_all(ctx, user_id)
    await ctx.db.set_gate_passed(user_id, 1 if ok else 0)
    return ok, missing

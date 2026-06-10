"""Разбор апдейтов MAX и маршрутизация в меню/игровой процесс."""
from __future__ import annotations

import logging
from typing import Any

from . import gate
from . import keyboards as kb
from . import menus, play
from .max_client import MaxError
from .runtime import Context

log = logging.getLogger("dispatch")


def _full_name(user: dict[str, Any]) -> str:
    fn = (user.get("first_name") or "").strip()
    ln = (user.get("last_name") or "").strip()
    name = f"{fn} {ln}".strip()
    return name or (user.get("name") or "").strip()


async def _handle_referral(ctx: Context, user_id: int, payload: str | None) -> None:
    if not payload or not payload.startswith("ref_"):
        return
    try:
        inviter_id = int(payload[4:])
    except ValueError:
        return
    if await ctx.db.add_referral(inviter_id, user_id, menus.REWARD_INVITE):
        await ctx.send(
            inviter_id,
            f"👥 По твоей ссылке пришёл новый друг! +{menus.REWARD_INVITE} 💎",
        )


async def dispatch(ctx: Context, update: dict[str, Any]) -> None:
    """Обрабатывает один апдейт MAX. Безопасна к исключениям (логирует, не падает)."""
    try:
        await _dispatch(ctx, update)
    except Exception:  # noqa: BLE001
        log.exception("ошибка обработки апдейта %s", update.get("update_type"))


async def _gate_block(ctx: Context, uid: int, *, force: bool = False) -> bool:
    """True, если юзера нужно держать на гейте подписки (не админ и не прошёл).
    force=True (вход/старт) — проверяем подписку по API, игнорируя кеш."""
    if uid in ctx.admin_ids:
        return False
    return not await gate.passed(ctx, uid, force=force)


async def _on_bot_added(ctx: Context, update: dict[str, Any]) -> None:
    """Бота добавили в канал админом → только ЗАПОМИНАЕМ канал (title/link/chat_id).
    В ОП или задания канал попадает ТОЛЬКО вручную через админку — иначе любой канал,
    куда добавили бота (задания, сторонние проекты), вставал бы в обязательную подписку."""
    chat_id = update.get("chat_id")
    if chat_id is None or not update.get("is_channel"):
        return
    title, link = "", ""
    try:
        chat = await ctx.api.get_chat(chat_id)
        title = chat.get("title") or ""
        link = chat.get("link") or ""
    except MaxError as e:
        log.warning("get_chat(%s) не удался: %s", chat_id, e)
    await ctx.db.register_known_channel(chat_id, title, link)
    log.info("Канал замечен (бот добавлен админом): %s (chat_id=%s). В ОП/задания — через админку.",
             title or "?", chat_id)


async def _dispatch(ctx: Context, update: dict[str, Any]) -> None:
    utype = update.get("update_type")

    if utype == "bot_started":
        user = update.get("user", {}) or {}
        uid = user.get("user_id")
        if uid is None:
            return
        log.info("bot_started uid=%s payload=%s", uid, update.get("payload"))
        await ctx.db.ensure_user(uid, _full_name(user), user.get("username") or "")
        await _handle_referral(ctx, uid, update.get("payload"))
        if await _gate_block(ctx, uid, force=True):  # вход в бота — всегда реальная проверка ОП
            await menus.show_gate(ctx, uid, force_new=True)
            return
        await menus.show_welcome(ctx, uid)
        return

    if utype == "message_created":
        msg = update.get("message", {}) or {}
        sender = msg.get("sender", {}) or {}
        uid = sender.get("user_id")
        if uid is None:
            return
        await ctx.db.ensure_user(uid, _full_name(sender), sender.get("username") or "")
        text = ((msg.get("body", {}) or {}).get("text") or "").strip()
        log.info("message uid=%s text=%r", uid, text[:40])
        # /start — явный перезаход: проверяем ОП по API, не доверяя кешу.
        if await _gate_block(ctx, uid, force=text.lower().startswith("/start")):
            await menus.show_gate(ctx, uid, force_new=True)
            return
        await _on_text(ctx, uid, text)
        return

    if utype == "message_callback":
        cbk = update.get("callback", {}) or {}
        user = cbk.get("user", {}) or {}
        uid = user.get("user_id")
        callback_id = cbk.get("callback_id")
        payload = cbk.get("payload") or ""
        if uid is None or not callback_id:
            return
        log.info("callback uid=%s payload=%s", uid, payload)
        await ctx.db.ensure_user(uid, _full_name(user), user.get("username") or "")
        if payload.startswith("gate:"):
            await menus.handle_gate_check(ctx, uid, callback_id)
            return
        if await _gate_block(ctx, uid):
            await ctx.api.answer_callback(callback_id)
            await menus.show_gate(ctx, uid)
            return
        await _on_callback(ctx, uid, payload, callback_id)
        return

    if utype == "bot_added":
        await _on_bot_added(ctx, update)
        return

    log.debug("проигнорирован апдейт типа %s", utype)


async def _on_text(ctx: Context, user_id: int, text: str) -> None:
    low = text.lower()
    # /start с ручным payload: "/start ref_123"
    if low.startswith("/start"):
        rest = text[6:].strip()
        await ctx.db.update_user(user_id, pending=None)  # сбрасываем ввод имени
        if rest:
            await _handle_referral(ctx, user_id, rest)
        await menus.show_welcome(ctx, user_id)
        return
    # Ожидаем ввод имени героини?
    if await menus.apply_hero_name(ctx, user_id, text):
        return
    # Админская команда выдачи кристаллов (для теста): /give [N]
    if low.startswith("/give") and user_id in ctx.admin_ids:
        bits = text.split()
        amount = 1000
        if len(bits) > 1 and bits[1].lstrip("-").isdigit():
            amount = int(bits[1])
        bal = await ctx.db.add_crystals(user_id, amount, "grant", "admin_give")
        await ctx.show_screen(user_id, f"💎 Начислено *{amount}*. Баланс: *{bal}* 💎", kb.main_menu(), force_new=True)
        return
    if low in ("/menu", "меню", "menu", "/help", "помощь", "/play", "играть"):
        await menus.show_main(ctx, user_id)
        return
    # Любой другой текст — подсказываем меню (новым сообщением под текстом юзера).
    await ctx.show_screen(
        user_id,
        "Я бот интерактивных историй 📖\nОткрой меню, чтобы выбрать историю или получить кристаллы:",
        kb.main_menu(),
        force_new=True,
    )


async def _on_callback(ctx: Context, user_id: int, payload: str, callback_id: str) -> None:
    parts = payload.split(":")
    if parts and parts[0] == "pl":
        await play.on_callback(ctx, user_id, parts, callback_id)
    else:
        await menus.on_callback(ctx, user_id, payload, callback_id)

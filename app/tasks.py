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
RETENTION_BATCH = 500      # сколько выдач проверяем за один проход
RETENTION_BACKOFF = 86400  # на сколько откладываем проверку, если её нельзя выполнить (сек)


async def retention_sweep(ctx: Context) -> int:
    """Один проход проверки удержания. Возвращает число отозванных наград."""
    now = int(time.time())
    due = await ctx.db.list_due_claims(now, RETENTION_BATCH)
    if not due:
        return 0
    if len(due) >= RETENTION_BATCH:
        log.warning("retention: упёрлись в лимит %d — остальные на следующий проход", RETENTION_BATCH)
    log.info("retention: к проверке %d выдач", len(due))
    revoked = 0
    for claim in due:
        uid, chat_id = claim["user_id"], claim["chat_id"]
        sub = await gate.is_subscribed(ctx, chat_id, uid)
        if sub is None:
            # проверить нельзя (бот не админ / канал удалён) — откладываем, чтобы не дёргать API каждый час
            await ctx.db.defer_claim_check(uid, chat_id, now + RETENTION_BACKOFF)
            continue
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


async def recheck_subscriptions(ctx: Context, state: dict) -> None:
    """Массовая перепроверка подписок ОП (как recheck в OPBOT): по каждому каналу
    скачиваем ВЕСЬ список участников одним проходом (а не is_member на юзера —
    бережём общий rate-limit токена), затем сверяем всех юзеров локально, пишем
    срез в user_subscriptions и пересчитываем gate_passed.

    state — общий dict для прогресса/остановки: running, stage, done, total,
    stop (флаг остановки), channels {chat_id: участников|None}, finished_at.
    Каналы, где бот не админ, пропускаются (как в _verify_all — не блокируют)."""
    now = int(time.time())
    state.update(running=True, stop=False, stage="каналы", done=0, total=0,
                 channels={}, current=None, started_at=now, finished_at=0)
    max_pages = int(state.get("max_pages") or 5000)  # потолок ~500k участников на канал
    try:
        channels = await ctx.db.list_required_channels()
        users = await ctx.db.all_user_ids()
        state["total"] = len(users)
        state["channels"] = {ch["chat_id"]: None for ch in channels}  # прогресс «0/N» виден сразу

        member_sets: dict[int, set[int]] = {}
        for ch in channels:
            if state["stop"]:
                log.info("recheck: остановлен на стадии каналов")
                return
            cid = ch["chat_id"]
            expected = None
            try:
                chat = await ctx.api.get_chat(cid)
                expected = chat.get("participants_count")
            except Exception:  # noqa: BLE001
                pass
            ids: set[int] = set()
            pages = 0
            truncated = False
            try:
                async for page in ctx.api.iter_chat_member_pages(cid):
                    ids |= page
                    pages += 1
                    state["current"] = {"chat_id": cid, "title": ch.get("title") or "",
                                        "pages": pages, "collected": len(ids), "expected": expected}
                    if state["stop"]:
                        log.info("recheck: остановлен на канале %s (страница %d)", cid, pages)
                        return
                    if pages >= max_pages:
                        truncated = True
                        break
            except Exception as e:  # noqa: BLE001 — не админ/канал удалён: пропускаем
                state["channels"][cid] = None
                log.warning("recheck: канал %s не прочитать (%s) — пропуск", cid, e)
                continue
            finally:
                state["current"] = None
            if truncated:
                # Недокачанный список НЕЛЬЗЯ использовать: реально подписанные за пределом
                # потолка были бы помечены неподписанными. Канал пропускаем целиком.
                state["channels"][cid] = None
                log.warning("recheck: канал %s больше %d страниц — пропуск (поднимите потолок)",
                            cid, max_pages)
                continue
            member_sets[cid] = ids
            state["channels"][cid] = len(ids)
            log.info("recheck: канал %s — %d участников (%d страниц)", cid, len(ids), pages)

        state["stage"] = "юзеры"
        sub_rows: list[tuple[int, int, int, int]] = []
        gate_rows: list[tuple[int, int, int]] = []
        for i, uid in enumerate(users):
            if state["stop"]:
                log.info("recheck: остановлен на %d/%d", i, len(users))
                return
            ok_all = True
            for cid, ids in member_sets.items():
                sub = uid in ids
                sub_rows.append((uid, cid, 1 if sub else 0, now))
                ok_all = ok_all and sub
            gate_rows.append((1 if ok_all else 0, now, uid))
            state["done"] = i + 1
            if i % 500 == 499:
                await asyncio.sleep(0)  # отдаём loop, чтобы не блокировать бота

        state["stage"] = "запись"
        await ctx.db.bulk_set_subscriptions(sub_rows)
        await ctx.db.bulk_set_gate(gate_rows)
        passed = sum(1 for g, _, _ in gate_rows if g)
        log.info("recheck: готово — %d юзеров, прошли гейт %d, каналов проверено %d/%d",
                 len(users), passed, len(member_sets), len(channels))
    except Exception:  # noqa: BLE001
        log.exception("recheck упал")
    finally:
        state["running"] = False
        state["finished_at"] = int(time.time())

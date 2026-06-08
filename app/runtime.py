"""Контекст исполнения: общие зависимости (БД, API-клиент, реестр историй)."""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from .db import DB
from .max_client import ChatDenied, MaxClient, MaxError
from .stories import Registry

log = logging.getLogger("rt")


@dataclass
class Context:
    db: DB
    api: MaxClient
    registry: Registry
    bot_username: str = ""
    admin_ids: frozenset[int] = frozenset()
    # user_id -> mid текущего «экрана» (его редактируем на месте, чтобы не плодить сообщения).
    screen_mid: dict[int, str] = field(default_factory=dict)

    async def send(
        self,
        user_id: int,
        text: str,
        keyboard: list[list[dict[str, Any]]] | None = None,
        *,
        image_url: str | None = None,
    ) -> str | None:
        """Отправка нового сообщения. Возвращает mid (или None)."""
        try:
            resp = await self.api.send_message(
                user_id=user_id, text=text, keyboard=keyboard, image_url=image_url
            )
            return self.api.extract_mid(resp)
        except ChatDenied:
            log.info("user %s заблокировал бота — пропуск", user_id)
        except MaxError as e:
            log.warning("send to %s failed: %s", user_id, e)
        return None

    async def show_screen(
        self,
        user_id: int,
        text: str,
        keyboard: list[list[dict[str, Any]]] | None = None,
        *,
        image_url: str | None = None,
        force_new: bool = False,
    ) -> str | None:
        """Показать экран: ОТРЕДАКТИРОВАТЬ текущее сообщение на месте, либо (если
        его нет / редактирование не удалось / нужен ре-якорь после текста юзера)
        прислать новое и подчистить старое. Так в чате не копятся сообщения."""
        old = self.screen_mid.get(user_id)
        if old and not force_new:
            try:
                await self.api.edit_message(old, text=text, keyboard=keyboard, image_url=image_url)
                return old
            except ChatDenied:
                return None
            except MaxError as e:
                log.debug("edit %s failed: %s — шлём новое", old, e)
        new_mid = await self.send(user_id, text, keyboard, image_url=image_url)
        if new_mid:
            self.screen_mid[user_id] = new_mid
            if old and old != new_mid:
                try:
                    await self.api.delete_message(old)
                except MaxError:
                    pass
        return new_mid

    def deep_link(self, payload: str = "") -> str:
        base = f"https://max.ru/{self.bot_username}" if self.bot_username else "https://max.ru/<bot>"
        return f"{base}?start={payload}" if payload else base

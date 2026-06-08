"""Тонкий асинхронный клиент MAX Bot API поверх aiohttp.

Заложены эмпирические грабли (см. база знаний OPBOT/Podpiski/GiftBot):
  • Авторизация: заголовок `Authorization: <token>` БЕЗ префикса `Bearer`.
  • Под нагрузкой — webhook, не polling (polling MAX дросселит после ~10 RPS).
  • Форматирование текста — поле `format` ("markdown" | "html"), иначе ссылки сырые.
  • answer_callback: НЕЛЬЗЯ заменить сообщение новой клавиатурой — шлём новый POST /messages.
  • clipboard-кнопка: копируемый текст в поле `payload`, не `value`.
  • Лимиты клавиатуры: 210 кнопок, 30 рядов, 7 в ряду.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

import aiohttp

log = logging.getLogger("max")

BASE_URL = "https://botapi.max.ru"


class MaxError(RuntimeError):
    """Любая нефатальная ошибка API."""


class ChatDenied(MaxError):
    """Юзер заблокировал бота / нет доступа к чату."""


class TransientError(MaxError):
    """Временная ошибка (429/5xx) — можно повторить позже."""


class MaxClient:
    def __init__(self, token: str, *, timeout: float = 35.0) -> None:
        if not token:
            raise ValueError("MAX_BOT_TOKEN пуст — задайте токен в .env")
        self._token = token
        self._timeout = aiohttp.ClientTimeout(total=timeout)
        self._session: aiohttp.ClientSession | None = None

    async def _ensure_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            # Авторизация заголовком, БЕЗ Bearer.
            self._session = aiohttp.ClientSession(
                headers={"Authorization": self._token},
                timeout=self._timeout,
            )
        return self._session

    async def close(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        session = await self._ensure_session()
        url = f"{BASE_URL}{path}"
        # Один ретрай на транзиентных ошибках (429/5xx) — как в проверенном коде.
        for attempt in range(2):
            try:
                async with session.request(method, url, params=params, json=json) as r:
                    try:
                        data = await r.json()
                    except aiohttp.ContentTypeError:
                        data = {}
                    if r.status < 400:
                        return data if isinstance(data, dict) else {"result": data}
                    code = (data or {}).get("code", "")
                    if r.status == 403 and code in ("chat.denied", "denied"):
                        raise ChatDenied(f"{path}: {data}")
                    if r.status == 429 and attempt == 0:
                        await asyncio.sleep(0.4)
                        continue
                    if r.status == 429 or r.status >= 500:
                        raise TransientError(f"{r.status} {path}: {data}")
                    raise MaxError(f"{r.status} {path}: {data}")
            except (aiohttp.ClientError, asyncio.TimeoutError) as e:
                if attempt == 0:
                    await asyncio.sleep(0.4)
                    continue
                raise TransientError(f"net {path}: {e}") from e
        raise TransientError(f"исчерпаны попытки: {path}")

    # ── Базовые методы ────────────────────────────────────────
    async def get_me(self) -> dict[str, Any]:
        return await self._request("GET", "/me")

    async def get_updates(self, *, marker: int | None = None, timeout: int = 30, limit: int = 100) -> dict[str, Any]:
        params: dict[str, Any] = {"timeout": timeout, "limit": limit}
        if marker is not None:
            params["marker"] = marker
        return await self._request("GET", "/updates", params=params)

    async def send_message(
        self,
        *,
        user_id: int | None = None,
        chat_id: int | None = None,
        text: str,
        keyboard: list[list[dict[str, Any]]] | None = None,
        fmt: str | None = "markdown",
        image: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Отправка сообщения в личку (user_id) или чат (chat_id).

        image — payload вложения-картинки, напр. {"photos": {...}} (из upload_image)."""
        if (user_id is None) == (chat_id is None):
            raise ValueError("укажите ровно одно из user_id / chat_id")
        params: dict[str, Any] = {}
        if user_id is not None:
            params["user_id"] = user_id
        else:
            params["chat_id"] = chat_id

        body: dict[str, Any] = {"text": text}
        if fmt:
            body["format"] = fmt
        attachments: list[dict[str, Any]] = []
        if image:
            attachments.append({"type": "image", "payload": image})
        if keyboard:
            attachments.append({"type": "inline_keyboard", "payload": {"buttons": keyboard}})
        if attachments:
            body["attachments"] = attachments
        return await self._request("POST", "/messages", params=params, json=body)

    async def delete_message(self, message_id: str) -> dict[str, Any]:
        """Удалить сообщение бота. DELETE /messages?message_id=<mid>."""
        return await self._request("DELETE", "/messages", params={"message_id": message_id})

    async def edit_message(
        self,
        message_id: str,
        *,
        text: str,
        keyboard: list[list[dict[str, Any]]] | None = None,
        fmt: str | None = "markdown",
        image: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Отредактировать сообщение на месте. PUT /messages?message_id=<mid>."""
        body: dict[str, Any] = {"text": text}
        if fmt:
            body["format"] = fmt
        attachments: list[dict[str, Any]] = []
        if image:
            attachments.append({"type": "image", "payload": image})
        if keyboard:
            attachments.append({"type": "inline_keyboard", "payload": {"buttons": keyboard}})
        if attachments:
            body["attachments"] = attachments
        return await self._request("PUT", "/messages", params={"message_id": message_id}, json=body)

    async def get_messages(self, message_ids: str) -> dict[str, Any]:
        """Прочитать сообщения по id (для диагностики). GET /messages?message_ids=..."""
        return await self._request("GET", "/messages", params={"message_ids": message_ids})

    async def upload_image(self, data: bytes, filename: str = "image.png",
                           content_type: str = "image/png") -> dict[str, Any]:
        """Загружает картинку в MAX. Возвращает dict `photos` для вложения
        {"type": "image", "payload": {"photos": <это>}}."""
        info = await self._request("POST", "/uploads", params={"type": "image"})
        url = info.get("url")
        if not url:
            raise MaxError(f"/uploads без url: {info}")
        session = await self._ensure_session()
        form = aiohttp.FormData()
        form.add_field("data", data, filename=filename, content_type=content_type)
        async with session.post(url, data=form) as r:
            resp = await r.json(content_type=None)
        photos = (resp or {}).get("photos")
        if not photos:
            raise MaxError(f"upload не вернул photos: {resp}")
        return photos

    @staticmethod
    def extract_mid(resp: dict[str, Any]) -> str | None:
        """Достаёт mid отправленного сообщения из ответа /messages (защищённо)."""
        if not isinstance(resp, dict):
            return None
        msg = resp.get("message") or {}
        body = msg.get("body") or {}
        return body.get("mid") or msg.get("mid") or resp.get("mid")

    async def answer_callback(
        self,
        callback_id: str,
        *,
        notification: str | None = None,
        text: str | None = None,
    ) -> dict[str, Any]:
        """Ответ на нажатие inline-кнопки.

        notification — всплывашка (toast). text — заменить ТЕКСТ исходного сообщения
        (без новых кнопок — это ограничение MAX). Нужно хоть одно из полей.
        Для нового экрана с кнопками шлите отдельный send_message.
        """
        body: dict[str, Any] = {}
        if text is not None:
            body["message"] = {"text": text}
        # Всегда кладём notification (хотя бы пробел), чтобы снять «загрузку» на кнопке.
        body["notification"] = notification if notification is not None else " "
        return await self._request("POST", "/answers", params={"callback_id": callback_id}, json=body)

    # ── Каналы / проверка подписки ────────────────────────────
    async def get_chat(self, chat_id: int) -> dict[str, Any]:
        """Инфо о чате/канале: title, link, participants_count, is_public, icon."""
        return await self._request("GET", f"/chats/{chat_id}")

    async def get_my_membership(self, chat_id: int) -> dict[str, Any]:
        """Членство САМОГО бота в чате: is_owner, is_admin (для диагностики прав)."""
        return await self._request("GET", f"/chats/{chat_id}/members/me")

    async def is_member(self, chat_id: int, user_id: int) -> bool:
        """Подписан ли user_id на канал. Бот должен быть АДМИНОМ канала.

        ВАЖНО (грабля GiftBot): MAX может игнорировать фильтр user_ids и вернуть
        произвольных участников — поэтому СВЕРЯЕМ user_id явно. Бросает ChatDenied,
        если бот не админ канала (проверку провести нельзя)."""
        data = await self._request(
            "GET", f"/chats/{chat_id}/members", params={"user_ids": user_id}
        )
        members = data.get("members", []) if isinstance(data, dict) else []
        return any(m.get("user_id") == user_id for m in members)

    # ── Webhook (subscriptions) ───────────────────────────────
    async def list_webhooks(self) -> list[dict[str, Any]]:
        data = await self._request("GET", "/subscriptions")
        return data.get("subscriptions", []) if isinstance(data, dict) else []

    async def register_webhook(self, url: str, update_types: list[str]) -> dict[str, Any]:
        return await self._request("POST", "/subscriptions", json={"url": url, "update_types": update_types})

    async def delete_webhook(self, url: str) -> dict[str, Any]:
        return await self._request("DELETE", "/subscriptions", params={"url": url})

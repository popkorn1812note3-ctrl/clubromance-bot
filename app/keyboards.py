"""Построители inline-клавиатур и схема callback-payload.

Клавиатура для MAX — список рядов, каждый ряд — список кнопок-словарей.
Лимиты: 210 кнопок, 30 рядов, 7 в ряду.

Схема payload (держим компактной):
  nav:<screen>            — навигация по меню
  help:<page>             — страницы помощи
  set:<action>[:<arg>]    — настройки
  free:<source>           — бесплатные кристаллы
  buy:<pack>              — покупка пакета
  st:<action>:<story_id>  — действия с историей (open/start/cont/restart/restart_yes)
  pl:c:<i>                — выбор варианта №i в текущей сцене
  pl:n                    — кнопка «Дальше»
  pl:menu                 — выход в меню из финала
"""
from __future__ import annotations

from typing import Any

from .engine import Story
from .texts import GEM

Button = dict[str, Any]
Keyboard = list[list[Button]]


def cb(text: str, payload: str) -> Button:
    return {"type": "callback", "text": text, "payload": payload}


def link(text: str, url: str) -> Button:
    return {"type": "link", "text": text, "url": url}


def back(payload: str = "nav:main", text: str = "⬅️ Назад") -> list[Button]:
    return [cb(text, payload)]


# ── Главное меню ─────────────────────────────────────────────
def main_menu() -> Keyboard:
    return [
        [cb("📚 Истории", "nav:stories")],
        [cb(f"{GEM} Кристаллы", "nav:crystals"), cb("🏆 Достижения", "nav:ach")],
        [cb("👤 Профиль", "nav:profile"), cb("⚙️ Настройки", "nav:settings")],
        [cb("❓ Помощь", "nav:help")],
    ]


# ── Истории ──────────────────────────────────────────────────
def stories_list(stories: list[Story]) -> Keyboard:
    kb: Keyboard = []
    for s in stories:
        if s.available:
            label = f"{s.cover} {s.title} · {s.price}{GEM}"
            kb.append([cb(label, f"st:open:{s.id}")])
        else:
            kb.append([cb(f"🔒 {s.title} · скоро", f"st:soon:{s.id}")])
    kb.append(back("nav:main"))
    return kb


def story_card(story: Story, *, unlocked: bool, has_progress: bool, completed: bool) -> Keyboard:
    kb: Keyboard = []
    if not unlocked:
        kb.append([cb(f"🔓 Открыть за {story.price}{GEM}", f"st:start:{story.id}")])
    else:
        if has_progress and not completed:
            kb.append([cb("📖 Продолжить", f"st:cont:{story.id}")])
            kb.append([cb("🔄 Начать заново", f"st:restart:{story.id}")])
        elif completed:
            kb.append([cb("📖 Продолжить", f"st:cont:{story.id}")])
            kb.append([cb("🔄 Пройти заново", f"st:restart:{story.id}")])
        else:
            kb.append([cb("▶️ Начать историю", f"st:start:{story.id}")])
    kb.append(back("nav:stories"))
    return kb


def restart_confirm(story_id: str) -> Keyboard:
    return [
        [cb("✅ Да, начать заново", f"st:restart_yes:{story_id}")],
        [cb("⬅️ Отмена", f"st:open:{story_id}")],
    ]


def story_intro(story_id: str) -> Keyboard:
    return [
        [cb("✍️ Ввести имя героини", f"st:name:{story_id}")],
        [cb("▶️ Начать с «Героиня»", f"st:noname:{story_id}")],
        [cb("⬅️ Назад", f"st:open:{story_id}")],
    ]


def name_skip(story_id: str) -> Keyboard:
    return [[cb("Оставить «Героиня»", f"st:noname:{story_id}")]]


# ── Кристаллы ────────────────────────────────────────────────
def crystals_menu() -> Keyboard:
    return [
        [cb("🎁 Бесплатные кристаллы", "nav:free")],
        [cb("➕ Купить кристаллы", "nav:buy")],
        [cb("📊 История операций", "nav:history")],
        back("nav:main"),
    ]


def free_menu() -> Keyboard:
    return [
        [cb("📅 Ежедневная награда", "free:daily")],
        [cb("📣 Подписаться на канал", "free:sub")],
        [cb("👥 Пригласить друга", "free:invite")],
        back("nav:crystals"),
    ]


def buy_menu() -> Keyboard:
    return [
        [cb(f"{GEM} 50 — 99₽", "buy:50"), cb(f"{GEM} 150 — 249₽", "buy:150")],
        [cb(f"{GEM} 350 — 499₽", "buy:350"), cb(f"{GEM} 1000 — 999₽", "buy:1000")],
        back("nav:crystals"),
    ]


# ── Профиль ──────────────────────────────────────────────────
def profile_menu() -> Keyboard:
    return [
        [cb("🏆 Достижения", "nav:ach"), cb("📖 Мои истории", "nav:mystories")],
        back("nav:main"),
    ]


# ── Настройки ────────────────────────────────────────────────
def settings_menu(notif_on: bool) -> Keyboard:
    notif = "🔔 Уведомления: вкл" if notif_on else "🔕 Уведомления: выкл"
    return [
        [cb("🌍 Язык", "nav:lang")],
        [cb(notif, "set:notif:toggle")],
        [cb("🗑 Сбросить прогресс", "set:reset")],
        [cb("ℹ️ О проекте", "set:about")],
        back("nav:main"),
    ]


def lang_menu() -> Keyboard:
    return [
        [cb("Русский 🇷🇺", "set:lang:ru")],
        [cb("English 🇬🇧", "set:lang:en")],
        [cb("Українська 🇺🇦", "set:lang:uk")],
        back("nav:settings"),
    ]


def reset_confirm() -> Keyboard:
    return [
        [cb("🗑 Да, сбросить всё", "set:reset:yes")],
        back("nav:settings", "⬅️ Отмена"),
    ]


# ── Помощь ───────────────────────────────────────────────────
def help_menu() -> Keyboard:
    return [
        [cb("❔ Как играть", "help:howto")],
        [cb("💎 Как получить кристаллы", "help:crystals")],
        [cb("📨 Связаться с поддержкой", "help:support")],
        [cb("📜 Правила", "help:rules")],
        back("nav:main"),
    ]


# ── Игровой процесс ──────────────────────────────────────────
NUM = ["1️⃣", "2️⃣", "3️⃣", "4️⃣", "5️⃣", "6️⃣", "7️⃣"]


def choice_annotation(story: Story, choice: dict[str, Any]) -> str:
    """Пометка эффектов варианта: «+1🔥», «💎20 +1🔥». Пусто, если эффектов нет."""
    stats = story.stats
    meta: list[str] = []
    cost = int(choice.get("cost", 0))
    if cost > 0:
        meta.append(f"{GEM}{cost}")
    for key, val in (choice.get("fx") or {}).items():
        spec = stats.get(key)
        if spec and spec.get("kind") == "main" and isinstance(val, (int, float)) and val:
            sign = "+" if val > 0 else ""
            meta.append(f"{sign}{val}{spec['emoji']}")
    return " ".join(meta)


def play_choices(story: Story, choices: list[dict[str, Any]]) -> Keyboard:
    """Кнопки вариантов — компактные НОМЕРА (полный текст в теле сообщения, чтобы
    ничего не обрезалось). Премиум-варианты помечаются 💎ценой прямо на кнопке."""
    kb: Keyboard = []
    for i, ch in enumerate(choices):
        label = NUM[i] if i < len(NUM) else f"#{i + 1}"
        cost = int(ch.get("cost", 0))
        if cost > 0:
            label += f" {GEM}{cost}"
        kb.append([cb(label, f"pl:{story.id}:c:{i}")])
    return kb


def play_next(story_id: str) -> Keyboard:
    return [[cb("▶️ Дальше", f"pl:{story_id}:n")]]


# ── Гейт обязательной подписки ───────────────────────────────
def gate(channels: list[dict[str, Any]]) -> Keyboard:
    kb: Keyboard = []
    for ch in channels:
        url = ch.get("link") or ""
        title = ch.get("title") or "Наш канал"
        if url:
            kb.append([link(f"📢 {title}", url)])
    kb.append([cb("✅ Я подписался", "gate:check")])
    return kb


def support(url: str) -> Keyboard:
    return [[link("📨 Написать в поддержку", url)], back("nav:help")]


def play_final(story_id: str) -> Keyboard:
    return [
        [cb("🔄 Пройти заново", f"st:restart_yes:{story_id}")],
        [cb("📚 К историям", "nav:stories"), cb("🏛 В меню", "nav:main")],
    ]

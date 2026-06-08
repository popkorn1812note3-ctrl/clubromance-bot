"""Экраны меню и переходы: истории, кристаллы, профиль, настройки, помощь."""
from __future__ import annotations

import logging
import time
from typing import Any

from . import keyboards as kb
from . import play
from . import texts as t
from .runtime import Context
from .texts import GEM, esc

log = logging.getLogger("menu")

# Размеры наград (см. ТЗ).
REWARD_DAILY = 5
REWARD_SUBSCRIBE = 15
REWARD_INVITE = 20

LANG_NAMES = {"ru": "Русский 🇷🇺", "en": "English 🇬🇧", "uk": "Українська 🇺🇦"}
BUY_PACKS = {"50": "99₽", "150": "249₽", "350": "499₽", "1000": "999₽"}


def _fmt_dur(seconds: int) -> str:
    h = seconds // 3600
    m = (seconds % 3600) // 60
    if h:
        return f"{h} ч {m} мин"
    return f"{m} мин"


def achievement_catalog(ctx: Context) -> dict[str, dict[str, Any]]:
    """Сводный каталог достижений из всех историй."""
    cat: dict[str, dict[str, Any]] = dict(t.GLOBAL_ACHIEVEMENTS)
    for s in ctx.registry.all():
        cat.update(s.achievements)
    return cat


# ── Точки входа ──────────────────────────────────────────────
async def show_welcome(ctx: Context, user_id: int) -> None:
    u = await ctx.db.ensure_user(user_id)
    await ctx.show_screen(
        user_id,
        t.WELCOME.format(crystals=u["crystals"], gem=GEM),
        kb.main_menu(),
        force_new=True,
    )


async def show_main(ctx: Context, user_id: int) -> None:
    await ctx.show_screen(user_id, t.MAIN_MENU, kb.main_menu())


# ── Роутер callback'ов меню ──────────────────────────────────
async def on_callback(ctx: Context, user_id: int, payload: str, callback_id: str) -> None:
    await ctx.db.ensure_user(user_id)
    parts = payload.split(":")
    head = parts[0]
    try:
        if head == "nav":
            await _nav(ctx, user_id, parts, callback_id)
        elif head == "help":
            await _help(ctx, user_id, parts, callback_id)
        elif head == "set":
            await _settings(ctx, user_id, parts, callback_id)
        elif head == "free":
            await _free(ctx, user_id, parts, callback_id)
        elif head == "buy":
            await _buy(ctx, user_id, parts, callback_id)
        elif head == "st":
            await _story(ctx, user_id, parts, callback_id)
        else:
            await ctx.api.answer_callback(callback_id)
    except Exception:  # noqa: BLE001 — не роняем диспетчер из-за одного экрана
        log.exception("ошибка обработки payload %r", payload)
        await ctx.api.answer_callback(callback_id, notification="Что-то пошло не так, попробуйте ещё раз")


# ── Навигация ────────────────────────────────────────────────
async def _nav(ctx: Context, user_id: int, parts: list[str], callback_id: str) -> None:
    screen = parts[1] if len(parts) > 1 else "main"
    await ctx.api.answer_callback(callback_id)
    if screen == "main":
        await show_main(ctx, user_id)
    elif screen == "stories":
        await _screen_stories(ctx, user_id)
    elif screen == "crystals":
        await _screen_crystals(ctx, user_id)
    elif screen == "free":
        await _screen_free(ctx, user_id)
    elif screen == "buy":
        await ctx.show_screen(user_id, "➕ *Купить кристаллы*\n\nВыбери пакет:", kb.buy_menu())
    elif screen == "history":
        await _screen_history(ctx, user_id)
    elif screen == "profile":
        await _screen_profile(ctx, user_id)
    elif screen == "ach":
        await _screen_achievements(ctx, user_id)
    elif screen == "mystories":
        await _screen_mystories(ctx, user_id)
    elif screen == "settings":
        await _screen_settings(ctx, user_id)
    elif screen == "lang":
        await ctx.show_screen(user_id, "🌍 *Выбор языка*\n\n_(локализация контента — в планах; пока истории на русском)_", kb.lang_menu())
    elif screen == "help":
        await ctx.show_screen(user_id, t.HELP_ROOT, kb.help_menu())


# ── Истории ──────────────────────────────────────────────────
async def _screen_stories(ctx: Context, user_id: int) -> None:
    stories = ctx.registry.all()
    if not stories:
        await ctx.show_screen(user_id, "📚 Истории скоро появятся.", kb.main_menu())
        return
    await ctx.show_screen(user_id, "📚 *Истории*\n\nВыбери историю:", kb.stories_list(stories))


async def _story(ctx: Context, user_id: int, parts: list[str], callback_id: str) -> None:
    action = parts[1] if len(parts) > 1 else ""
    sid = parts[2] if len(parts) > 2 else ""

    if action == "soon":
        await ctx.api.answer_callback(callback_id, notification="Эта история скоро выйдет 🔒")
        return

    story = ctx.registry.get(sid)
    if story is None:
        await ctx.api.answer_callback(callback_id, notification="История не найдена")
        return

    if action == "open":
        await ctx.api.answer_callback(callback_id)
        await _story_card(ctx, user_id, sid)
        return

    if action == "start":
        # Открываем (списываем цену при первом запуске), затем показываем вводный экран.
        if not await _ensure_unlocked(ctx, user_id, story, callback_id):
            return
        await ctx.api.answer_callback(callback_id)
        await _story_intro(ctx, user_id, story)
        return

    if action == "cont":
        prog = await ctx.db.get_progress(user_id, sid)
        await ctx.api.answer_callback(callback_id)
        if not prog:
            if await _ensure_unlocked(ctx, user_id, story, None):
                await _story_intro(ctx, user_id, story)
            return
        await play.resume(ctx, user_id, story, prog)
        return

    if action == "restart":
        await ctx.api.answer_callback(callback_id)
        await ctx.show_screen(
            user_id,
            f"🔄 Начать «{esc(story.title)}» заново? Текущий прогресс будет сброшен.",
            kb.restart_confirm(sid),
        )
        return

    if action == "restart_yes":
        await ctx.api.answer_callback(callback_id)
        await ctx.db.delete_progress(user_id, sid)
        await _story_intro(ctx, user_id, story)
        return

    if action == "name":
        await ctx.db.update_user(user_id, pending=f"name:{sid}")
        await ctx.api.answer_callback(callback_id)
        await ctx.show_screen(
            user_id,
            "✍️ Как зовут героиню? Напиши имя сообщением (до 24 символов).\nИли оставь имя по умолчанию:",
            kb.name_skip(sid),
        )
        return

    if action == "noname":
        await ctx.api.answer_callback(callback_id)
        await ctx.db.update_user(user_id, pending=None)
        await _start_game(ctx, user_id, story, "")
        return

    await ctx.api.answer_callback(callback_id)


async def _story_card(ctx: Context, user_id: int, sid: str) -> None:
    story = ctx.registry.get(sid)
    if story is None:
        return
    unlocked = await ctx.db.is_unlocked(user_id, sid)
    prog = await ctx.db.get_progress(user_id, sid)
    completion = await ctx.db.get_completion(user_id, sid)
    has_progress = prog is not None
    completed = (prog is not None and prog["status"] == "completed") or completion is not None

    lines = [f"{story.cover} *{esc(story.title)}*", ""]
    if story.short:
        lines.append(f"_{esc(story.short)}_")
        lines.append("")
    lines.append(story.description)
    lines.append("")
    if unlocked:
        lines.append("✅ История открыта")
    else:
        lines.append(f"Стоимость: *{story.price}* {GEM}")
    if completion:
        lines.append(f"🏁 Пройдена: _{esc(completion['ending_title'] or '—')}_")
        if completion["secret"]:
            lines.append("✉️ Секретная концовка открыта")
    await ctx.show_screen(
        user_id,
        "\n".join(lines),
        kb.story_card(story, unlocked=unlocked, has_progress=has_progress, completed=completed),
    )


async def _ensure_unlocked(ctx: Context, user_id: int, story, callback_id: str | None) -> bool:
    """Гарантирует, что история открыта (списывает цену при первом запуске).
    False — если не хватило кристаллов (показывает экран кристаллов)."""
    sid = story.id
    if await ctx.db.is_unlocked(user_id, sid):
        return True
    if story.price > 0:
        ok = await ctx.db.spend_crystals(user_id, story.price, "spend", f"unlock:{sid}")
        if not ok:
            u = await ctx.db.get_user(user_id)
            bal = u["crystals"] if u else 0
            if callback_id:
                await ctx.api.answer_callback(
                    callback_id, notification=f"Не хватает кристаллов: нужно {story.price}, у тебя {bal}"
                )
            await ctx.show_screen(
                user_id,
                f"💎 Чтобы открыть «{esc(story.title)}», нужно *{story.price}* {GEM}. "
                f"Твой баланс: *{bal}* {GEM}.\nПополни баланс в разделе «Кристаллы».",
                kb.crystals_menu(),
            )
            return False
    await ctx.db.unlock_story(user_id, sid)
    return True


async def _story_intro(ctx: Context, user_id: int, story) -> None:
    """Вводный экран перед стартом: описание, персонажи, характеристики, объём."""
    lines = [f"{story.cover} *{esc(story.title)}*", "", story.description, ""]

    chars = [(name, info) for name, info in story.characters.items() if info.get("desc") or info.get("hero")]
    if chars:
        lines.append("*👥 Персонажи:*")
        for name, info in chars:
            emoji = info.get("emoji", "")
            if info.get("hero"):
                lines.append(f"{emoji} {esc(name)} — это вы")
            else:
                desc = info.get("desc", "")
                lines.append(f"{emoji} *{esc(name)}*" + (f" — {esc(desc)}" if desc else ""))
        lines.append("")

    main = story.main_stats()
    if main:
        lines.append("*🎚 Характеристики:*")
        lines.append(" · ".join(f"{s['emoji']} {esc(s['name'])}" for _, s in main))
    lines.append("")
    lines.append(f"📖 Объём: *{story.chapter_count()}* глав · *{story.scene_count()}* сцен")
    lines.append("")
    lines.append("_Каждый выбор меняет характеристики, отношения и финал._")

    await ctx.show_screen(user_id, "\n".join(lines), kb.story_intro(story.id))


async def _start_game(ctx: Context, user_id: int, story, hero: str, *, reanchor: bool = False) -> None:
    variables = story.init_vars()
    if hero:
        variables["_hero"] = hero
    await play.enter(ctx, user_id, story, story.start_scene, variables, prev_chapter=None, reanchor=reanchor)


async def apply_hero_name(ctx: Context, user_id: int, text: str) -> bool:
    """Если юзер вводил имя героини — применяет его и стартует историю. True — обработано."""
    u = await ctx.db.get_user(user_id)
    pending = (u.get("pending") if u else "") or ""
    if not pending.startswith("name:"):
        return False
    sid = pending.split(":", 1)[1]
    await ctx.db.update_user(user_id, pending=None)
    story = ctx.registry.get(sid)
    if story is None:
        return True
    name = (text or "").strip()[:24] or "Героиня"
    # Имя пришло текстом — заякориваем сцену новым сообщением (под текстом юзера).
    await _start_game(ctx, user_id, story, name, reanchor=True)
    return True


# ── Кристаллы ────────────────────────────────────────────────
async def _screen_crystals(ctx: Context, user_id: int) -> None:
    u = await ctx.db.get_user(user_id)
    bal = u["crystals"] if u else 0
    await ctx.show_screen(user_id, t.CRYSTALS_TITLE.format(bal=bal, gem=GEM), kb.crystals_menu())


async def _screen_free(ctx: Context, user_id: int) -> None:
    u = await ctx.db.get_user(user_id)
    bal = u["crystals"] if u else 0
    body = (
        t.FREE_TITLE.format(bal=bal, gem=GEM)
        + f"\n\n📅 Ежедневно: +{REWARD_DAILY} {GEM}"
        + f"\n📣 Подписка на канал: +{REWARD_SUBSCRIBE} {GEM}"
        + f"\n👥 Друг по приглашению: +{REWARD_INVITE} {GEM}"
    )
    await ctx.show_screen(user_id, body, kb.free_menu())


async def _free(ctx: Context, user_id: int, parts: list[str], callback_id: str) -> None:
    source = parts[1] if len(parts) > 1 else ""
    if source == "daily":
        ok, bal, wait = await ctx.db.claim_daily(user_id, REWARD_DAILY)
        await ctx.api.answer_callback(callback_id)
        if ok:
            await ctx.show_screen(user_id, f"📅 +{REWARD_DAILY} {GEM} начислено!\nБаланс: *{bal}* {GEM}\nПриходи завтра за новой наградой.", kb.free_menu())
        else:
            await ctx.show_screen(user_id, f"📅 Сегодня награда уже получена.\nСледующая — через *{_fmt_dur(wait)}*.\nБаланс: *{bal}* {GEM}", kb.free_menu())
        return
    if source == "sub":
        ok, bal = await ctx.db.claim_subscribe(user_id, REWARD_SUBSCRIBE)
        await ctx.api.answer_callback(callback_id)
        if ok:
            await ctx.show_screen(
                user_id,
                f"📣 +{REWARD_SUBSCRIBE} {GEM} за подписку!\nБаланс: *{bal}* {GEM}\n\n"
                "_(Реальную проверку подписки на канал подключим, когда бот станет админом канала.)_",
                kb.free_menu(),
            )
        else:
            await ctx.show_screen(user_id, f"📣 Бонус за подписку уже был получен ранее.\nБаланс: *{bal}* {GEM}", kb.free_menu())
        return
    if source == "invite":
        await ctx.api.answer_callback(callback_id)
        cnt = await ctx.db.count_referrals(user_id)
        link = ctx.deep_link(f"ref_{user_id}")
        await ctx.show_screen(
            user_id,
            f"👥 *Пригласи друга*\n\nЗа каждого нового друга по твоей ссылке — *+{REWARD_INVITE}* {GEM}.\n\n"
            f"Твоя ссылка:\n`{link}`\n\nПриглашено друзей: *{cnt}*",
            [[kb.cb("⬅️ Назад", "nav:free")]],
        )
        return
    await ctx.api.answer_callback(callback_id)


async def _buy(ctx: Context, user_id: int, parts: list[str], callback_id: str) -> None:
    pack = parts[1] if len(parts) > 1 else ""
    price = BUY_PACKS.get(pack)
    if not price:
        await ctx.api.answer_callback(callback_id)
        return
    await ctx.api.answer_callback(callback_id, notification="Оплата скоро будет подключена")
    await ctx.show_screen(
        user_id,
        f"➕ *Пакет {pack}* {GEM} — {price}\n\n"
        "💳 Приём платежей пока *не подключён* (заглушка). "
        "Подключим отдельной итерацией — здесь появится переход на оплату.\n\n"
        f"А пока кристаллы можно получить бесплатно 👇",
        kb.free_menu(),
    )


async def _screen_history(ctx: Context, user_id: int) -> None:
    rows = await ctx.db.recent_ledger(user_id, 10)
    if not rows:
        await ctx.show_screen(user_id, "📊 *История операций*\n\nПока пусто.", [[kb.cb("⬅️ Назад", "nav:crystals")]])
        return
    names = {
        "grant": "Бонус", "daily": "Ежедневная", "subscribe": "Подписка",
        "invite": "Реферал", "spend": "Списание", "buy": "Покупка",
    }
    lines = ["📊 *История операций*", ""]
    for r in rows:
        ts = time.strftime("%d.%m %H:%M", time.localtime(r["created_at"]))
        sign = "＋" if r["amount"] >= 0 else "−"
        lines.append(f"{ts}  {sign}{abs(r['amount'])} {GEM}  · {names.get(r['type'], r['type'])}")
    await ctx.show_screen(user_id, "\n".join(lines), [[kb.cb("⬅️ Назад", "nav:crystals")]])


# ── Профиль ──────────────────────────────────────────────────
async def _screen_profile(ctx: Context, user_id: int) -> None:
    u = await ctx.db.get_user(user_id)
    st = await ctx.db.stats(user_id)
    name = (u["name"] if u and u["name"] else "Игрок")
    body = (
        "👤 *Профиль*\n\n"
        f"Имя: *{esc(name)}*\n"
        f"ID: `{user_id}`\n"
        f"Баланс: *{u['crystals'] if u else 0}* {GEM}\n\n"
        f"📖 Историй пройдено: *{st['completed']}*\n"
        f"✉️ Секретных концовок: *{st['secret']}*\n"
        f"🏆 Достижений: *{st['achievements']}*"
    )
    await ctx.show_screen(user_id, body, kb.profile_menu())


async def _screen_achievements(ctx: Context, user_id: int) -> None:
    cat = achievement_catalog(ctx)
    unlocked = await ctx.db.list_achievements(user_id)
    lines = ["🏆 *Достижения*", ""]
    if not cat:
        lines.append("Пока нет достижений.")
    for code, spec in cat.items():
        emoji = spec.get("emoji", "🏆")
        title = spec.get("title", code)
        if code in unlocked:
            lines.append(f"{emoji} *{esc(title)}* — открыто")
        else:
            lines.append(f"🔒 _{esc(title)}_")
    lines.append("")
    lines.append(f"Открыто: *{len(unlocked & set(cat))}* из *{len(cat)}*  ·  _за каждое +1_ {GEM}")
    await ctx.show_screen(user_id, "\n".join(lines), [[kb.cb("📚 К историям", "nav:stories")], [kb.cb("⬅️ В меню", "nav:main")]])


async def _screen_mystories(ctx: Context, user_id: int) -> None:
    lines = ["📖 *Мои истории*", ""]
    for s in ctx.registry.all():
        if not s.available:
            lines.append(f"🔒 {esc(s.title)} — скоро")
            continue
        prog = await ctx.db.get_progress(user_id, s.id)
        comp = await ctx.db.get_completion(user_id, s.id)
        if comp:
            mark = "✅"
            extra = f" — _{esc(comp['ending_title'] or 'пройдена')}_"
        elif prog:
            mark = "📖"
            extra = f" — _{esc(prog.get('current_chapter') or 'в процессе')}_"
        elif await ctx.db.is_unlocked(user_id, s.id):
            mark = "🔓"
            extra = " — открыта"
        else:
            mark = "▫️"
            extra = ""
        lines.append(f"{mark} {esc(s.title)}{extra}")
    await ctx.show_screen(user_id, "\n".join(lines), [[kb.cb("📚 К историям", "nav:stories")], [kb.cb("⬅️ Назад", "nav:profile")]])


# ── Настройки ────────────────────────────────────────────────
async def _screen_settings(ctx: Context, user_id: int) -> None:
    u = await ctx.db.get_user(user_id)
    notif = bool(u["notifications"]) if u else True
    lang = LANG_NAMES.get(u["language"] if u else "ru", "Русский 🇷🇺")
    await ctx.show_screen(
        user_id,
        f"{t.SETTINGS}\n\nЯзык: *{lang}*\n" + (t.NOTIF_ON if notif else t.NOTIF_OFF),
        kb.settings_menu(notif),
    )


async def _settings(ctx: Context, user_id: int, parts: list[str], callback_id: str) -> None:
    action = parts[1] if len(parts) > 1 else ""
    if action == "notif":
        u = await ctx.db.get_user(user_id)
        new = 0 if (u and u["notifications"]) else 1
        await ctx.db.update_user(user_id, notifications=new)
        await ctx.api.answer_callback(callback_id, notification="Уведомления " + ("включены" if new else "выключены"))
        await _screen_settings(ctx, user_id)
        return
    if action == "lang":
        lang = parts[2] if len(parts) > 2 else "ru"
        await ctx.db.update_user(user_id, language=lang)
        await ctx.api.answer_callback(callback_id, notification=f"Язык: {LANG_NAMES.get(lang, lang)}")
        await _screen_settings(ctx, user_id)
        return
    if action == "reset":
        if len(parts) > 2 and parts[2] == "yes":
            await ctx.db.delete_progress(user_id)
            await ctx.api.answer_callback(callback_id, notification="Прогресс сброшен")
            await ctx.show_screen(user_id, "🗑 Прогресс по всем историям сброшен.\n_(Кристаллы и открытые истории сохранены.)_", kb.main_menu())
        else:
            await ctx.api.answer_callback(callback_id)
            await ctx.show_screen(
                user_id,
                "🗑 *Сбросить прогресс?*\n\nБудет удалён прогресс по *всем* историям. Это необратимо.\n"
                "_(Баланс кристаллов и открытые истории останутся.)_",
                kb.reset_confirm(),
            )
        return
    if action == "about":
        await ctx.api.answer_callback(callback_id)
        await ctx.show_screen(user_id, t.ABOUT, [[kb.cb("⬅️ Назад", "nav:settings")]])
        return
    await ctx.api.answer_callback(callback_id)


# ── Помощь ───────────────────────────────────────────────────
async def _help(ctx: Context, user_id: int, parts: list[str], callback_id: str) -> None:
    page = parts[1] if len(parts) > 1 else ""
    await ctx.api.answer_callback(callback_id)
    body = {
        "howto": t.HELP_HOWTO,
        "crystals": t.HELP_CRYSTALS,
        "support": t.HELP_SUPPORT,
        "rules": t.HELP_RULES,
    }.get(page, t.HELP_ROOT)
    await ctx.show_screen(user_id, body, [[kb.cb("⬅️ Назад", "nav:help")]])

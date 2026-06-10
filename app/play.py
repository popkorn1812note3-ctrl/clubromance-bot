"""Игровой процесс: рендер сцен, продвижение по сюжету, обработка выборов."""
from __future__ import annotations

import logging
from typing import Any

from . import keyboards as kb
from .engine import (
    Story,
    apply_effects,
    eval_group,
    evaluate_achievements,
    resolve_next,
    visible_blocks,
    visible_choices,
)
from .runtime import Context
from .texts import GEM, esc

log = logging.getLogger("play")

MSG_LIMIT = 3500       # безопасный порог длины одного сообщения
NARRATORS = ("Рассказчик", "Narrator", "")
SEP = "┄┄┄┄┄┄┄┄┄┄┄"
ACHIEVEMENT_REWARD = 1  # +1 💎 за каждое достижение
CHAPTER_REWARD = 1      # +1 💎 за первый вход в новую главу (как в Клубе Романтики)


# ── Подстановка имени героини ────────────────────────────────
def hero_name(variables: dict[str, Any]) -> str:
    return str(variables.get("_hero") or "Героиня")


def subst(text: str, hero: str) -> str:
    return (text or "").replace("{hero}", hero)


# ── Шапка интерфейса (баланс + характеристики) ───────────────
def status_line(story: Story, variables: dict[str, Any], balance: int) -> str:
    # HUD: баланс + только основные характеристики (без романтических метров —
    # они отдельные «переменные отношений», в хабе их не показываем).
    parts = [f"{GEM} {balance}"]
    for key, spec in story.main_stats():
        parts.append(f"{spec['emoji']} {variables.get(key, 0)}")
    return "  ·  ".join(parts)


# ── Реплики персонажей ───────────────────────────────────────
def fmt_speaker(story: Story, speaker: str, hero: str) -> str:
    """«🖤 Дэмиан» с эмодзи персонажа; имя героини подменяется на выбранное."""
    phone = speaker.endswith("📱")
    base = speaker[:-1].rstrip() if phone else speaker
    info = story.characters.get(base, {})
    if info.get("hero") or base == "Героиня":
        display, emoji = hero, info.get("emoji", "🧑‍💼")
    else:
        display, emoji = base, info.get("emoji", "")
    label = f"{emoji} {esc(display)}".strip()
    if phone:
        label += " 📱"
    return f"*{label}:*"


def render_scene(story: Story, scene: dict[str, Any], vblocks: list[dict[str, Any]],
                 vchoices: list[dict[str, Any]], variables: dict[str, Any], balance: int) -> str:
    """Полный текст сообщения сцены: шапка-интерфейс + локация + реплики + варианты."""
    hero = hero_name(variables)
    body: list[str] = []
    title = scene.get("title")
    if title:
        body.append(f"📍 *{esc(subst(title, hero))}*")
    for b in vblocks:
        sp = b.get("speaker")
        txt = subst(b["text"], hero)
        if not sp or sp in NARRATORS:
            body.append(txt)
        else:
            body.append(f"{fmt_speaker(story, sp, hero)} {txt}")
    if vchoices:
        # Полный текст вариантов — в теле (на кнопках только номера, чтобы не обрезалось).
        lines = ["*— Твой выбор: —*"]
        for i, ch in enumerate(vchoices):
            num = kb.NUM[i] if i < len(kb.NUM) else f"#{i + 1}"
            ann = kb.choice_annotation(story, ch)
            lines.append(f"{num} {subst(ch['text'], hero)}" + (f"  _({ann})_" if ann else ""))
        body.append("\n".join(lines))
    header = status_line(story, variables, balance)
    return f"{header}\n{SEP}\n\n" + "\n\n".join(body)


def _chunk(text: str, limit: int = MSG_LIMIT) -> list[str]:
    """Делит длинный текст по границам абзацев."""
    if len(text) <= limit:
        return [text]
    chunks: list[str] = []
    buf = ""
    for para in text.split("\n\n"):
        candidate = f"{buf}\n\n{para}" if buf else para
        if len(candidate) > limit and buf:
            chunks.append(buf)
            buf = para
        else:
            buf = candidate
    if buf:
        chunks.append(buf)
    return chunks


def _keyboard_for(story: Story, scene: dict[str, Any], vchoices: list[dict[str, Any]],
                  variables: dict[str, Any]) -> list[list[dict[str, Any]]]:
    if vchoices:
        return kb.play_choices(story, vchoices)
    if scene.get("final"):
        return kb.play_final(story.id)
    if resolve_next(story, scene, variables):
        return kb.play_next(story.id)
    return kb.play_final(story.id)


async def resolve_image(ctx: Context, story: Story, scene_id: str, scene: dict[str, Any]):
    """Картинка сцены: override конкретной сцены > фон локации. payload или None."""
    photos = await ctx.db.get_image(f"scene:{story.id}:{scene_id}")
    if photos is None and scene.get("bg"):
        photos = await ctx.db.get_image(f"bg:{story.id}:{scene['bg']}")
    return {"photos": photos} if photos else None


# ── Достижения / завершение ──────────────────────────────────
async def _award_new(ctx: Context, user_id: int, story: Story, variables: dict[str, Any]) -> list[dict[str, Any]]:
    """Награждает новыми достижениями (+1 💎 за каждое). Возвращает их спеки."""
    newly: list[dict[str, Any]] = []
    for code in evaluate_achievements(story, variables):
        if await ctx.db.award_achievement(user_id, code):
            await ctx.db.add_crystals(user_id, ACHIEVEMENT_REWARD, "achievement", code)
            spec = dict(story.achievements.get(code, {}))
            spec["code"] = code
            newly.append(spec)
    return newly


async def _handle_complete(ctx: Context, user_id: int, story: Story, scene: dict[str, Any],
                           variables: dict[str, Any]) -> None:
    variables["_completed"] = 1
    spec = scene["complete"]
    titles: list[str] = []
    for gname in spec.get("groups", []):
        opt = eval_group(story, gname, variables)
        if opt:
            titles.append(opt["title"])
    code = ""
    if spec.get("code_from"):
        opt = eval_group(story, spec["code_from"], variables)
        code = opt["code"] if opt else ""
    await ctx.db.mark_completed(user_id, story.id, code, " · ".join(titles), secret=False)


# ── Войти в сцену и отрендерить ──────────────────────────────
def _ach_toast(newly: list[dict[str, Any]]) -> str | None:
    """Короткая всплывашка о новых достижениях (для answer_callback)."""
    if not newly:
        return None
    if len(newly) == 1:
        a = newly[0]
        return f"🏆 {a.get('title', a['code'])}  +{ACHIEVEMENT_REWARD}💎"
    return f"🏆 +{len(newly)} достижения  (+{len(newly) * ACHIEVEMENT_REWARD}💎)"


async def enter(ctx: Context, user_id: int, story: Story, scene_id: str,
                variables: dict[str, Any], *, prev_chapter: str | None,
                reanchor: bool = False) -> list[dict[str, Any]]:
    """Входит в сцену, проходит сквозь маршрутные сцены, рендерит первую
    содержательную/терминальную сцену (редактируя ОДИН экран) и сохраняет прогресс.
    Возвращает список новых достижений (для всплывашки у вызывающего)."""
    newly: list[dict[str, Any]] = []
    chapter = prev_chapter
    new_chapter_banner: str | None = None
    guard = 0
    current = scene_id

    while True:
        guard += 1
        if guard > 200:
            log.error("зациклились на сцене %s (story %s)", current, story.id)
            await ctx.send(user_id, "⚠️ Произошла ошибка сюжета. Попробуйте начать историю заново.")
            return
        scene = story.scene(current)
        if scene is None:
            log.error("нет сцены %s (story %s)", current, story.id)
            await ctx.send(user_id, "⚠️ Сцена не найдена. Попробуйте начать историю заново.")
            return

        apply_effects(variables, scene.get("fx"))
        vblocks = visible_blocks(scene, variables)
        for b in vblocks:
            if b.get("fx"):
                apply_effects(variables, b["fx"])

        if scene.get("complete"):
            await _handle_complete(ctx, user_id, story, scene, variables)
        if scene.get("secret"):
            variables["secret_ending_unlocked"] = 1
            await ctx.db.mark_secret(user_id, story.id)

        newly += await _award_new(ctx, user_id, story, variables)

        if scene.get("chapter") and scene["chapter"] != chapter:
            new_chapter_banner = scene["chapter"]
            chapter = scene["chapter"]

        vchoices = visible_choices(scene, variables)
        renderable = bool(vblocks) or bool(vchoices) or scene.get("final")
        if not renderable:
            nxt = resolve_next(story, scene, variables)
            if nxt:
                current = nxt
                continue
        break

    chapter_bonus = 0
    if new_chapter_banner and await ctx.db.award_chapter(
        user_id, story.id, new_chapter_banner, CHAPTER_REWARD
    ):
        chapter_bonus = CHAPTER_REWARD

    user = await ctx.db.get_user(user_id)
    balance = int(user["crystals"]) if user else 0
    keyboard = _keyboard_for(story, scene, vchoices, variables)
    text = render_scene(story, scene, vblocks, vchoices, variables, balance)
    if new_chapter_banner:  # баннер главы — в шапку того же сообщения
        bonus = f"  ·  +{chapter_bonus}{GEM}" if chapter_bonus else ""
        text = f"📖 *{esc(new_chapter_banner)}*{bonus}\n\n{text}"
    if newly:  # достижение — заметным баннером в шапке сцены (плюс всплывашка)
        ach = " · ".join(f"{a.get('emoji', '🏆')} {esc(a.get('title', a['code']))}" for a in newly)
        text = f"🏆 *Новое достижение!*  {ach}  +{len(newly)}{GEM}\n\n{text}"
    image = await resolve_image(ctx, story, current, scene)
    # Редактируем ОДИН экран на месте (или присылаем новый — после текста юзера).
    await ctx.show_screen(user_id, text, keyboard, image=image, force_new=reanchor)

    status = "completed" if variables.get("_completed") else "in_progress"
    await ctx.db.save_progress(user_id, story.id, current, chapter, variables, status)
    return newly


async def resume(ctx: Context, user_id: int, story: Story, prog: dict[str, Any],
                 *, reanchor: bool = False) -> None:
    """Показывает текущую сцену прогресса БЕЗ повторного применения эффектов."""
    variables = prog["vars"]
    scene = story.scene(prog["current_scene"])
    if scene is None:
        await ctx.show_screen(user_id, "⚠️ Сцена не найдена. Начните историю заново.")
        return
    vblocks = visible_blocks(scene, variables)
    vchoices = visible_choices(scene, variables)
    user = await ctx.db.get_user(user_id)
    balance = int(user["crystals"]) if user else 0
    keyboard = _keyboard_for(story, scene, vchoices, variables)
    text = render_scene(story, scene, vblocks, vchoices, variables, balance)
    chap = prog.get("current_chapter")
    if chap:
        text = f"📖 *{esc(chap)}* — продолжаем\n\n{text}"
    image = await resolve_image(ctx, story, prog["current_scene"], scene)
    await ctx.show_screen(user_id, text, keyboard, image=image, force_new=reanchor)


# ── Обработка игровых callback'ов ────────────────────────────
async def on_callback(ctx: Context, user_id: int, parts: list[str], callback_id: str) -> None:
    """parts — payload, разбитый по ':' :  ['pl', '<sid>', 'c'|'n', ('<i>')]."""
    sid = parts[1] if len(parts) > 1 else ""
    story = ctx.registry.get(sid)
    if story is None:
        await ctx.api.answer_callback(callback_id, notification="История не найдена")
        return
    prog = await ctx.db.get_progress(user_id, sid)
    if prog is None:
        await ctx.api.answer_callback(callback_id, notification="Прогресс не найден — начните историю заново")
        return

    variables = prog["vars"]
    scene_id = prog["current_scene"]
    scene = story.scene(scene_id)
    if scene is None:
        await ctx.api.answer_callback(callback_id, notification="Сцена не найдена")
        return

    action = parts[2] if len(parts) > 2 else "n"

    if action == "n":
        target = resolve_next(story, scene, variables)
        if not target:
            await ctx.api.answer_callback(callback_id)
            return
        newly = await enter(ctx, user_id, story, target, variables, prev_chapter=prog["current_chapter"])
        await ctx.api.answer_callback(callback_id, notification=_ach_toast(newly))
        return

    if action == "c":
        try:
            i = int(parts[3])
        except (IndexError, ValueError):
            await ctx.api.answer_callback(callback_id, notification="Некорректный выбор")
            return
        vchoices = visible_choices(scene, variables)
        if not (0 <= i < len(vchoices)):
            await ctx.api.answer_callback(callback_id, notification="Этот вариант больше недоступен")
            return
        choice = vchoices[i]
        cost = int(choice.get("cost", 0))
        if cost > 0:
            ok = await ctx.db.spend_crystals(user_id, cost, "spend", f"{sid}:{scene_id}")
            if not ok:
                u = await ctx.db.get_user(user_id)
                bal = u["crystals"] if u else 0
                await ctx.api.answer_callback(
                    callback_id,
                    notification=f"Не хватает кристаллов: нужно {cost}, у тебя {bal}. Выбери бесплатный вариант или пополни баланс.",
                )
                return
        # «X% игроков выбрали так же» — считаем по стабильному индексу в ПОЛНОМ списке
        # choices (видимый индекс i зависит от переменных конкретного игрока).
        pct: int | None = None
        stable_i = next((j for j, c in enumerate(scene.get("choices", [])) if c is choice), None)
        if stable_i is not None:
            await ctx.db.bump_choice(sid, scene_id, stable_i)
            pct = await ctx.db.choice_percent(sid, scene_id, stable_i)
        apply_effects(variables, choice.get("fx"))
        newly = await enter(ctx, user_id, story, choice["goto"], variables, prev_chapter=prog["current_chapter"])
        # Достижение в тосте приоритетнее; иначе — социальное «так же выбрали N%».
        toast = _ach_toast(newly) or (f"💬 Так же выбрали {pct}% игроков" if pct is not None else None)
        await ctx.api.answer_callback(callback_id, notification=toast)
        return

    await ctx.api.answer_callback(callback_id)

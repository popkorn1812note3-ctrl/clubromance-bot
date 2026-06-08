#!/usr/bin/env python
"""Веб-админка ClubRomance: картинки сцен (фоны по локациям + override) и каналы ОП.

  uvicorn admin_server:app --host 0.0.0.0 --port 8080
  # или:  python admin_server.py

Доступ: HTTP Basic Auth (ADMIN_USER / ADMIN_PASSWORD из .env). Делит БД с ботом
(SQLite WAL), картинки грузит в MAX и сохраняет локальную копию для превью.
"""
from __future__ import annotations

import html
import logging
import secrets
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials

from app.bootstrap import build_context, setup_logging
from app.config import ROOT, load_config
from app.runtime import Context

log = logging.getLogger("admin")
cfg = load_config()
setup_logging(cfg.log_level)

UPLOAD_DIR = ROOT / "data" / "uploads"
_ctx: Context | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _ctx
    if not cfg.token:
        raise SystemExit("❌ MAX_BOT_TOKEN не задан.")
    if not cfg.admin_password:
        raise SystemExit("❌ ADMIN_PASSWORD не задан — задайте в .env (иначе вход без пароля).")
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    _ctx = await build_context(cfg, fetch_identity=False)
    yield
    await _ctx.api.close()
    await _ctx.db.close()


app = FastAPI(title="ClubRomance Admin", lifespan=lifespan)
security = HTTPBasic()


def auth(creds: HTTPBasicCredentials = Depends(security)) -> str:
    ok = (
        secrets.compare_digest(creds.username, cfg.admin_user)
        and bool(cfg.admin_password)
        and secrets.compare_digest(creds.password, cfg.admin_password)
    )
    if not ok:
        raise HTTPException(status_code=401, detail="auth", headers={"WWW-Authenticate": "Basic"})
    return creds.username


def ctx() -> Context:
    assert _ctx is not None
    return _ctx


# ── HTML ─────────────────────────────────────────────────────
CSS = """
*{box-sizing:border-box} body{margin:0;background:#14121a;color:#e8e4f0;font:15px/1.5 system-ui,Segoe UI,Roboto,sans-serif}
.wrap{max-width:900px;margin:0 auto;padding:18px}
a{color:#c9a6ff;text-decoration:none} a:hover{text-decoration:underline}
h1{font-size:22px} h2{font-size:18px;margin-top:26px;border-bottom:1px solid #2e2a3a;padding-bottom:6px}
.card{background:#1d1a26;border:1px solid #2e2a3a;border-radius:10px;padding:12px 14px;margin:10px 0}
.row{display:flex;gap:12px;align-items:center;flex-wrap:wrap}
.muted{color:#9a93ad;font-size:13px}
.tag{display:inline-block;background:#2a2536;border-radius:6px;padding:1px 8px;font-size:12px;color:#c9a6ff}
img.prev{max-height:84px;border-radius:8px;border:1px solid #3a3450}
input,button{font:inherit} input[type=file]{color:#9a93ad;max-width:200px}
input[type=text],input[type=number]{background:#14121a;border:1px solid #3a3450;color:#e8e4f0;border-radius:7px;padding:7px 9px}
button{background:#7b4dff;color:#fff;border:0;border-radius:7px;padding:7px 14px;cursor:pointer}
button.del{background:#3a3450} button:hover{filter:brightness(1.1)}
.ok{color:#7ee08a} .no{color:#6c6580}
form.inline{display:inline}
"""


def page(title: str, body: str) -> HTMLResponse:
    return HTMLResponse(
        f"<!doctype html><html><head><meta charset=utf-8>"
        f"<meta name=viewport content='width=device-width,initial-scale=1'>"
        f"<title>{html.escape(title)}</title><style>{CSS}</style></head>"
        f"<body><div class=wrap>{body}</div></body></html>"
    )


def esc(s) -> str:
    return html.escape(str(s or ""))


def _img_block(key: str, present: bool) -> str:
    """Превью + форма загрузки/удаления для одного ключа картинки."""
    if present:
        prev = f"<img class=prev src='/preview/{esc(key)}?'>"
        status = "<span class=ok>✅ загружено</span>"
        delbtn = (
            f"<form class=inline method=post action='/img/delete'>"
            f"<input type=hidden name=key value='{esc(key)}'>"
            f"<button class=del>Удалить</button></form>"
        )
    else:
        prev, status, delbtn = "", "<span class=no>— нет картинки</span>", ""
    up = (
        f"<form class=inline method=post action='/upload' enctype='multipart/form-data'>"
        f"<input type=hidden name=key value='{esc(key)}'>"
        f"<input type=file name=file accept='image/*' required> "
        f"<button>Загрузить</button></form>"
    )
    return f"<div class=row>{prev}<div>{status}<br>{up} {delbtn}</div></div>"


# ── Роуты ────────────────────────────────────────────────────
@app.get("/", response_class=HTMLResponse)
async def dashboard(_: str = Depends(auth)):
    c = ctx()
    stories = c.registry.all()
    s_html = "".join(
        f"<div class=card><a href='/story/{esc(s.id)}'><b>{esc(s.cover)} {esc(s.title)}</b></a> "
        f"<span class=muted>{s.scene_count()} сцен</span></div>"
        for s in stories
    )
    channels = await c.db.list_required_channels()
    ch_rows = "".join(
        f"<div class=card><div class=row><div><b>{esc(ch['title'] or 'Канал')}</b> "
        f"<span class=muted>chat_id {ch['chat_id']}</span><br>"
        f"<span class=muted>{esc(ch['link'] or 'без ссылки')}</span></div>"
        f"<form class=inline method=post action='/channels/{ch['chat_id']}/delete'>"
        f"<button class=del>Удалить</button></form></div></div>"
        for ch in channels
    ) or "<p class=muted>Каналов нет. Добавь бота админом в канал — он зарегистрируется сам, либо добавь вручную ниже.</p>"
    add_ch = (
        "<form method=post action='/channels/add' class=card>"
        "<div class=row><input type=number name=chat_id placeholder='chat_id (напр. -7488…)' required>"
        "<input type=text name=title placeholder='Название'>"
        "<input type=text name=link placeholder='https://max.ru/...'>"
        "<button>Добавить канал</button></div>"
        "<div class=muted>chat_id берётся из логов при добавлении бота в канал. Бот должен быть АДМИНОМ канала.</div></form>"
    )
    body = (
        "<h1>🛠 ClubRomance — админка</h1>"
        "<p><a href='/stats'><b>📊 Статистика обязательной подписки →</b></a></p>"
        "<h2>📚 Истории (картинки)</h2>" + s_html +
        "<h2>🔒 Каналы обязательной подписки</h2>" + ch_rows + add_ch
    )
    return page("ClubRomance Admin", body)


@app.get("/stats", response_class=HTMLResponse)
async def stats_page(_: str = Depends(auth)):
    c = ctx()
    st = await c.db.gate_stats()
    channels = await c.db.list_required_channels()
    gate_on = bool(channels)
    conv = f"{round(100 * st['passed'] / st['total'])}%" if st["total"] else "—"

    funnel = (
        "<div class=card>"
        f"<div class=row><b style='font-size:22px'>👥 {st['total']}</b> <span class=muted>всего пользователей зашло в бота</span></div>"
        f"<div class=row><b style='font-size:22px' class=ok>🔓 {st['passed']}</b> "
        f"<span class=muted>прошли обязательную подписку · конверсия {conv}</span></div>"
        + (f"<div class=row><b style='font-size:22px'>⏳ {st['stuck']}</b> <span class=muted>ещё не подписались</span></div>" if gate_on else "")
        + f"<div class=muted style='margin-top:8px'>🆕 новых сегодня: {st['today']} (прошли {st['today_passed']}) · "
        f"за 7 дней: {st['week']} (прошли {st['week_passed']})</div>"
        "</div>"
    )
    if not gate_on:
        funnel += ("<p class=muted>⚠️ Гейт сейчас выключен — каналов нет. Цифра «прошли» "
                   "станет осмысленной после добавления канала (на главной или добавь бота админом в канал).</p>")

    ch_html = ""
    for ch in channels:
        size = "?"
        admin = False
        try:
            chat = await c.api.get_chat(ch["chat_id"])
            size = chat.get("participants_count", "?")
        except Exception:  # noqa: BLE001
            pass
        try:
            me = await c.api.get_my_membership(ch["chat_id"])
            admin = bool(me.get("is_admin") or me.get("is_owner"))
        except Exception:  # noqa: BLE001
            admin = False
        badge = ("<span class=ok>✅ бот админ — подписка проверяется</span>" if admin
                 else "<span class=no>⚠️ бот НЕ админ — подписку проверить нельзя (добавь бота админом)</span>")
        ch_html += (f"<div class=card><b>📢 {esc(ch['title'] or 'Канал')}</b> "
                    f"<span class=muted>{esc(size)} подписчиков · chat_id {ch['chat_id']}</span><br>{badge}</div>")
    if not channels:
        ch_html = "<p class=muted>Каналов нет.</p>"

    body = (
        "<p><a href='/'>← назад</a></p><h1>📊 Статистика обязательной подписки</h1>"
        "<h2>Воронка</h2>" + funnel +
        "<h2>Каналы</h2>" + ch_html
    )
    return page("Статистика ОП", body)


@app.get("/story/{sid}", response_class=HTMLResponse)
async def story_page(sid: str, _: str = Depends(auth)):
    c = ctx()
    story = c.registry.get(sid)
    if story is None:
        raise HTTPException(404)
    scenes = story.data.get("scenes", {})
    keys = await c.db.image_keys()

    # Локации (bg) → какие сцены используют
    locs: dict[str, int] = {}
    for sc in scenes.values():
        bg = sc.get("bg")
        if bg:
            locs[bg] = locs.get(bg, 0) + 1
    for k in story.data.get("backgrounds", {}):
        locs.setdefault(k, 0)
    loc_html = ""
    for bg in sorted(locs):
        key = f"bg:{sid}:{bg}"
        loc_html += (
            f"<div class=card><b>📍 {esc(bg)}</b> <span class=muted>({locs[bg]} сцен)</span>"
            f"{_img_block(key, key in keys)}</div>"
        )

    # Сцены (override), сгруппированы по главам
    sc_html = ""
    chapter = ""
    for scid, sc in scenes.items():
        if sc.get("chapter"):
            chapter = sc["chapter"]
            sc_html += f"<h3>{esc(chapter)}</h3>"
        first = next((b.get("text") for b in sc.get("blocks", []) if b.get("text")), "")
        key = f"scene:{sid}:{scid}"
        title = sc.get("title") or scid
        sc_html += (
            f"<div class=card><b>{esc(title)}</b> <span class=tag>{esc(scid)}</span> "
            f"<span class=muted>фон: {esc(sc.get('bg') or '—')}</span>"
            f"<div class=muted>{esc(first[:90])}</div>"
            f"{_img_block(key, key in keys)}</div>"
        )

    body = (
        f"<p><a href='/'>← назад</a></p><h1>{esc(story.cover)} {esc(story.title)}</h1>"
        "<h2>🖼 Фоны по локациям</h2>"
        "<p class=muted>Одна картинка на локацию — бот ставит её фоном во всех сценах этого места.</p>"
        + loc_html +
        "<h2>🎬 Картинки к сценам (override)</h2>"
        "<p class=muted>Перебивает фон локации для конкретной сцены — для ключевых моментов.</p>"
        + sc_html
    )
    return page(f"{story.title} — картинки", body)


def _safe_name(key: str) -> str:
    return key.replace(":", "__").replace("/", "_")


@app.post("/upload")
async def upload(key: str = Form(...), file: UploadFile = None, _: str = Depends(auth)):
    if file is None:
        raise HTTPException(400, "нет файла")
    data = await file.read()
    if not data:
        raise HTTPException(400, "пустой файл")
    try:
        photos = await ctx().api.upload_image(
            data, filename=file.filename or "image.png", content_type=file.content_type or "image/png"
        )
    except Exception as e:  # noqa: BLE001
        log.exception("upload to MAX failed")
        return page("Ошибка", f"<p>Не удалось загрузить картинку в MAX: {esc(e)}</p><p><a href='/'>← назад</a></p>")
    await ctx().db.set_image(key, photos)
    (UPLOAD_DIR / _safe_name(key)).write_bytes(data)  # локальная копия для превью
    sid = key.split(":")[1] if ":" in key else ""
    return RedirectResponse(f"/story/{sid}" if sid else "/", status_code=303)


@app.post("/img/delete")
async def img_delete(key: str = Form(...), _: str = Depends(auth)):
    await ctx().db.delete_image(key)
    p = UPLOAD_DIR / _safe_name(key)
    if p.exists():
        p.unlink()
    sid = key.split(":")[1] if ":" in key else ""
    return RedirectResponse(f"/story/{sid}" if sid else "/", status_code=303)


@app.get("/preview/{key}")
async def preview(key: str, _: str = Depends(auth)):
    p = UPLOAD_DIR / _safe_name(key)
    if not p.exists():
        raise HTTPException(404)
    return FileResponse(p)


@app.post("/channels/add")
async def channel_add(chat_id: int = Form(...), title: str = Form(""), link: str = Form(""), _: str = Depends(auth)):
    await ctx().db.upsert_channel(chat_id, title.strip(), link.strip())
    return RedirectResponse("/", status_code=303)


@app.post("/channels/{chat_id}/delete")
async def channel_delete(chat_id: int, _: str = Depends(auth)):
    await ctx().db.remove_channel(chat_id)
    return RedirectResponse("/", status_code=303)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host=cfg.admin_host, port=cfg.admin_port)

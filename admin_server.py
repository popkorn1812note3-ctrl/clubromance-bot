#!/usr/bin/env python
"""Веб-админка ClubRomance: картинки сцен (фоны по локациям + override), каналы ОП,
статистика подписки. Современный дизайн (server-rendered, без сборки).

  uvicorn admin_server:app --host 0.0.0.0 --port 8080
Доступ: HTTP Basic Auth (ADMIN_USER / ADMIN_PASSWORD из .env). Делит БД с ботом.
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
        raise SystemExit("❌ ADMIN_PASSWORD не задан — задайте в .env.")
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


def esc(s) -> str:
    return html.escape(str(s if s is not None else ""))


# ── Дизайн-система ───────────────────────────────────────────
CSS = """
:root{
  --bg:#0b0a10;--bg2:#100e1a;--surface:#16131f;--surface2:#1d1930;--elev:#241e39;
  --border:#2a2440;--border2:#3a3160;--text:#efecf7;--muted:#9b94b6;--faint:#6f688c;
  --accent:#ff5d9e;--accent2:#a15bff;--ok:#46d98a;--danger:#ff5d6c;--warn:#ffce5a;
  --grad:linear-gradient(135deg,#ff5d9e,#a15bff);
  --r:16px;--rs:11px;--sh:0 1px 2px rgba(0,0,0,.4),0 10px 30px -16px rgba(0,0,0,.65);
  --shl:0 18px 50px -22px rgba(0,0,0,.7);
}
*{box-sizing:border-box}html{scroll-behavior:smooth}
body{margin:0;color:var(--text);font:15px/1.6 'Inter',system-ui,Segoe UI,Roboto,sans-serif;
  -webkit-font-smoothing:antialiased;
  background:radial-gradient(1100px 560px at 82% -12%,#221643 0%,transparent 58%),
             radial-gradient(900px 480px at 0% 0%,#1a0f2e 0%,transparent 55%),var(--bg)}
a{color:inherit;text-decoration:none}
.wrap{max-width:1020px;margin:0 auto;padding:26px 20px 70px}
.topbar{position:sticky;top:0;z-index:30;background:rgba(11,10,16,.7);backdrop-filter:blur(14px);
  border-bottom:1px solid var(--border)}
.bar{max-width:1020px;margin:0 auto;padding:12px 20px;display:flex;align-items:center;justify-content:space-between}
.brand{display:flex;align-items:center;gap:11px;font-weight:700;font-size:17px}
.logo{display:grid;place-items:center;width:32px;height:32px;border-radius:10px;background:var(--grad);
  color:#fff;font-size:16px;box-shadow:0 8px 20px -8px var(--accent)}
.brand .nm{background:var(--grad);-webkit-background-clip:text;background-clip:text;color:transparent}
.badge-soft{font-size:11px;font-weight:600;color:var(--accent);background:rgba(255,93,158,.12);
  padding:2px 9px;border-radius:999px;border:1px solid rgba(255,93,158,.22)}
.nav{display:flex;gap:4px}
.navlink{padding:7px 14px;border-radius:10px;color:var(--muted);font-weight:500;font-size:14px;transition:.15s}
.navlink:hover{color:var(--text);background:var(--surface)}
.navlink.on{color:var(--text);background:var(--surface2);box-shadow:inset 0 0 0 1px var(--border)}
.hero{margin:24px 0 6px}
.hero h1{font-size:28px;font-weight:760;margin:0 0 4px;letter-spacing:-.5px}
.sub{color:var(--muted);margin:0}
.back{display:inline-flex;align-items:center;gap:6px;color:var(--muted);font-size:14px;transition:.15s}
.back:hover{color:var(--text);transform:translateX(-2px)}
section{margin-top:32px}
.sec-head{display:flex;align-items:baseline;gap:12px;margin:0 0 14px}
h2{font-size:12.5px;font-weight:700;text-transform:uppercase;letter-spacing:.13em;color:var(--muted);margin:0}
.hint{color:var(--faint);font-size:13px}
.chap{font-size:14px;font-weight:650;color:#cdbcff;margin:22px 0 10px;display:flex;align-items:center;gap:9px}
.chap:before{content:'';width:16px;height:2px;border-radius:2px;background:var(--grad)}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(290px,1fr));gap:14px}
.card{background:var(--surface);border:1px solid var(--border);border-radius:var(--r);padding:16px;
  box-shadow:var(--sh);transition:transform .18s,border-color .18s,box-shadow .18s}
.card:hover{border-color:var(--border2)}
.scard{display:flex;flex-direction:column;gap:5px}
.scard:hover{transform:translateY(-4px);box-shadow:var(--shl);border-color:var(--border2)}
.scard .cover{font-size:30px}
.scard .t{font-weight:650;font-size:16px}
.scard .m{color:var(--muted);font-size:13px}
.scard .go{margin-top:8px;color:var(--accent);font-size:13px;font-weight:600}
.btn{display:inline-flex;align-items:center;gap:7px;border:0;border-radius:var(--rs);padding:9px 16px;
  font:inherit;font-weight:600;font-size:14px;cursor:pointer;
  transition:transform .12s,box-shadow .18s,background .18s,border-color .18s,filter .18s}
.btn:active{transform:translateY(1px) scale(.99)}
.btn:focus-visible{outline:0;box-shadow:0 0 0 3px rgba(161,91,255,.35)}
.btn.primary{background:var(--grad);color:#fff;box-shadow:0 8px 22px -10px var(--accent)}
.btn.primary:hover{filter:brightness(1.06);box-shadow:0 13px 28px -8px var(--accent)}
.btn.ghost{background:var(--surface2);color:var(--text);box-shadow:inset 0 0 0 1px var(--border)}
.btn.ghost:hover{background:var(--elev)}
.btn.danger{background:rgba(255,93,108,.1);color:var(--danger);box-shadow:inset 0 0 0 1px rgba(255,93,108,.28)}
.btn.danger:hover{background:rgba(255,93,108,.18)}
.btn.sm{padding:7px 12px;font-size:13px}
input[type=text],input[type=number]{background:var(--bg2);border:1px solid var(--border);color:var(--text);
  border-radius:var(--rs);padding:10px 13px;font:inherit;transition:.15s;min-width:0}
input::placeholder{color:var(--faint)}
input:focus{outline:0;border-color:var(--accent);box-shadow:0 0 0 3px rgba(255,93,158,.16)}
.field{display:flex;gap:10px;flex-wrap:wrap;align-items:center;margin-bottom:10px}
.field input{flex:1}
.uploader{display:flex;gap:14px;align-items:center;margin-top:13px;flex-wrap:wrap}
.thumb{width:96px;height:64px;border-radius:11px;object-fit:cover;border:1px solid var(--border2);
  background:var(--bg2) center/cover no-repeat;flex:none}
.thumb.empty{display:grid;place-items:center;color:var(--faint);font-size:11px;border-style:dashed}
.ucol{display:flex;flex-direction:column;gap:8px}
.upform{display:flex;gap:8px;align-items:center;flex-wrap:wrap}
.filebtn{display:inline-flex;align-items:center;gap:7px;background:var(--surface2);
  box-shadow:inset 0 0 0 1px var(--border);border-radius:var(--rs);padding:8px 13px;font-size:13px;
  font-weight:600;cursor:pointer;transition:.15s}
.filebtn:hover{background:var(--elev)}
.filebtn input{display:none}
.fname{color:var(--faint);font-size:12px;max-width:140px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.pill{display:inline-flex;align-items:center;gap:5px;font-size:12px;font-weight:600;padding:3px 10px;border-radius:999px}
.pill.ok{color:var(--ok);background:rgba(70,217,138,.13)}
.pill.no{color:var(--faint);background:var(--surface2)}
.pill.warn{color:var(--warn);background:rgba(255,206,90,.13)}
.tag{font-family:ui-monospace,SFMono-Regular,monospace;font-size:11px;color:#c9a6ff;
  background:rgba(161,91,255,.13);padding:1px 7px;border-radius:6px}
.muted{color:var(--muted);font-size:13px}
.scene{display:flex;justify-content:space-between;gap:14px;align-items:flex-start}
.scene .meta{min-width:0}
.scene .ttl{font-weight:600}
.scene .pv{color:var(--faint);font-size:12.5px;margin-top:3px;max-width:560px}
.stats-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:14px;margin-top:4px}
.stat{background:var(--surface);border:1px solid var(--border);border-radius:var(--r);padding:18px;box-shadow:var(--sh)}
.stat .num{font-size:36px;font-weight:800;letter-spacing:-1.5px;line-height:1;
  background:var(--grad);-webkit-background-clip:text;background-clip:text;color:transparent}
.stat.plain .num{background:none;color:var(--text)}
.stat .lab{color:var(--muted);font-size:13px;margin-top:9px}
.stat .s2{color:var(--faint);font-size:12px;margin-top:2px}
.chips{display:flex;gap:10px;flex-wrap:wrap;margin-top:14px}
.chip{display:flex;align-items:center;gap:8px;background:var(--surface);border:1px solid var(--border);
  border-radius:999px;padding:7px 14px;font-size:13px;transition:.15s}
.chip:hover{border-color:var(--border2)}
.chip b{font-weight:700}
.chrow{display:flex;align-items:center;justify-content:space-between;gap:12px}
.empty{text-align:center;color:var(--faint);padding:24px;border:1px dashed var(--border);border-radius:var(--r)}
.wrap>*{animation:fade .42s cubic-bezier(.2,.7,.3,1) both}
@keyframes fade{from{opacity:0;transform:translateY(7px)}to{opacity:1;transform:none}}
::-webkit-scrollbar{width:10px;height:10px}::-webkit-scrollbar-thumb{background:var(--border2);border-radius:6px}
::-webkit-scrollbar-track{background:transparent}
@media(max-width:560px){.hero h1{font-size:23px}.bar{padding:10px 15px}.nav .navlink{padding:7px 11px}}
"""

JS = """
document.querySelectorAll('.up-input').forEach(function(inp){
  inp.addEventListener('change',function(){
    var f=inp.files[0]; if(!f) return;
    var box=inp.closest('.uploader');
    var nm=box.querySelector('.fname'); if(nm) nm.textContent=f.name;
    var th=box.querySelector('.thumb');
    if(th && f.type.indexOf('image/')===0){
      var rd=new FileReader();
      rd.onload=function(e){
        if(th.tagName==='IMG'){th.src=e.target.result;}
        else{th.style.backgroundImage='url('+e.target.result+')';th.classList.remove('empty');th.textContent='';}
      };
      rd.readAsDataURL(f);
    }
    var b=box.querySelector('button'); if(b) b.classList.add('primary');
  });
});
"""

FONT = "<link rel=preconnect href='https://fonts.googleapis.com'><link rel=preconnect href='https://fonts.gstatic.com' crossorigin><link href='https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&display=swap' rel=stylesheet>"


def page(title: str, body: str, active: str = "") -> HTMLResponse:
    def nav(href, label, key):
        on = " on" if active == key else ""
        return f"<a class='navlink{on}' href='{href}'>{label}</a>"
    top = (
        "<div class=topbar><div class=bar>"
        "<a class=brand href='/'><span class=logo>♥</span> <span class=nm>Club Romance</span> "
        "<span class=badge-soft>admin</span></a>"
        f"<nav class=nav>{nav('/', 'Главная', 'home')}{nav('/stats', 'Статистика', 'stats')}</nav>"
        "</div></div>"
    )
    return HTMLResponse(
        f"<!doctype html><html lang=ru><head><meta charset=utf-8>"
        f"<meta name=viewport content='width=device-width,initial-scale=1'>"
        f"<title>{esc(title)}</title>{FONT}<style>{CSS}</style></head><body>"
        f"{top}<main class=wrap>{body}</main><script>{JS}</script></body></html>"
    )


def uploader(key: str, present: bool) -> str:
    if present:
        thumb = f"<img class=thumb src='/preview/{esc(key)}'>"
        pill = "<span class='pill ok'>● загружено</span>"
        delete = (
            f"<form method=post action='/img/delete'>"
            f"<input type=hidden name=key value='{esc(key)}'>"
            f"<button class='btn danger sm'>Удалить</button></form>"
        )
    else:
        thumb = "<div class='thumb empty'>нет фото</div>"
        pill = "<span class='pill no'>○ пусто</span>"
        delete = ""
    return (
        f"<div class=uploader>{thumb}<div class=ucol>{pill}"
        f"<form class=upform method=post action='/upload' enctype='multipart/form-data'>"
        f"<input type=hidden name=key value='{esc(key)}'>"
        f"<label class=filebtn>📁 Выбрать<input class=up-input type=file name=file accept='image/*' required></label>"
        f"<span class=fname>файл не выбран</span>"
        f"<button class='btn ghost sm'>Загрузить</button></form>{delete}</div></div>"
    )


# ── Роуты ────────────────────────────────────────────────────
@app.get("/", response_class=HTMLResponse)
async def dashboard(_: str = Depends(auth)):
    c = ctx()
    st = await c.db.gate_stats()
    conv = f"{round(100 * st['passed'] / st['total'])}%" if st["total"] else "—"
    chips = (
        f"<a class=chip href='/stats'>👥 <b>{st['total']}</b> юзеров</a>"
        f"<a class=chip href='/stats'>🔓 <b>{st['passed']}</b> прошли подписку</a>"
        f"<a class=chip href='/stats'>📈 <b>{conv}</b> конверсия</a>"
    )

    stories = c.registry.all()
    s_html = "".join(
        f"<a class='card scard' href='/story/{esc(s.id)}'>"
        f"<span class=cover>{esc(s.cover)}</span><span class=t>{esc(s.title)}</span>"
        f"<span class=m>{s.scene_count()} сцен · {s.chapter_count()} глав</span>"
        f"<span class=go>Картинки →</span></a>"
        for s in stories
    )

    channels = await c.db.list_required_channels()
    ch_rows = "".join(
        f"<div class=card><div class=chrow><div><b>{esc(ch['title'] or 'Канал')}</b> "
        f"<span class=tag>{ch['chat_id']}</span><div class=muted>{esc(ch['link'] or 'без ссылки')}</div></div>"
        f"<form method=post action='/channels/{ch['chat_id']}/delete'>"
        f"<button class='btn danger sm'>Удалить</button></form></div></div>"
        for ch in channels
    ) or "<div class=empty>Каналов нет. Добавь бота админом в канал — он зарегистрируется сам, или добавь вручную ниже.</div>"
    add_ch = (
        "<form method=post action='/channels/add' class=card style='margin-top:14px'>"
        "<div class=field><input type=number name=chat_id placeholder='chat_id (напр. -7488…)' required>"
        "<input type=text name=title placeholder='Название канала'></div>"
        "<div class=field><input type=text name=link placeholder='https://max.ru/...'>"
        "<button class='btn primary'>Добавить канал</button></div>"
        "<div class=muted>chat_id берётся из логов при добавлении бота в канал. Бот должен быть админом.</div></form>"
    )

    body = (
        "<div class=hero><h1>Панель управления</h1>"
        "<p class=sub>Картинки историй и обязательная подписка</p>"
        f"<div class=chips>{chips}</div></div>"
        "<section><div class=sec-head><h2>Истории</h2><span class=hint>картинки сцен</span></div>"
        f"<div class=grid>{s_html}</div></section>"
        "<section><div class=sec-head><h2>Каналы обязательной подписки</h2></div>"
        f"{ch_rows}{add_ch}</section>"
    )
    return page("ClubRomance Admin", body, "home")


@app.get("/stats", response_class=HTMLResponse)
async def stats_page(_: str = Depends(auth)):
    c = ctx()
    st = await c.db.gate_stats()
    channels = await c.db.list_required_channels()
    conv = f"{round(100 * st['passed'] / st['total'])}%" if st["total"] else "—"

    cards = (
        f"<div class='stat plain'><div class=num>{st['total']}</div><div class=lab>всего пользователей</div></div>"
        f"<div class=stat><div class=num>{st['passed']}</div><div class=lab>прошли подписку</div>"
        f"<div class=s2>конверсия {conv}</div></div>"
    )
    if channels:
        cards += f"<div class='stat plain'><div class=num>{st['stuck']}</div><div class=lab>ещё не подписались</div></div>"
    newcard = (
        "<div class=card style='margin-top:14px'><div class=muted>"
        f"🆕 новых сегодня: <b style='color:var(--text)'>{st['today']}</b> (прошли {st['today_passed']}) &nbsp;·&nbsp; "
        f"за 7 дней: <b style='color:var(--text)'>{st['week']}</b> (прошли {st['week_passed']})</div></div>"
    )
    warn = "" if channels else "<div class=empty style='margin-top:14px'>⚠️ Гейт выключен — каналов нет. «Прошли подписку» станет осмысленным после добавления канала.</div>"

    ch_html = ""
    for ch in channels:
        size, admin = "?", False
        try:
            size = (await c.api.get_chat(ch["chat_id"])).get("participants_count", "?")
        except Exception:  # noqa: BLE001
            pass
        try:
            me = await c.api.get_my_membership(ch["chat_id"])
            admin = bool(me.get("is_admin") or me.get("is_owner"))
        except Exception:  # noqa: BLE001
            admin = False
        badge = ("<span class='pill ok'>✓ бот админ — проверка работает</span>" if admin
                 else "<span class='pill warn'>⚠ бот не админ — подписку не проверить</span>")
        ch_html += (f"<div class=card><div class=chrow><div><b>📢 {esc(ch['title'] or 'Канал')}</b> "
                    f"<span class=tag>{ch['chat_id']}</span><div class=muted>{esc(size)} подписчиков</div></div>"
                    f"{badge}</div></div>")
    if not channels:
        ch_html = "<div class=empty>Каналов нет.</div>"

    body = (
        "<a class=back href='/'>← Назад</a>"
        "<div class=hero><h1>Статистика подписки</h1><p class=sub>Воронка обязательной подписки (ОП)</p></div>"
        f"<div class=stats-grid>{cards}</div>{newcard}{warn}"
        "<section><div class=sec-head><h2>Каналы</h2></div>" + ch_html + "</section>"
    )
    return page("Статистика ОП", body, "stats")


@app.get("/story/{sid}", response_class=HTMLResponse)
async def story_page(sid: str, _: str = Depends(auth)):
    c = ctx()
    story = c.registry.get(sid)
    if story is None:
        raise HTTPException(404)
    scenes = story.data.get("scenes", {})
    keys = await c.db.image_keys()

    locs: dict[str, int] = {}
    for sc in scenes.values():
        if sc.get("bg"):
            locs[sc["bg"]] = locs.get(sc["bg"], 0) + 1
    for k in story.data.get("backgrounds", {}):
        locs.setdefault(k, 0)
    loc_html = "".join(
        f"<div class=card><b>📍 {esc(bg)}</b> <span class=muted>· {locs[bg]} сцен</span>"
        f"{uploader(f'bg:{sid}:{bg}', f'bg:{sid}:{bg}' in keys)}</div>"
        for bg in sorted(locs)
    )

    sc_html = ""
    for scid, sc in scenes.items():
        if sc.get("chapter"):
            sc_html += f"<div class=chap>{esc(sc['chapter'])}</div>"
        first = next((b.get("text") for b in sc.get("blocks", []) if b.get("text")), "")
        key = f"scene:{sid}:{scid}"
        sc_html += (
            f"<div class=card><div class=scene><div class=meta>"
            f"<div class=ttl>{esc(sc.get('title') or scid)} <span class=tag>{esc(scid)}</span></div>"
            f"<div class=pv>{esc(first[:96])}</div></div>"
            f"<span class=muted>фон: {esc(sc.get('bg') or '—')}</span></div>"
            f"{uploader(key, key in keys)}</div>"
        )

    body = (
        "<a class=back href='/'>← К историям</a>"
        f"<div class=hero><h1>{esc(story.cover)} {esc(story.title)}</h1>"
        "<p class=sub>Фоны по локациям и картинки к ключевым сценам</p></div>"
        "<section><div class=sec-head><h2>Фоны по локациям</h2>"
        "<span class=hint>одна картинка на место</span></div>"
        f"<div class=grid>{loc_html}</div></section>"
        "<section><div class=sec-head><h2>Картинки к сценам</h2>"
        "<span class=hint>override поверх фона — для важных моментов</span></div>"
        f"{sc_html}</section>"
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
        return page("Ошибка", f"<div class=empty>Не удалось загрузить в MAX: {esc(e)}</div>"
                              "<p><a class=back href='/'>← назад</a></p>")
    await ctx().db.set_image(key, photos)
    (UPLOAD_DIR / _safe_name(key)).write_bytes(data)
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

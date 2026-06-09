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
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote

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


PAGE = 50  # пользователей на страницу списка


def fmt_ts(ts, empty: str = "—") -> str:
    ts = int(ts or 0)
    if not ts:
        return empty
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d %H:%M")


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
.list{display:flex;flex-direction:column;gap:10px}
.urow{display:flex;align-items:center;justify-content:space-between;gap:14px}
.urow:hover{transform:translateY(-3px);box-shadow:var(--shl);border-color:var(--border2)}
.uname{font-weight:650}
.bal{font-weight:700;color:var(--warn);white-space:nowrap}
select{background:var(--bg2);border:1px solid var(--border);color:var(--text);border-radius:var(--rs);
  padding:10px 13px;font:inherit;cursor:pointer}
select:focus{outline:0;border-color:var(--accent);box-shadow:0 0 0 3px rgba(255,93,158,.16)}
.formgrid{display:grid;grid-template-columns:repeat(auto-fit,minmax(190px,1fr));gap:12px;margin-bottom:6px}
.formgrid label{display:flex;flex-direction:column;gap:6px;font-size:13px;color:var(--muted)}
.formgrid input{width:100%}
.checks{display:flex;gap:20px;flex-wrap:wrap;margin:12px 0}
.check{display:flex;align-items:center;gap:8px;color:var(--text);font-size:14px;cursor:pointer}
.check input{width:17px;height:17px;accent-color:var(--accent);cursor:pointer}
.kv{display:grid;grid-template-columns:auto 1fr;gap:8px 18px;font-size:14px;align-items:center}
.kv .k{color:var(--faint)}
.danger-zone{border-color:rgba(255,93,108,.32)}
.led{display:flex;justify-content:space-between;gap:12px;font-size:13px;padding:8px 0;border-bottom:1px solid var(--border)}
.led:last-child{border:0}.led:first-child{padding-top:0}
.pos{color:var(--ok);white-space:nowrap}.neg{color:var(--danger);white-space:nowrap}
.pager{display:flex;gap:10px;align-items:center;justify-content:center;margin-top:20px}
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
document.querySelectorAll('form[data-confirm]').forEach(function(f){
  f.addEventListener('submit',function(e){ if(!confirm(f.getAttribute('data-confirm'))) e.preventDefault(); });
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
        f"<nav class=nav>{nav('/', 'Главная', 'home')}{nav('/users', 'Пользователи', 'users')}{nav('/subs', 'Задания', 'subs')}{nav('/stats', 'Статистика', 'stats')}</nav>"
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
            f"<form method=post action='/img/delete' "
            f"data-confirm='Удалить эту картинку? Её придётся загружать заново.'>"
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
    keys = await c.db.image_keys()
    st = await c.db.gate_stats()
    conv = f"{round(100 * st['passed'] / st['total'])}%" if st["total"] else "—"
    chips = (
        f"<a class=chip href='/users'>👥 <b>{st['total']}</b> юзеров</a>"
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
        "<section><div class=sec-head><h2>Картинка главного меню</h2>"
        "<span class=hint>показывается на экране меню и приветствия</span></div>"
        f"<div class=card>{uploader('ui:main', 'ui:main' in keys)}</div></section>"
        "<section><div class=sec-head><h2>Истории</h2><span class=hint>обложка + картинки сцен</span></div>"
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

    # Карта переходов: откуда и по какому выбору попадают в сцену. Нужно, чтобы
    # вариации («с кем сцена» — Леонард / Дэмиан / София) были различимы и было
    # видно их место в сюжете (за одно прохождение игрок видит лишь одну ветку).
    egroups = story.data.get("ending_groups", {})
    incoming: dict[str, list[str]] = {}
    ending_of: dict[str, str] = {}

    def _edge(target, label):
        if target:
            incoming.setdefault(target, []).append(label)

    for _sc in scenes.values():
        for ch in _sc.get("choices", []):
            txt = (ch.get("text") or "").strip()
            _edge(ch.get("goto"), f"«{txt[:32]}»" if txt else "выбор")
        nxt = _sc.get("next")
        if isinstance(nxt, str):
            _edge(nxt, "далее →")
        elif isinstance(nxt, list):
            for o in nxt:
                _edge(o.get("goto"), "далее (по условию)")
        rg = _sc.get("route_group")
        if rg:
            for o in egroups.get(rg, []):
                _edge(o.get("goto"), f"финал «{o.get('title') or o.get('code')}»")
    for _opts in egroups.values():
        for o in _opts:
            if o.get("goto"):
                ending_of[o["goto"]] = o.get("title") or o.get("code") or "финал"

    sc_html = ""
    for scid, sc in scenes.items():
        if sc.get("chapter"):
            sc_html += f"<div class=chap>{esc(sc['chapter'])}</div>"
        first = next((b.get("text") for b in sc.get("blocks", []) if b.get("text")), "")
        key = f"scene:{sid}:{scid}"
        # Кто говорит в сцене (кроме героини) — сразу видно, «с кем» вариация.
        speakers: list[str] = []
        for b in sc.get("blocks", []):
            sp = b.get("speaker")
            if sp and sp != "Героиня" and sp not in speakers:
                speakers.append(sp)
        sp_html = " ".join(f"<span class=tag>🗣 {esc(s)}</span>" for s in speakers)
        end_html = f"<span class='pill warn'>🏁 финал «{esc(ending_of[scid])}»</span>" if scid in ending_of else ""
        meta_line = (f"<div style='margin:5px 0 3px;display:flex;gap:6px;flex-wrap:wrap;align-items:center'>"
                     f"{sp_html}{end_html}</div>") if (sp_html or end_html) else ""
        src = incoming.get(scid, [])
        from_html = ""
        if src:
            shown = " · ".join(esc(x) for x in src[:3])
            more = f" +{len(src) - 3}" if len(src) > 3 else ""
            from_html = f"<div class=muted>◀ ведёт сюда: {shown}{more}</div>"
        sc_html += (
            f"<div class=card><div class=scene><div class=meta>"
            f"<div class=ttl>{esc(sc.get('title') or scid)} <span class=tag>{esc(scid)}</span></div>"
            f"{meta_line}{from_html}"
            f"<div class=pv>{esc(first[:96])}</div></div>"
            f"<span class=muted>фон: {esc(sc.get('bg') or '—')}</span></div>"
            f"{uploader(key, key in keys)}</div>"
        )

    body = (
        "<a class=back href='/'>← К историям</a>"
        f"<div class=hero><h1>{esc(story.cover)} {esc(story.title)}</h1>"
        "<p class=sub>Обложка, фоны по локациям и картинки к ключевым сценам</p></div>"
        "<section><div class=sec-head><h2>Обложка / превью истории</h2>"
        "<span class=hint>показывается на карточке истории в боте</span></div>"
        f"<div class=card>{uploader(f'cover:{sid}', f'cover:{sid}' in keys)}</div></section>"
        "<section><div class=sec-head><h2>Фоны по локациям</h2>"
        "<span class=hint>одна картинка на место</span></div>"
        f"<div class=grid>{loc_html}</div></section>"
        "<section><div class=sec-head><h2>Картинки к сценам</h2>"
        "<span class=hint>override поверх фона — для важных моментов</span></div>"
        "<div class=card style='margin-bottom:14px'><div class=muted>"
        "🔀 <b style='color:var(--text)'>Про вариации.</b> Сюжет местами ветвится — героиня "
        "общается с Леонардом, Дэмианом или Софией в зависимости от выборов игрока. Под "
        "заголовком сцены видно, <b style='color:var(--text)'>кто в ней</b> (🗣) и "
        "<b style='color:var(--text)'>откуда в неё попадают</b> (◀). Это нормально, что у "
        "одного момента несколько вариантов: игрок за прохождение видит только свою ветку, "
        "и картинку, прикреплённую к сцене, увидит только он."
        "</div></div>"
        f"{sc_html}</section>"
    )
    return page(f"{story.title} — картинки", body)


def _safe_name(key: str) -> str:
    return key.replace(":", "__").replace("/", "_")


def _redirect_for(key: str) -> str:
    """Куда вернуться после загрузки/удаления по ключу картинки."""
    parts = key.split(":")
    if parts[0] in ("bg", "scene", "cover") and len(parts) > 1:
        return f"/story/{parts[1]}"
    return "/"  # ui:* и прочее — на главную


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
    return RedirectResponse(_redirect_for(key), status_code=303)


@app.post("/img/delete")
async def img_delete(key: str = Form(...), _: str = Depends(auth)):
    await ctx().db.delete_image(key)
    p = UPLOAD_DIR / _safe_name(key)
    if p.exists():
        p.unlink()
    return RedirectResponse(_redirect_for(key), status_code=303)


@app.get("/preview/{key}")
async def preview(key: str, _: str = Depends(auth)):
    p = UPLOAD_DIR / _safe_name(key)
    if not p.exists():
        raise HTTPException(404)
    # no-store: после замены картинки админка показывает свежее фото, а не кеш браузера.
    return FileResponse(p, headers={"Cache-Control": "no-store, max-age=0", "Pragma": "no-cache"})


@app.post("/channels/add")
async def channel_add(chat_id: int = Form(...), title: str = Form(""), link: str = Form(""), _: str = Depends(auth)):
    await ctx().db.upsert_channel(chat_id, title.strip(), link.strip())
    return RedirectResponse("/", status_code=303)


@app.post("/channels/{chat_id}/delete")
async def channel_delete(chat_id: int, _: str = Depends(auth)):
    await ctx().db.remove_channel(chat_id)
    return RedirectResponse("/", status_code=303)


# ── Подписки за награду (каналы-задания) ─────────────────────
@app.get("/subs", response_class=HTMLResponse)
async def subs_page(_: str = Depends(auth)):
    c = ctx()
    channels = await c.db.list_reward_channels()
    summary = await c.db.subscription_summary()

    rows = ""
    for ch in channels:
        cid = ch["chat_id"]
        admin = False
        try:
            me = await c.api.get_my_membership(cid)
            admin = bool(me.get("is_admin") or me.get("is_owner"))
        except Exception:  # noqa: BLE001
            admin = False
        badge = ("<span class='pill ok'>✓ бот админ</span>" if admin
                 else "<span class='pill warn'>⚠ бот не админ — награду не выдать</span>")
        s = summary.get(cid, {"active": 0, "revoked": 0})
        rows += (
            f"<div class=card><div class=chrow><div>"
            f"<b>📣 {esc(ch['title'] or 'Канал')}</b> <span class=tag>{cid}</span>"
            f"<div class=muted>{esc(ch['link'] or 'без ссылки')}</div>"
            f"<div class=muted>выдано: {s['active']} · отозвано: {s['revoked']}</div></div>"
            f"<div>{badge}</div></div>"
            f"<form method=post action='/subs/add' class=field style='margin-top:12px'>"
            f"<input type=hidden name=chat_id value='{cid}'>"
            f"<input type=hidden name=title value='{esc(ch['title'] or '')}'>"
            f"<input type=hidden name=link value='{esc(ch['link'] or '')}'>"
            f"<label class=check>Награда {esc('💎')}<input type=number name=reward value='{ch['reward']}' min='0' style='max-width:100px'></label>"
            f"<label class=check>Держать дней<input type=number name=hold_days value='{ch['hold_days']}' min='0' style='max-width:100px'></label>"
            f"<button class='btn ghost sm'>Сохранить</button></form>"
            f"<form method=post action='/subs/{cid}/delete' data-confirm='Удалить задание? Уже выданные награды останутся у игроков.' style='margin-top:8px'>"
            f"<button class='btn danger sm'>Удалить задание</button></form></div>"
        )
    if not channels:
        rows = "<div class=empty>Заданий нет. Добавь канал ниже — игроки будут получать кристаллы за подписку.</div>"

    add = (
        "<form method=post action='/subs/add' class=card style='margin-top:14px'>"
        "<div class=field><input type=number name=chat_id placeholder='chat_id канала (напр. -7488…)' required>"
        "<input type=text name=title placeholder='Название канала'></div>"
        "<div class=field><input type=text name=link placeholder='https://max.ru/...'>"
        "<input type=number name=reward value='15' min='0' placeholder='Награда 💎' style='max-width:150px'>"
        "<input type=number name=hold_days value='7' min='0' placeholder='Держать дней' style='max-width:150px'>"
        "<button class='btn primary'>Добавить задание</button></div>"
        "<div class=muted>Чтобы проверка подписки работала, бот должен быть <b>админом</b> канала. "
        "«Держать дней» — через сколько дней проверим, не отписался ли игрок (0 — награду не отзывать).</div></form>"
    )

    body = (
        "<a class=back href='/'>← На главную</a>"
        "<div class=hero><h1>Подписки за награду</h1>"
        "<p class=sub>Игрок подписывается на канал → получает кристаллы. Отпишется в срок удержания → награду заберём</p></div>"
        f"<section><div class=sec-head><h2>Каналы-задания</h2>"
        "<span class=hint>награда и срок удержания настраиваются на каждый канал</span></div>"
        f"{rows}{add}</section>"
    )
    return page("Задания — подписки за награду", body, "subs")


@app.post("/subs/add")
async def subs_add(
    chat_id: int = Form(...),
    title: str = Form(""),
    link: str = Form(""),
    reward: int = Form(0),
    hold_days: int = Form(0),
    _: str = Depends(auth),
):
    await ctx().db.upsert_reward_channel(
        chat_id, title.strip(), link.strip(), max(0, reward), max(0, hold_days)
    )
    return RedirectResponse("/subs", status_code=303)


@app.post("/subs/{chat_id}/delete")
async def subs_delete(chat_id: int, _: str = Depends(auth)):
    await ctx().db.remove_channel(chat_id)
    return RedirectResponse("/subs", status_code=303)


# ── Пользователи ─────────────────────────────────────────────
@app.get("/users", response_class=HTMLResponse)
async def users_page(q: str = "", offset: int = 0, _: str = Depends(auth)):
    c = ctx()
    q = (q or "").strip()
    offset = max(0, offset)
    total = await c.db.count_users(q)
    users = await c.db.list_users(q, limit=PAGE, offset=offset)

    rows = ""
    for u in users:
        gate = ("<span class='pill ok'>● прошёл ОП</span>" if u["gate_passed"]
                else "<span class='pill no'>○ не прошёл</span>")
        un = f"@{esc(u['username'])}" if u["username"] else "—"
        rows += (
            f"<a class='card urow' href='/users/{u['user_id']}'>"
            f"<div><div class=uname>{esc(u['name'] or 'без имени')} "
            f"<span class=tag>{u['user_id']}</span></div>"
            f"<div class=muted>{un} · рег. {fmt_ts(u['created_at'])}</div></div>"
            f"<div style='display:flex;align-items:center;gap:12px'>"
            f"<span class=bal>💎 {u['crystals']}</span>{gate}</div></a>"
        )
    if not users:
        rows = "<div class=empty>Никого не найдено.</div>"

    qq = f"&q={quote(q)}" if q else ""
    parts = []
    if offset > 0:
        parts.append(f"<a class='btn ghost sm' href='/users?offset={max(0, offset - PAGE)}{qq}'>← Назад</a>")
    if offset + PAGE < total:
        parts.append(f"<a class='btn ghost sm' href='/users?offset={offset + PAGE}{qq}'>Вперёд →</a>")
    rng = f"{offset + 1}–{min(offset + PAGE, total)} из {total}" if total else "0"
    pager = f"<div class=pager>{''.join(parts)}<span class=muted>{rng}</span></div>"

    search = (
        "<form class=field method=get action='/users' style='margin:0 0 16px'>"
        f"<input type=text name=q value='{esc(q)}' placeholder='ID, имя или @username'>"
        "<button class='btn primary'>Поиск</button>"
        + ("<a class='btn ghost' href='/users'>Сброс</a>" if q else "")
        + "</form>"
    )
    body = (
        "<a class=back href='/'>← На главную</a>"
        "<div class=hero><h1>Пользователи</h1>"
        f"<p class=sub>Всего {total} · баланс кристаллов и управление</p></div>"
        f"{search}<div class=list>{rows}</div>{pager}"
    )
    return page("Пользователи", body, "users")


@app.get("/users/{uid}", response_class=HTMLResponse)
async def user_detail(uid: int, _: str = Depends(auth)):
    c = ctx()
    u = await c.db.get_user(uid)
    if u is None:
        raise HTTPException(404)
    progs = await c.db.list_progress(uid)
    comps = await c.db.list_completions(uid)
    ach_codes = await c.db.list_achievements(uid)
    refs = await c.db.count_referrals(uid)
    led = await c.db.recent_ledger(uid, 15)
    a = f"/users/{uid}"

    def story_label(sid: str) -> str:
        s = c.registry.get(sid)
        return f"{esc(s.cover)} {esc(s.title)}" if s else f"<span class=tag>{esc(sid)}</span>"

    def ach_name(code: str) -> str:
        for s in c.registry.all():
            spec = s.achievements.get(code)
            if spec:
                return esc(spec.get("name") or spec.get("title") or code)
        return esc(code)

    gate = ("<span class='pill ok'>прошёл ОП</span>" if u["gate_passed"]
            else "<span class='pill no'>не прошёл ОП</span>")
    sub = "<span class='pill ok'>подписка засчитана</span>" if u["subscribed"] else ""

    kv = (
        "<div class=kv>"
        f"<span class=k>ID</span><span><span class=tag>{u['user_id']}</span></span>"
        f"<span class=k>Имя</span><span>{esc(u['name'] or '—')}</span>"
        f"<span class=k>Username</span><span>{('@' + esc(u['username'])) if u['username'] else '—'}</span>"
        f"<span class=k>Язык</span><span>{esc(u['language'])}</span>"
        f"<span class=k>Уведомления</span><span>{'вкл' if u['notifications'] else 'выкл'}</span>"
        f"<span class=k>Ежедневная</span><span>{fmt_ts(u['last_daily'])}</span>"
        f"<span class=k>Пригласил</span><span>{u['referred_by'] or '—'}</span>"
        f"<span class=k>Рефералов</span><span>{refs}</span>"
        f"<span class=k>Регистрация</span><span>{fmt_ts(u['created_at'])}</span>"
        "</div>"
    )

    crystals = (
        "<section><div class=sec-head><h2>Кристаллы</h2>"
        f"<span class=hint>текущий баланс — 💎 {u['crystals']}</span></div>"
        f"<div class=card><form method=post action='{a}/crystals' class=field>"
        "<select name=mode><option value=add>Изменить (±)</option>"
        "<option value=set>Установить точно</option></select>"
        "<input type=number name=amount value='0' required style='max-width:160px'>"
        "<button class='btn primary'>Применить</button></form>"
        "<div class=muted>Операция пишется в журнал (виден ниже). В минус уйти нельзя.</div></div></section>"
    )

    langs = ""
    for code, label in (("ru", "Русский"), ("en", "English"), ("uk", "Українська")):
        langs += f"<option value={code}{' selected' if u['language'] == code else ''}>{label}</option>"

    def chk(field: str, label: str) -> str:
        return (f"<label class=check><input type=checkbox name={field} value=1"
                f"{' checked' if u[field] else ''}> {label}</label>")

    profile = (
        "<section><div class=sec-head><h2>Профиль</h2>"
        "<span class=hint>имя, юзернейм, язык и флаги</span></div>"
        f"<div class=card><form method=post action='{a}/update'>"
        "<div class=formgrid>"
        f"<label>Имя<input type=text name=name value='{esc(u['name'])}'></label>"
        f"<label>Username<input type=text name=username value='{esc(u['username'])}'></label>"
        f"<label>Язык<select name=language>{langs}</select></label>"
        "</div>"
        f"<div class=checks>{chk('gate_passed', 'Прошёл ОП')}"
        f"{chk('subscribed', 'Награда за подписку')}{chk('notifications', 'Уведомления')}</div>"
        "<button class='btn primary'>Сохранить профиль</button></form></div></section>"
    )

    if progs:
        pr = ""
        for p in progs:
            s = c.registry.get(p["story_id"])
            sc = s.scene(p["current_scene"]) if s else None
            scene_t = esc(sc["title"]) if sc and sc.get("title") else esc(p["current_scene"])
            status = "завершено" if p["status"] == "completed" else "в процессе"
            pr += (f"<div class=led><span>{story_label(p['story_id'])} — {scene_t}</span>"
                   f"<span class=muted>{status} · {fmt_ts(p['updated_at'])}</span></div>")
        progress_html = f"<div class=card>{pr}</div>"
    else:
        progress_html = "<div class=empty>Нет историй в процессе.</div>"

    if comps:
        cm = ""
        for cp in comps:
            secret = " · 🔒секретка" if cp["secret"] else ""
            ttl = esc(cp["ending_title"] or cp["ending_code"] or "финал")
            cm += (f"<div class=led><span>{story_label(cp['story_id'])} — {ttl}{secret}</span>"
                   f"<span class=muted>×{cp['plays']} · {fmt_ts(cp['completed_at'])}</span></div>")
        comp_html = f"<div class=card>{cm}</div>"
    else:
        comp_html = "<div class=empty>Ещё не завершал историй.</div>"

    if ach_codes:
        ach_html = ("<div class=chips>"
                    + "".join(f"<span class=chip>🏆 {ach_name(co)}</span>" for co in sorted(ach_codes))
                    + "</div>")
    else:
        ach_html = "<div class=empty>Достижений нет.</div>"

    if led:
        lr = ""
        for e in led:
            amt = int(e["amount"])
            cls, sign = ("pos", "+") if amt >= 0 else ("neg", "")
            meta = f" <span class=muted>{esc(e['meta'])}</span>" if e["meta"] else ""
            lr += (f"<div class=led><span>{esc(e['type'])}{meta}</span>"
                   f"<span class='{cls}'>{sign}{amt} 💎 <span class=muted>· {fmt_ts(e['created_at'])}</span></span></div>")
        led_html = f"<div class=card>{lr}</div>"
    else:
        led_html = "<div class=empty>Операций нет.</div>"

    unlock_opts = "".join(f"<option value='{esc(s.id)}'>{esc(s.title)}</option>" for s in c.registry.all())
    danger = (
        "<section><div class=sec-head><h2>Опасная зона</h2></div>"
        "<div class='card danger-zone'>"
        f"<form method=post action='{a}/unlock' class=field style='margin-bottom:14px'>"
        f"<select name=story_id>{unlock_opts}</select>"
        "<button class='btn ghost'>Открыть историю (разблокировать)</button></form>"
        "<div class=field>"
        f"<form method=post action='{a}/reset' data-confirm='Сбросить весь игровой прогресс "
        "(истории, концовки, достижения)? Баланс и профиль останутся.'>"
        "<button class='btn danger'>Сбросить прогресс</button></form>"
        f"<form method=post action='{a}/delete' data-confirm='Удалить пользователя НАВСЕГДА "
        "со всеми данными? Это необратимо.'>"
        "<button class='btn danger'>Удалить пользователя</button></form>"
        "</div></div></section>"
    )

    body = (
        "<a class=back href='/users'>← К пользователям</a>"
        f"<div class=hero><h1>{esc(u['name'] or 'Пользователь')} "
        f"<span class=tag style='font-size:14px'>{u['user_id']}</span></h1>"
        f"<p class=sub>💎 {u['crystals']} &nbsp; {gate} {sub}</p></div>"
        f"<div class=card>{kv}</div>"
        f"{crystals}{profile}"
        "<section><div class=sec-head><h2>Истории в процессе</h2></div>" + progress_html + "</section>"
        "<section><div class=sec-head><h2>Завершённые</h2></div>" + comp_html + "</section>"
        "<section><div class=sec-head><h2>Достижения</h2></div>" + ach_html + "</section>"
        "<section><div class=sec-head><h2>Журнал операций</h2></div>" + led_html + "</section>"
        + danger
    )
    return page(f"Пользователь {uid}", body, "users")


@app.post("/users/{uid}/crystals")
async def user_crystals(uid: int, mode: str = Form("add"), amount: int = Form(0), _: str = Depends(auth)):
    db = ctx().db
    if await db.get_user(uid) is None:
        raise HTTPException(404)
    if mode == "set":
        await db.set_crystals(uid, amount)
    else:
        await db.adjust_crystals(uid, amount)
    return RedirectResponse(f"/users/{uid}", status_code=303)


@app.post("/users/{uid}/update")
async def user_update(
    uid: int,
    name: str = Form(""),
    username: str = Form(""),
    language: str = Form("ru"),
    gate_passed: str = Form(""),
    subscribed: str = Form(""),
    notifications: str = Form(""),
    _: str = Depends(auth),
):
    db = ctx().db
    if await db.get_user(uid) is None:
        raise HTTPException(404)
    await db.update_user(
        uid,
        name=name.strip(),
        username=username.strip().lstrip("@"),
        language=(language or "ru").strip() or "ru",
        gate_passed=1 if gate_passed else 0,
        subscribed=1 if subscribed else 0,
        notifications=1 if notifications else 0,
    )
    return RedirectResponse(f"/users/{uid}", status_code=303)


@app.post("/users/{uid}/unlock")
async def user_unlock(uid: int, story_id: str = Form(...), _: str = Depends(auth)):
    if await ctx().db.get_user(uid) is None:
        raise HTTPException(404)
    await ctx().db.unlock_story(uid, story_id)
    return RedirectResponse(f"/users/{uid}", status_code=303)


@app.post("/users/{uid}/reset")
async def user_reset(uid: int, _: str = Depends(auth)):
    await ctx().db.reset_user_game(uid)
    return RedirectResponse(f"/users/{uid}", status_code=303)


@app.post("/users/{uid}/delete")
async def user_delete(uid: int, _: str = Depends(auth)):
    await ctx().db.delete_user(uid)
    return RedirectResponse("/users", status_code=303)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host=cfg.admin_host, port=cfg.admin_port)

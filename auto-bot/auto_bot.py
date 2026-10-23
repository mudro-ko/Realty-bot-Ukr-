"""
Telegram-бот пошуку авто UA
===========================
Джерела: AUTO.RIA (пошук сайту + картка авто) і публічні Telegram-канали з продажем авто.
Кнопкове меню пошуку, розклад і дайджести, архів з історією цін, Excel-звіт, порівняння з ринком,
доступ для інших за номером телефону або посиланням.

Запуск на Windows: start.bat.  Вручну:  pip install -r requirements.txt && python auto_bot.py
Налаштування у файлі .env (див. .env.example).
"""
import asyncio
import copy
import json
import logging
import os
import re
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone
from logging.handlers import RotatingFileHandler

from telegram import (InlineKeyboardButton, InlineKeyboardMarkup, KeyboardButton, LinkPreviewOptions,
                      ReplyKeyboardMarkup, Update)
from telegram.constants import ParseMode
from telegram.error import TelegramError
from telegram.ext import (Application, CallbackQueryHandler, CommandHandler, ContextTypes,
                          ConversationHandler, MessageHandler, filters)

HERE = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(HERE, "data")
REPORTS_DIR = os.path.join(HERE, "Звіти")
os.makedirs(DATA_DIR, exist_ok=True)
os.environ.setdefault("AUTOMON_CACHE", os.path.join(DATA_DIR, "cache"))
KYIV = timezone(timedelta(hours=3))

from automon import catalog as CAT                      # noqa: E402
from automon.archive import Archive                      # noqa: E402
from automon.caption import Collector, esc               # noqa: E402
from automon.export import build_report                  # noqa: E402
from automon.filters import split_keywords, soft_keywords               # noqa: E402
from automon.market import Market                        # noqa: E402
from automon.run import run_search                       # noqa: E402
from automon.sources import tgchan as TG                 # noqa: E402

# ── .env ───────────────────────────────────────────────────────
def load_env(path=os.path.join(HERE, ".env")):
    if not os.path.exists(path):
        return
    for line in open(path, encoding="utf-8-sig"):
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


load_env()
BOT_TOKEN = os.environ.get("BOT_TOKEN") or os.environ.get("TELEGRAM_BOT_TOKEN", "")
ALLOWED = {x.strip() for x in os.environ.get("ALLOWED_CHAT_IDS", "").split(",") if x.strip()}
SETTINGS = {
    "usd_uah": float(os.environ.get("USD_UAH", "41.5")),
    "max_per_search": int(os.environ.get("MAX_PER_SEARCH", "15")),
    "first_run_send": int(os.environ.get("FIRST_RUN_SEND", "10")),
}

log = logging.getLogger("bot")
NO_PREVIEW = LinkPreviewOptions(is_disabled=True)


def setup_logging():
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    fh = RotatingFileHandler(os.path.join(DATA_DIR, "bot.log"), maxBytes=2_000_000, backupCount=3, encoding="utf-8")
    fh.setFormatter(fmt)
    sh = logging.StreamHandler()
    sh.setFormatter(fmt)
    root.handlers[:] = [fh, sh]
    logging.getLogger("httpx").setLevel(logging.WARNING)


# ── Сховище ────────────────────────────────────────────────────
class DB:
    """Один JSON-файл: власник, пошуки й стан кожного чату. Запис атомарний."""

    def __init__(self, path=os.path.join(DATA_DIR, "db.json")):
        self.path = path
        self.lock = threading.RLock()
        self.data = {"owner": None, "chats": {}}
        if os.path.exists(path):
            with open(path, encoding="utf-8") as f:
                self.data = json.load(f)
        self.data.setdefault("chats", {})

    def save(self):
        with self.lock:
            tmp = self.path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self.data, f, ensure_ascii=False)
            os.replace(tmp, self.path)

    def chat(self, chat_id) -> dict:
        with self.lock:
            return self.data["chats"].setdefault(str(chat_id), {"searches": [], "state": {}})

    def searches(self, chat_id) -> list:
        return self.chat(chat_id)["searches"]

    def get(self, chat_id, sid):
        return next((s for s in self.searches(chat_id) if s["id"] == sid), None)

    def delete(self, chat_id, sid):
        with self.lock:
            c = self.chat(chat_id)
            c["searches"] = [s for s in c["searches"] if s["id"] != sid]
            c["state"].pop(sid, None)
        self.save()


# ── Довідники й підписи ────────────────────────────────────────
INTERVALS = {15: "15 хв", 30: "30 хв", 60: "1 год", 180: "3 год", 360: "6 год"}
WEEKDAYS_FULL = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Нд"]
SOURCES_UA = {"autoria": "AUTO.RIA", "tg": "Telegram-канали"}
COND_FLAGS = {"customs_only": ("🛃 Лише розмитнені", True), "no_damage": ("💥 Без ДТП / пошкоджень", True),
              "in_ukraine": ("🇺🇦 Лише в Україні", True), "owner_only": ("👤 Без автосалонів (AUTO.RIA)", False)}

(S_CARS, S_YEAR, S_PRICE, S_MILEAGE, S_FUEL, S_GEAR, S_BODY, S_DRIVE, S_ENGINE, S_REGION, S_COND,
 S_KEYWORDS, S_SOURCES, S_MARKET, S_FREQ, S_EDIT) = range(16)

MAIN_KB = ReplyKeyboardMarkup(
    [[KeyboardButton("📋 Мої пошуки"), KeyboardButton("➕ Новий пошук")],
     [KeyboardButton("📊 Звіт"), KeyboardButton("❓ Допомога")]],
    resize_keyboard=True, is_persistent=True)


def usd(v):
    return f"${v:,}".replace(",", " ")


def kb(rows):
    return InlineKeyboardMarkup([[InlineKeyboardButton(t, callback_data=d) for t, d in row] for row in rows])


def chunks(items, n):
    return [items[i:i + n] for i in range(0, len(items), n)]


def parse_range(text: str):
    """«2015-2020», «від 40 000», «до 15к», «10к–20к», «45000» (= до 45000) → (min, max)."""
    t = (text or "").lower().replace(" ", " ").replace(",", ".")
    t = re.sub(r"(\d)\s+(?=\d{3}\b)", r"\1", t)

    def num(x):
        m = re.match(r"\s*(\d+(?:\.\d+)?)\s*(к|k|тис|тыс)?", x)
        if not m:
            return None
        v = float(m.group(1))
        return int(v * 1000) if m.group(2) else (v if v != int(v) else int(v))

    lo = hi = None
    m = re.search(r"(\d[\d.]*\s*(?:к|k|тис|тыс)?)\s*(?:-|–|—|\.\.)\s*(\d[\d.]*\s*(?:к|k|тис|тыс)?)", t)
    if m:
        lo, hi = num(m.group(1)), num(m.group(2))
    else:
        m_from = re.search(r"(?:від|от|from|>)\s*(\d[\d.]*\s*(?:к|k|тис|тыс)?)", t)
        m_to = re.search(r"(?:до|to|<)\s*(\d[\d.]*\s*(?:к|k|тис|тыс)?)", t)
        if m_from:
            lo = num(m_from.group(1))
        if m_to:
            hi = num(m_to.group(1))
        if not (m_from or m_to):
            m_one = re.search(r"\d[\d.]*\s*(?:к|k|тис|тыс)?", t)
            if m_one:
                hi = num(m_one.group(0))
    if lo and hi and lo > hi:
        lo, hi = hi, lo
    return lo, hi


def rng_label(r, fmt=str, unit=""):
    r = r or {}
    lo, hi = r.get("min"), r.get("max")
    if lo and hi:
        return f"{fmt(lo)}–{fmt(hi)}{unit}"
    if lo:
        return f"від {fmt(lo)}{unit}"
    if hi:
        return f"до {fmt(hi)}{unit}"
    return ""


def cars_label(s, limit=4):
    pairs = s.get("cars") or []
    if not pairs:
        return "будь-яка марка"
    by = {}
    for p in pairs:
        by.setdefault(p["brand"], []).append(p.get("model") or "")
    parts = []
    for b, ms in by.items():
        ms = [m for m in ms if m]
        parts.append(f"{b} {', '.join(ms)}" if ms else f"{b} (усі моделі)")
    txt = "; ".join(parts)
    return txt if len(txt) < 120 else txt[:117] + "…"


def auto_name(s):
    parts = []
    pairs = s.get("cars") or []
    if pairs:
        names = [f"{p['brand']} {p.get('model') or ''}".strip() for p in pairs]
        parts.append(", ".join(names[:2]) + (f" +{len(names) - 2}" if len(names) > 2 else ""))
    else:
        parts.append("Будь-яке авто")
    if rng_label(s.get("year")):
        parts.append(rng_label(s.get("year")))
    if rng_label(s.get("price_usd")):
        parts.append(rng_label(s.get("price_usd"), usd))
    return " · ".join(parts)


def list_label(sel, catalog_map):
    return ", ".join(catalog_map[x][0] for x in sel if x in catalog_map)


def regions_label(s):
    rs = s.get("regions") or []
    return ", ".join(CAT.REGIONS.get(r, str(r)) for r in rs) if rs else "вся Україна"


def cond_label(s):
    on = [COND_FLAGS[k][0] for k in COND_FLAGS if s.get(k, COND_FLAGS[k][1])]
    return " · ".join(on)


def market_label(s):
    bm = s.get("below_market")
    if bm is None:
        return ""
    return "📉 лише нижче медіани ринку" if int(bm) == 0 else f"📉 лише на {int(bm)}%+ дешевше ринку"


def summary(s):
    lines = [f"📋 <b>{esc(s['name'])}</b>", f"🚗 {esc(cars_label(s))}"]
    l2 = [x for x in [("📅 " + rng_label(s.get("year"))) if rng_label(s.get("year")) else "",
                      ("💰 " + rng_label(s.get("price_usd"), usd)) if rng_label(s.get("price_usd")) else "",
                      ("🛣 " + rng_label(s.get("mileage"), str, " тис. км")) if rng_label(s.get("mileage")) else ""] if x]
    if l2:
        lines.append(" · ".join(l2))
    tech = [x for x in [list_label(s.get("fuels") or [], CAT.FUELS),
                        CAT.GEARBOXES[s["gearbox"]][0] if s.get("gearbox") else "",
                        list_label(s.get("bodies") or [], CAT.BODIES),
                        (list_label(s.get("drives") or [], CAT.DRIVES) + " привід") if s.get("drives") else "",
                        ("🔧 " + rng_label(s.get("engine"), lambda v: f"{v:g}", " л")) if rng_label(s.get("engine")) else ""] if x]
    if tech:
        lines.append("⚙️ " + " · ".join(tech))
    lines.append(f"📍 {regions_label(s)}")
    if cond_label(s):
        lines.append(cond_label(s))
    inc, exc = split_keywords(s)
    if inc:
        lines.append("🔑 " + esc(", ".join(inc)))
    if soft_keywords(s):
        lines.append("⭐ бажано: " + esc(", ".join(soft_keywords(s))))
    if exc:
        lines.append("🚫 " + esc(", ".join(exc)))
    if market_label(s):
        lines.append(market_label(s))
    src = " + ".join(SOURCES_UA[x] for x in s.get("sources") or [])
    status = "⏸ на паузі" if s.get("paused") else freq_text(s)
    lines.append(f"🔔 {status} · {src}")
    if s.get("last_run"):
        lines.append(f"🕒 остання перевірка: {s['last_run']}")
    return "\n".join(lines)


# ── Доступ ─────────────────────────────────────────────────────
def is_owner(chat_id, db: DB) -> bool:
    return db.data.get("owner") is not None and str(chat_id) == str(db.data["owner"])


def chat_allowed(chat_id, db: DB) -> bool:
    cid = str(chat_id)
    if cid in ALLOWED or is_owner(cid, db):
        return True
    return cid in (db.data.get("users") or {})


def allowed(update: Update, db: DB) -> bool:
    if db.data.get("owner") is None and not ALLOWED:
        return True          # перший /start зробить цей чат власником
    return chat_allowed(update.effective_chat.id, db)


def norm_phone(p: str) -> str:
    d = re.sub(r"\D", "", p or "")
    if len(d) == 10 and d.startswith("0"):
        d = "38" + d
    elif len(d) == 9:
        d = "380" + d
    return d


CONTACT_KB = ReplyKeyboardMarkup([[KeyboardButton("📱 Поділитися номером", request_contact=True)]],
                                 resize_keyboard=True, one_time_keyboard=True)


def owner_only(fn):
    async def wrapper(update: Update, ctx: ContextTypes.DEFAULT_TYPE, *a, **kw):
        if not is_owner(update.effective_chat.id, ctx.bot_data["db"]):
            if update.callback_query:
                await update.callback_query.answer("Лише для власника бота", show_alert=True)
            else:
                await update.effective_message.reply_text("Ця команда лише для власника бота.")
            return
        return await fn(update, ctx, *a, **kw)
    wrapper.__name__ = fn.__name__
    return wrapper


def guard(fn):
    async def wrapper(update: Update, ctx: ContextTypes.DEFAULT_TYPE, *a, **kw):
        db: DB = ctx.bot_data["db"]
        if not allowed(update, db):
            if update.callback_query:
                await update.callback_query.answer("Це приватний бот", show_alert=True)
            elif update.effective_message:
                await update.effective_message.reply_text(
                    "🔒 Це приватний бот.\n\nЯкщо власник дав тобі доступ за номером телефону — "
                    "натисни кнопку нижче, щоб поділитися номером.", reply_markup=CONTACT_KB)
            return ConversationHandler.END
        return await fn(update, ctx, *a, **kw)
    wrapper.__name__ = fn.__name__
    return wrapper


# ── Перевірка пошуку ───────────────────────────────────────────
CHECK_LOCK = asyncio.Lock()


async def check_search(app: Application, chat_id: int, sid: str, manual=False):
    db: DB = app.bot_data["db"]
    async with CHECK_LOCK:
        s = db.get(chat_id, sid)
        if not s:
            return
        chat = db.chat(chat_id)
        with db.lock:
            st = copy.deepcopy(chat["state"].get(sid, {}))
            scfg = copy.deepcopy(s)
        report = {}
        collector = Collector()                     # повідомлення лише збираються, надсилає бот
        outbox = collector.outbox
        t0 = time.time()
        try:
            await asyncio.to_thread(run_search, scfg, st, collector, SETTINGS, report, app.bot_data["archive"],
                                    app.bot_data.get("market"))
        except Exception:
            log.exception("Перевірка %s впала", sid)
            if manual:
                await app.bot.send_message(chat_id, "⚠️ Не вдалося перевірити, деталі в data/bot.log")
            return
        rep = report.get(sid, {})
        digest = bool(s.get("digest"))
        with db.lock:
            if db.get(chat_id, sid) is None:       # пошук видалили, поки перевіряли
                return
            if digest:
                # режим дайджесту: складаємо в чергу; надсилаємо за розкладом або при ручній перевірці
                pending = (chat["state"].get(sid) or {}).get("pending", []) + outbox
                st["pending"] = [] if manual else pending[-200:]
                if manual:
                    outbox = pending
                else:
                    outbox = []
            chat["state"][sid] = st
            s["last_run"] = datetime.now(KYIV).strftime("%d.%m %H:%M")
            s["next_due"] = time.time() + int(s.get("interval", 30)) * 60
            s["last_report"] = {k: v for k, v in rep.get("sources", {}).items()}
        db.save()
        log.info("Перевірка %s за %.1fс: %s", sid, time.time() - t0, json.dumps(rep, ensure_ascii=False))

    sent = await deliver(app, chat_id, outbox)
    if manual and sent == 0:
        errs = [f"{k}: {v['error']}" for k, v in rep.get("sources", {}).items() if "error" in v]
        txt = "Нових оголошень немає ✅" if not errs else "⚠️ Частина джерел не відповіла:\n" + "\n".join(errs)
        await app.bot.send_message(chat_id, txt[:3500])


async def deliver(app: Application, chat_id: int, outbox: list) -> int:
    sent = 0
    for m in outbox:
        try:
            if m["type"] == "text":
                await app.bot.send_message(chat_id, m["text"], parse_mode=ParseMode.HTML,
                                           link_preview_options=NO_PREVIEW)
            else:
                btn = InlineKeyboardMarkup([[InlineKeyboardButton(f"Відкрити на {m.get('origin') or 'сайті'}",
                                                                  url=m["url"])]])
                try:
                    if not m.get("photo"):
                        raise TelegramError("no photo")
                    await app.bot.send_photo(chat_id, m["photo"], caption=m["caption"],
                                             parse_mode=ParseMode.HTML, reply_markup=btn)
                except TelegramError:
                    await app.bot.send_message(chat_id, m["caption"], parse_mode=ParseMode.HTML,
                                               reply_markup=btn, link_preview_options=NO_PREVIEW)
                sent += 1
        except TelegramError as e:
            log.warning("Не вдалося надіслати: %s", e)
        await asyncio.sleep(1.0)
    return sent


WEEKDAYS = ["пн", "вт", "ср", "чт", "пт", "сб", "нд"]


def digest_kind(dg: dict) -> str:
    return dg.get("kind") or ("daily" if dg.get("dow") is None else "weekly")


def next_digest(dg: dict, after: datetime = None) -> float:
    """Наступний час дайджесту за місцевим часом комп'ютера."""
    import calendar
    now = after or datetime.now()
    hh, mm = int(dg.get("hour", 14)), int(dg.get("minute", 0))
    kind = digest_kind(dg)
    if kind == "monthly":
        dom = int(dg.get("dom", 1))
        y, m = now.year, now.month
        for _ in range(3):
            d = min(dom, calendar.monthrange(y, m)[1])
            t = datetime(y, m, d, hh, mm)
            if t > now:
                return t.timestamp()
            y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    t = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
    if kind == "daily":
        if t <= now:
            t += timedelta(days=1)
    else:
        t += timedelta(days=(int(dg.get("dow", 5)) - t.weekday()) % 7)
        if t <= now:
            t += timedelta(days=7)
    return t.timestamp()


def digest_label(dg: dict) -> str:
    kind = digest_kind(dg)
    when = {"daily": "щодня",
            "weekly": f"щотижня, {WEEKDAYS[int(dg.get('dow', 5))]}",
            "monthly": f"щомісяця, {int(dg.get('dom', 1))}-го"}[kind]
    return f"🗓 дайджест {when} о {int(dg.get('hour', 14)):02d}:{int(dg.get('minute', 0)):02d}"


async def send_digest(app: Application, chat_id: int, sid: str):
    db: DB = app.bot_data["db"]
    with db.lock:
        s = db.get(chat_id, sid)
        if not s:
            return
        st = db.chat(chat_id)["state"].setdefault(sid, {})
        pending, st["pending"] = st.get("pending", []), []
        s["digest_next"] = next_digest(s["digest"])
    db.save()
    listings = [m for m in pending if m["type"] == "listing"]
    others = [m for m in pending if m["type"] != "listing"]
    cap = 25
    head = (f"🗓 <b>Дайджест · {esc(s['name'])}</b>\n"
            + (f"Нових оголошень: <b>{len(listings)}</b>" if listings else "Нових оголошень за цей період немає."))
    if len(listings) > cap:
        head += f"\nПоказую {cap} найсвіжіших, усі — у «📊 Звіт»."
    await app.bot.send_message(chat_id, head, parse_mode=ParseMode.HTML)
    await deliver(app, chat_id, others + listings[-cap:])


async def tick(ctx: ContextTypes.DEFAULT_TYPE):
    """Раз на хвилину: запускає перевірки, у яких настав час."""
    db: DB = ctx.bot_data["db"]
    now = time.time()
    due = []
    with db.lock:
        for cid, chat in db.data["chats"].items():
            if not chat_allowed(cid, db):
                continue
            for s in chat["searches"]:
                if not s.get("paused") and s.get("next_due", 0) <= now:
                    due.append((int(cid), s["id"]))
    for cid, sid in due:
        await check_search(ctx.application, cid, sid)
    # дайджести, у яких настав час
    digests = []
    with db.lock:
        for cid, chat in db.data["chats"].items():
            if not chat_allowed(cid, db):
                continue
            for s in chat["searches"]:
                if s.get("digest") and not s.get("paused"):
                    if not s.get("digest_next"):
                        s["digest_next"] = next_digest(s["digest"])
                    elif s["digest_next"] <= time.time():
                        digests.append((int(cid), s["id"]))
    for cid, sid in digests:
        await check_search(ctx.application, cid, sid)       # свіжа перевірка перед відправкою
        await send_digest(ctx.application, cid, sid)


# ═══════════════════════════════════════════════════════════════
# ДІАЛОГ: СТВОРЕННЯ / РЕДАГУВАННЯ ПОШУКУ АВТО
# ═══════════════════════════════════════════════════════════════
async def show(src, text, markup=None):
    """src — CallbackQuery (редагуємо повідомлення) або Message (надсилаємо нове)."""
    if hasattr(src, "edit_message_text"):
        await src.edit_message_text(text, parse_mode=ParseMode.HTML, reply_markup=markup)
    else:
        await src.reply_text(text, parse_mode=ParseMode.HTML, reply_markup=markup)


async def after(src, ctx, next_step):
    if ctx.user_data.get("edit"):
        return await ask_edit_menu(src, ctx)
    return await next_step(src, ctx)


def toggle_kb(prefix, options, selected, cols=2, extra=None):
    rows = chunks([(("✅ " if k in selected else "") + label, f"{prefix}:{k}") for k, label in options], cols)
    rows.append(extra or [("🔄 Будь-яке", f"{prefix}:any"), ("✔️ Готово", f"{prefix}:done")])
    return kb(rows)


@guard
async def new_search(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ctx.user_data["edit"] = False
    ctx.user_data["new"] = {"id": uuid.uuid4().hex[:8], "sources": ["autoria", "tg"], "cars": [], "interval": 30,
                            "customs_only": True, "no_damage": True, "in_ukraine": True, "v": SCHEMA_V}
    return await ask_cars(update.effective_message, ctx)


@guard
async def edit_search(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    db: DB = ctx.bot_data["db"]
    s = db.get(q.message.chat_id, q.data.split(":", 1)[1])
    if not s:
        await q.message.reply_text("Пошук не знайдено")
        return ConversationHandler.END
    ctx.user_data["edit"] = True
    ctx.user_data["new"] = copy.deepcopy(s)
    return await ask_edit_menu(q, ctx, new_message=True)


async def ask_edit_menu(src, ctx, new_message=False):
    s = ctx.user_data["new"]
    rows = [[("🚗 Марки / моделі", "ed:cars")],
            [("📅 Рік", "ed:year"), ("💰 Ціна", "ed:price"), ("🛣 Пробіг", "ed:mileage")],
            [("⛽ Пальне", "ed:fuel"), ("⚙️ КПП", "ed:gear"), ("🚙 Кузов", "ed:body")],
            [("🧭 Привід", "ed:drive"), ("🔧 Двигун", "ed:engine"), ("📍 Регіон", "ed:region")],
            [("🛃 Стан", "ed:cond"), ("📢 Джерела", "ed:src"), ("📉 Ринок", "ed:market")],
            [("🔑 Ключові слова", "ed:kw"), ("⏱ Частота", f"fq:{s['id']}:menu")],
            [("✅ Зберегти", "ed:save"), ("❌ Скасувати", "ed:cancel")]]
    s["name"] = auto_name(s)
    text = summary(s) + "\n\n✏️ <b>Що змінити?</b>"
    if new_message:
        await src.message.reply_text(text, parse_mode=ParseMode.HTML, reply_markup=kb(rows))
    else:
        await show(src, text, kb(rows))
    return S_EDIT


async def cb_edit_menu(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    act = q.data.split(":", 1)[1]
    steps = {"cars": ask_cars, "year": ask_year, "price": ask_price, "mileage": ask_mileage, "fuel": ask_fuel,
             "gear": ask_gear, "body": ask_body, "drive": ask_drive, "engine": ask_engine, "region": ask_region,
             "cond": ask_cond, "src": ask_sources, "market": ask_market, "kw": ask_keywords}
    if act in steps:
        return await steps[act](q, ctx)
    if act == "cancel":
        ctx.user_data.pop("new", None)
        await q.edit_message_text("Зміни скасовано.")
        return ConversationHandler.END
    return await finish(q, ctx)


# ── Марка / модель ─────────────────────────────────────────────
def _pairs(s):
    return s.setdefault("cars", [])


async def ask_cars(src, ctx, note=""):
    s = ctx.user_data["new"]
    ctx.user_data["car_brand"] = None
    pop = [b for b in (CAT.brand_by_name(n) for n in CAT.POPULAR_BRANDS) if b]
    rows = chunks([(b["name"], f"cb:{b['value']}") for b in pop], 3)
    rows.append([("🔎 Інша марка — напиши назву", "cq")])
    bottom = [("🔄 Будь-яка марка", "cx")]
    if _pairs(s):
        bottom = [("🗑 Очистити", "cc"), ("✔️ Готово", "cd")]
    rows.append(bottom)
    chosen = f"\nОбрано: <b>{esc(cars_label(s))}</b>" if _pairs(s) else ""
    await show(src, "🚗 <b>Марка й модель</b> — обери марку (можна кілька пар марка/модель)."
                    f"{chosen}{note}\n<i>Можна просто написати назву, напр. «Skoda» або «Камрі».</i>", kb(rows))
    return S_CARS


async def show_brand(src, ctx, brand_id, models=None, note=""):
    s = ctx.user_data["new"]
    b = CAT.brand_by_id(brand_id)
    if not b:
        return await ask_cars(src, ctx)
    ctx.user_data["car_brand"] = brand_id
    ms = models if models is not None else (CAT.popular_models(b["name"], brand_id) or CAT.models(brand_id)[:16])
    sel = {p.get("model_id") for p in _pairs(s) if p["brand_id"] == brand_id}
    all_sel = None in sel
    rows = chunks([(("✅ " if m["value"] in sel else "") + m["name"], f"cm:{brand_id}:{m['value']}") for m in ms[:24]], 2)
    rows.append([(("✅ " if all_sel else "") + f"Усі моделі {b['name']}", f"ca:{brand_id}")])
    rows.append([("⬅️ Марки", "cs"), ("✔️ Готово", "cd")])
    await show(src, f"🚗 <b>{esc(b['name'])}</b> — познач моделі."
                    f"{note}\n<i>Немає потрібної? Напиши назву моделі, напр. «Octavia» чи «X5».</i>", kb(rows))
    return S_CARS


async def cb_cars(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    s = ctx.user_data["new"]
    d = q.data
    if d == "cq":
        ctx.user_data["car_brand"] = None
        await show(q, "🔎 Напиши назву марки (латиницею або кирилицею):", kb([[("⬅️ Марки", "cs")]]))
        return S_CARS
    if d == "cs":
        return await ask_cars(q, ctx)
    if d == "cc":
        s["cars"] = []
        return await ask_cars(q, ctx)
    if d == "cx":
        s["cars"] = []
        return await after(q, ctx, ask_year)
    if d == "cd":
        return await after(q, ctx, ask_year)
    if d.startswith("cb:"):
        return await show_brand(q, ctx, int(d[3:]))
    if d.startswith("ca:"):
        bid = int(d[3:])
        b = CAT.brand_by_id(bid)
        pairs = [p for p in _pairs(s) if p["brand_id"] != bid]
        if not any(p["brand_id"] == bid and p.get("model_id") is None for p in _pairs(s)):
            pairs.append({"brand_id": bid, "brand": b["name"], "model_id": None, "model": ""})
        s["cars"] = pairs
        return await show_brand(q, ctx, bid)
    if d.startswith("cm:"):
        _, bid, mid = d.split(":")
        bid, mid = int(bid), int(mid)
        b = CAT.brand_by_id(bid)
        m = CAT.model_by_id(bid, mid)
        pairs = [p for p in _pairs(s) if not (p["brand_id"] == bid and p.get("model_id") is None)]
        if any(p["brand_id"] == bid and p.get("model_id") == mid for p in pairs):
            pairs = [p for p in pairs if not (p["brand_id"] == bid and p.get("model_id") == mid)]
        else:
            pairs.append({"brand_id": bid, "brand": b["name"], "model_id": mid, "model": m["name"] if m else str(mid)})
        s["cars"] = pairs
        found = ctx.user_data.get("car_found")
        return await show_brand(q, ctx, bid, models=found if found and found[0] == bid else None and None) \
            if not (found and found[0] == bid) else await show_brand(q, ctx, bid, models=found[1])
    return S_CARS


async def txt_cars(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    text = (update.message.text or "").strip()
    bid = ctx.user_data.get("car_brand")
    if bid:
        ms = CAT.search_models(bid, text)
        if not ms:
            await update.message.reply_text("Не знайшов такої моделі. Спробуй інакше (напр. «Land Cruiser Prado»).")
            return S_CARS
        ctx.user_data["car_found"] = (bid, ms)
        return await show_brand(update.message, ctx, bid, models=ms, note=f"\nЗнайдено за «{esc(text)}»:")
    bs = CAT.search_brands(text)
    if not bs:
        await update.message.reply_text("Не знайшов такої марки. Спробуй латиницею, напр. «Hyundai».")
        return S_CARS
    if len(bs) == 1 or CAT.norm(bs[0]["name"]) == CAT.norm(text):
        return await show_brand(update.message, ctx, bs[0]["value"])
    rows = chunks([(b["name"], f"cb:{b['value']}") for b in bs[:18]], 3)
    rows.append([("⬅️ Марки", "cs")])
    await update.message.reply_text("Яку саме марку?", reply_markup=kb(rows))
    return S_CARS


# ── Діапазони: рік, ціна, пробіг, двигун ───────────────────────
RANGE_STEPS = {
    "year": ("📅 <b>Рік випуску.</b> Напиши діапазон: <code>2015-2020</code>, <code>від 2017</code>",
             [("від 2010", "min:2010"), ("від 2015", "min:2015"), ("від 2018", "min:2018"), ("від 2020", "min:2020"),
              ("від 2022", "min:2022")], (1985, 2031)),
    "price_usd": ("💰 <b>Ціна, $.</b> Напиши діапазон: <code>8000-15000</code>, <code>до 20к</code>",
                  [("до $5k", "max:5000"), ("до $10k", "max:10000"), ("до $15k", "max:15000"), ("до $20k", "max:20000"),
                   ("до $30k", "max:30000"), ("до $50k", "max:50000")], (300, 3_000_000)),
    "mileage": ("🛣 <b>Пробіг, тис. км.</b> Напиши діапазон або обери: <code>до 150</code>",
                [("до 50", "max:50"), ("до 100", "max:100"), ("до 150", "max:150"), ("до 200", "max:200"),
                 ("до 250", "max:250")], (0, 1500)),
    "engine": ("🔧 <b>Об'єм двигуна, л.</b> Напиши діапазон: <code>1.6-2.5</code>, <code>від 2</code>",
               [("до 1.6", "max:1.6"), ("1.6–2.0", "rng:1.6:2.0"), ("2.0–3.0", "rng:2.0:3.0"), ("від 3.0", "min:3.0")],
               (0.5, 8.0)),
}
RANGE_NEXT = {"year": "ask_price", "price_usd": "ask_mileage", "mileage": "ask_fuel", "engine": "ask_region"}
RANGE_STATE = {"year": S_YEAR, "price_usd": S_PRICE, "mileage": S_MILEAGE, "engine": S_ENGINE}


async def ask_range(src, ctx, field):
    s = ctx.user_data["new"]
    ctx.user_data["range_field"] = field
    text, quick, _ = RANGE_STEPS[field]
    cur = rng_label(s.get(field), usd if field == "price_usd" else (lambda v: f"{v:g}"))
    rows = chunks([(t, f"rg:{field}:{v}") for t, v in quick], 3)
    rows.append([("Без обмеження", f"rg:{field}:none")])
    await show(src, text + (f"\nЗараз: <b>{cur}</b>" if cur else ""), kb(rows))
    return RANGE_STATE[field]


async def ask_year(src, ctx):
    return await ask_range(src, ctx, "year")


async def ask_price(src, ctx):
    return await ask_range(src, ctx, "price_usd")


async def ask_mileage(src, ctx):
    return await ask_range(src, ctx, "mileage")


async def ask_engine(src, ctx):
    return await ask_range(src, ctx, "engine")


async def _range_done(src, ctx, field):
    return await after(src, ctx, globals()[RANGE_NEXT[field]])


async def cb_range(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    s = ctx.user_data["new"]
    _, field, *v = q.data.split(":")
    if v[0] == "none":
        s.pop(field, None)
    elif v[0] == "min":
        s[field] = {"min": float(v[1]) if "." in v[1] else int(v[1]), "max": None}
    elif v[0] == "max":
        s[field] = {"min": None, "max": float(v[1]) if "." in v[1] else int(v[1])}
    elif v[0] == "rng":
        s[field] = {"min": float(v[1]), "max": float(v[2])}
    return await _range_done(q, ctx, field)


async def txt_range(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    field = ctx.user_data.get("range_field")
    s = ctx.user_data["new"]
    raw = (update.message.text or "").lower()
    if field == "mileage":
        raw = re.sub(r"тис\w*|тыс\w*|км|km", "", raw)
    if field in ("year", "mileage", "engine"):
        raw = re.sub(r"(?<=\d)\s*(?:к|k)\b", "", raw)       # «2015к» не множимо
    lo, hi = parse_range(raw)
    _, _, (mn, mx) = RANGE_STEPS[field]
    if field == "price_usd":
        if hi and hi < 300:                                 # «15» → 15 000
            lo, hi = (lo * 1000 if lo else lo), hi * 1000
        elif lo and lo < 300 and hi and hi >= 1000:         # «40-70к» → 40 000–70 000
            lo *= 1000
    if not (lo or hi) or any(x is not None and not (mn <= x <= mx) for x in (lo, hi)):
        await update.message.reply_text("⚠️ Не зрозумів. Приклад: " + {"year": "2015-2020", "price_usd": "8000-15000",
                                        "mileage": "до 150", "engine": "1.6-2.5"}[field])
        return RANGE_STATE[field]
    s[field] = {"min": lo, "max": hi}
    await update.message.reply_text("✅ " + rng_label(s[field], usd if field == "price_usd" else (lambda v: f"{v:g}")))
    return await _range_done(update.message, ctx, field)


# ── Мультивибір: пальне, кузов, привід, регіон; КПП ────────────
async def ask_fuel(src, ctx):
    s = ctx.user_data["new"]
    await show(src, "⛽ <b>Пальне</b> (можна кілька):",
               toggle_kb("fu", [(k, v[0]) for k, v in CAT.FUELS.items()], s.get("fuels") or []))
    return S_FUEL


async def ask_body(src, ctx):
    s = ctx.user_data["new"]
    await show(src, "🚙 <b>Кузов</b> (можна кілька):",
               toggle_kb("bo", [(k, v[0]) for k, v in CAT.BODIES.items()], s.get("bodies") or []))
    return S_BODY


async def ask_drive(src, ctx):
    s = ctx.user_data["new"]
    await show(src, "🧭 <b>Привід</b>:", toggle_kb("dr", [(k, v[0]) for k, v in CAT.DRIVES.items()], s.get("drives") or [], cols=3))
    return S_DRIVE


async def ask_region(src, ctx):
    s = ctx.user_data["new"]
    opts = [(str(r), CAT.REGIONS[r]) for r in CAT.REGION_ORDER]
    await show(src, "📍 <b>Регіон</b> (можна кілька; «Будь-яке» — вся Україна):",
               toggle_kb("rg2", opts, [str(r) for r in s.get("regions") or []], cols=3))
    return S_REGION


MULTI = {"fu": ("fuels", ask_fuel, "ask_gear"), "bo": ("bodies", ask_body, "ask_region"),
         "dr": ("drives", ask_drive, "ask_region"), "rg2": ("regions", ask_region, "ask_cond")}


async def cb_multi(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    s = ctx.user_data["new"]
    prefix, val = q.data.split(":", 1)
    field, ask, nxt = MULTI[prefix]
    if val == "any":
        s.pop(field, None)
        return await after(q, ctx, globals()[nxt])
    if val == "done":
        if not s.get(field):
            s.pop(field, None)
        return await after(q, ctx, globals()[nxt])
    v = int(val) if field == "regions" else val
    sel = s.setdefault(field, [])
    sel.remove(v) if v in sel else sel.append(v)
    return await ask(q, ctx)


async def ask_gear(src, ctx):
    s = ctx.user_data["new"]
    cur = s.get("gearbox")
    rows = [[(("✅ " if cur == k else "") + v[0], f"ge:{k}") for k, v in CAT.GEARBOXES.items()],
            [(("✅ " if not cur else "") + "🔄 Будь-яка", "ge:any")]]
    await show(src, "⚙️ <b>Коробка передач</b>:\n<i>Автомат — разом з роботом, варіатором і типтроніком.</i>", kb(rows))
    return S_GEAR


async def cb_gear(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    s = ctx.user_data["new"]
    v = q.data.split(":", 1)[1]
    if v == "any":
        s.pop("gearbox", None)
    else:
        s["gearbox"] = v
    return await after(q, ctx, ask_body)


# ── Стан, джерела, ринок, ключові слова ────────────────────────
async def ask_cond(src, ctx):
    s = ctx.user_data["new"]
    rows = [[(("✅ " if s.get(k, d) else "⬜️ ") + t, f"co:{k}")] for k, (t, d) in COND_FLAGS.items()]
    rows.append([("✔️ Готово", "co:done")])
    await show(src, "🛃 <b>Стан і продавець</b> — натискай, щоб увімкнути/вимкнути:", kb(rows))
    return S_COND


async def cb_cond(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    s = ctx.user_data["new"]
    k = q.data.split(":", 1)[1]
    if k == "done":
        return await after(q, ctx, ask_keywords)
    s[k] = not s.get(k, COND_FLAGS[k][1])
    return await ask_cond(q, ctx)


async def ask_sources(src, ctx):
    s = ctx.user_data["new"]
    sel = s.get("sources") or []
    chans = s.get("channels") or TG.DEFAULT_CHANNELS
    rows = [[(("✅ " if k in sel else "⬜️ ") + v, f"so:{k}")] for k, v in SOURCES_UA.items()]
    rows.append([("📢 Канали", "so:ch"), ("✔️ Готово", "so:done")])
    await show(src, "📢 <b>Де шукати</b>:\n"
                    f"Telegram-каналів: {len(chans)} (<i>{esc(', '.join('@' + c for c in chans[:6]))}"
                    f"{'…' if len(chans) > 6 else ''}</i>)", kb(rows))
    return S_SOURCES


async def ask_channels(src, ctx):
    s = ctx.user_data["new"]
    chans = s.get("channels") or list(TG.DEFAULT_CHANNELS)
    s["channels"] = chans
    rows = chunks([(f"🗑 @{c}", f"so:rm:{c}") for c in chans], 2)
    rows.append([("↩️ Стандартний список", "so:reset"), ("✔️ Готово", "so:back")])
    await show(src, "📢 <b>Telegram-канали</b> — натисни, щоб прибрати.\n"
                    "Щоб <b>додати</b>, напиши @назву або посилання t.me/… (лише публічні канали).", kb(rows))
    ctx.user_data["adding_channel"] = True
    return S_SOURCES


async def cb_sources(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    s = ctx.user_data["new"]
    v = q.data.split(":", 1)[1]
    if v == "done":
        if not s.get("sources"):
            s["sources"] = ["autoria"]
        ctx.user_data.pop("adding_channel", None)
        return await after(q, ctx, ask_market)
    if v == "ch":
        return await ask_channels(q, ctx)
    if v == "back":
        ctx.user_data.pop("adding_channel", None)
        return await ask_sources(q, ctx)
    if v == "reset":
        s["channels"] = list(TG.DEFAULT_CHANNELS)
        return await ask_channels(q, ctx)
    if v.startswith("rm:"):
        s["channels"] = [c for c in s.get("channels") or [] if c != v[3:]]
        return await ask_channels(q, ctx)
    sel = s.setdefault("sources", [])
    sel.remove(v) if v in sel else sel.append(v)
    return await ask_sources(q, ctx)


async def txt_sources(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    s = ctx.user_data["new"]
    m = re.search(r"(?:t\.me/(?:s/)?|@)?([A-Za-z][A-Za-z0-9_]{3,40})", update.message.text or "")
    if not m:
        await update.message.reply_text("Напиши @назву каналу, напр. @KievavtoSale")
        return S_SOURCES
    ch = m.group(1)
    await update.message.reply_text(f"⏳ Перевіряю @{ch}…")
    try:
        r = await asyncio.to_thread(lambda: __import__("automon.common", fromlist=["get"]).get(f"https://t.me/s/{ch}", retries=1))
        posts = TG.parse_posts(r.text, ch)
    except Exception:
        posts = None
    if not posts:
        await update.message.reply_text("⚠️ Не вдалося прочитати канал. Він має бути публічним (з переглядом без підписки).")
        return S_SOURCES
    chans = s.setdefault("channels", list(TG.DEFAULT_CHANNELS))
    if ch not in chans:
        chans.append(ch)
    ads = sum(1 for p in posts if TG.to_car(p, ch))
    await update.message.reply_text(f"✅ Додано @{ch}: з {len(posts)} останніх постів {ads} схожі на оголошення.")
    return await ask_channels(update.message, ctx)


MARKET_OPTS = {"off": ("🔄 Усі, з позначкою «дешевше/дорожче ринку»", None),
               "0": ("📉 Лише нижче медіани", 0),
               "10": ("📉 На 10%+ дешевше ринку", 10),
               "20": ("🔥 На 20%+ дешевше ринку", 20)}


async def ask_market(src, ctx):
    s = ctx.user_data["new"]
    cur = s.get("below_market")
    rows = [[(("✅ " if (cur is None and k == "off") or (cur is not None and str(cur) == k) else "") + t, f"mk:{k}")]
            for k, (t, _) in MARKET_OPTS.items()]
    await show(src, "📉 <b>Порівняння з ринком</b>.\n"
                    "Порівнюю ціну з медіаною тієї ж моделі й року (якщо даних досить — ще й того ж пального та КПП). "
                    "Раз на добу збираю ринкову вибірку обраних моделей без твого фільтра ціни.\n"
                    "<i>Без вибраних марок/моделей порівнюю з тим, що трапляється в пошуку.</i>", kb(rows))
    return S_MARKET


async def cb_market(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    s = ctx.user_data["new"]
    k = q.data.split(":", 1)[1]
    if MARKET_OPTS[k][1] is None:
        s.pop("below_market", None)
    else:
        s["below_market"] = MARKET_OPTS[k][1]
    return await after(q, ctx, ask_freq_new)


async def ask_keywords(src, ctx):
    s = ctx.user_data["new"]
    cur = ", ".join(s.get("keywords") or [])
    await show(src,
               "🔑 <b>Ключові слова</b> через кому:\n"
               "• звичайне слово — <b>обов'язкове</b> (авто має містити хоч одне з таких);\n"
               "• <b>+слово</b> — <b>бажане</b>: авто не відсіюється, але з ним піде першим із позначкою ⭐, "
               "а без — із приміткою «не згадано»;\n"
               "• <b>-слово</b> — виключити.\n\n"
               "🧠 Розумію відмінки, українську й російську та формулювання: «не фарбований» знайде й «в рідній фарбі», "
               "«один власник» — й «1 владелец».\n"
               "Готові поняття: не фарбований, один власник, офіційний, сервісна історія, без ДТП, рідний пробіг, "
               "обмін, кредит, гарантія, зі США, з Європи, панорама, шкіряний салон, 7 місць, фаркоп, два ключі.\n\n"
               "<i>Напр.: +шкіряний салон, +панорама, -зі сша</i>\n"
               + (f"\nЗараз: <code>{esc(cur)}</code>\n" if cur else "")
               + "\n/skip — без ключових слів")
    return S_KEYWORDS


async def txt_keywords(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ctx.user_data["new"]["keywords"] = [w.strip() for w in update.message.text.split(",") if w.strip()]
    return await after(update.message, ctx, ask_sources)


async def skip_keywords(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ctx.user_data["new"]["keywords"] = []
    return await after(update.message, ctx, ask_sources)


async def finish(src, ctx):
    db: DB = ctx.bot_data["db"]
    msg = src.message if hasattr(src, "edit_message_text") else src
    chat_id = msg.chat_id
    s = ctx.user_data.pop("new")
    editing = ctx.user_data.pop("edit", False)
    s["name"] = auto_name(s)
    s["next_due"] = 0
    s["v"] = SCHEMA_V
    with db.lock:
        lst = db.searches(chat_id)
        if editing:
            for i, x in enumerate(lst):
                if x["id"] == s["id"]:
                    lst[i] = s
                    break
            else:
                lst.append(s)
            st = db.chat(chat_id)["state"].get(s["id"], {})
            db.chat(chat_id)["state"][s["id"]] = {"sent": st.get("sent", {})}     # нові фільтри → переглянути заново
        else:
            lst.append(s)
    db.save()
    if editing:
        try:
            ctx.bot_data["archive"].clear(s["id"])
        except Exception:
            log.exception("Не вдалося очистити архів %s", s["id"])
    tail = ("✅ Оновлено. Переглядаю все заново з новими фільтрами (уже надіслане не повторюватиму)."
            if editing else "✅ Збережено. Зараз покажу найсвіжіші, далі — лише нові.")
    await msg.reply_text(summary(s) + "\n\n" + tail, parse_mode=ParseMode.HTML, reply_markup=MAIN_KB)
    asyncio.create_task(check_search(ctx.application, chat_id, s["id"], manual=True))
    return ConversationHandler.END


async def cancel(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ctx.user_data.pop("new", None)
    ctx.user_data.pop("edit", None)
    await update.effective_message.reply_text("Скасовано.", reply_markup=MAIN_KB)
    return ConversationHandler.END


async def cancel_and_list(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ctx.user_data.pop("new", None)
    ctx.user_data.pop("edit", None)
    await my_searches(update, ctx)
    return ConversationHandler.END


# ── Частота сповіщень ─────────────────────────────────────────
FREQ_TIMES = [9, 12, 14, 18, 21]


def freq_menu_kb(sid):
    return kb([[("⚡ 30 хв", f"fq:{sid}:i:30"), ("🕐 Щогодини", f"fq:{sid}:i:60"), ("🕒 Кожні 3 год", f"fq:{sid}:i:180")],
               [("📅 Щодня", f"fq:{sid}:d"), ("🗓 Щотижня", f"fq:{sid}:w"), ("📆 Раз на місяць", f"fq:{sid}:m")]])


def time_kb(sid, mode, arg):
    return kb([[(f"{h:02d}:00", f"fq:{sid}:t:{mode}:{arg}:{h}") for h in FREQ_TIMES]])


async def ask_freq_new(src, ctx):
    s = ctx.user_data["new"]
    await show(src, "⏱ <b>Як часто повідомляти?</b>\n"
                    "• 30 хв / щогодини / кожні 3 год — кожне нове оголошення приходить одразу після перевірки.\n"
                    "• Щодня / щотижня / раз на місяць — збираю все в один дайджест у вибраний час "
                    "(перевіряю у фоні, нічого не пропаде).", freq_menu_kb(s["id"]))
    return S_FREQ


def apply_freq(s, parts):
    """parts: ['i', '60'] або ['t', mode, arg, hour]."""
    if parts[0] == "i":
        s.pop("digest", None)
        s.pop("digest_next", None)
        s["interval"] = int(parts[1])
    else:
        mode, arg, hour = parts[1], parts[2], int(parts[3])
        dg = {"kind": {"d": "daily", "w": "weekly", "m": "monthly"}[mode], "hour": hour, "minute": 0}
        if mode == "w":
            dg["dow"] = int(arg)
        if mode == "m":
            dg["dom"] = int(arg)
        s["digest"] = dg
        s["digest_next"] = next_digest(dg)
        s["interval"] = {"daily": 180, "weekly": 360, "monthly": 720}[dg["kind"]]
    s["next_due"] = time.time() + int(s["interval"]) * 60 if s.get("next_due") else 0


def freq_text(s):
    if s.get("digest"):
        return digest_label(s["digest"])
    return f"🟢 кожні {INTERVALS.get(s.get('interval', 30), str(s.get('interval')) + ' хв')}"


@guard
async def cb_freq(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    _, sid, *parts = q.data.split(":")
    draft = ctx.user_data.get("new")
    in_dialog = bool(draft and draft.get("id") == sid)
    db: DB = ctx.bot_data["db"]
    target = draft if in_dialog else db.get(q.message.chat_id, sid)
    if not target:
        return None
    step = parts[0]
    if step == "menu":
        await q.edit_message_text(summary(target) + "\n\n⏱ <b>Обери частоту:</b>", parse_mode=ParseMode.HTML,
                                  reply_markup=freq_menu_kb(sid))
        return S_EDIT if in_dialog else None
    if step == "d":
        await q.edit_message_text("📅 Щодня — о котрій?", reply_markup=time_kb(sid, "d", 0))
        return None
    if step == "w" and len(parts) == 1:
        await q.edit_message_text("🗓 Щотижня — в який день?",
                                  reply_markup=kb([[(d, f"fq:{sid}:w:{i}") for i, d in enumerate(WEEKDAYS_FULL)]]))
        return None
    if step == "w":
        await q.edit_message_text(f"🗓 Щотижня, {WEEKDAYS_FULL[int(parts[1])]} — о котрій?",
                                  reply_markup=time_kb(sid, "w", parts[1]))
        return None
    if step == "m" and len(parts) == 1:
        await q.edit_message_text("📆 Раз на місяць — якого числа?",
                                  reply_markup=kb([[(str(d), f"fq:{sid}:m:{d}") for d in (1, 5, 10, 15, 20, 25)]]))
        return None
    if step == "m":
        await q.edit_message_text(f"📆 Щомісяця, {parts[1]}-го — о котрій?", reply_markup=time_kb(sid, "m", parts[1]))
        return None
    # фінальний вибір: i:<хв> або t:<mode>:<arg>:<год>
    if in_dialog:
        apply_freq(target, parts)
        if ctx.user_data.get("edit"):
            return await ask_edit_menu(q, ctx)
        return await finish(q, ctx)
    with db.lock:
        apply_freq(target, parts)
    db.save()
    await q.edit_message_text(summary(target) + f"\n\n✅ Частота: {freq_text(target)}", parse_mode=ParseMode.HTML,
                              reply_markup=search_kb(target))
    return None


# ═══════════════════════════════════════════════════════════════
# МОЇ ПОШУКИ
# ═══════════════════════════════════════════════════════════════
def search_kb(s):
    sid = s["id"]
    rows = [[("🔍 Перевірити зараз", f"chk:{sid}"), ("✏️ Змінити", f"edt:{sid}")]]
    rows.append([("▶️ Відновити", f"res:{sid}") if s.get("paused") else ("⏸ Пауза", f"pau:{sid}"),
                 ("⏱ Частота", f"fq:{sid}:menu")])
    rows.append([("🗑 Видалити", f"del:{sid}")])
    return kb(rows)


@guard
async def my_searches(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    db: DB = ctx.bot_data["db"]
    ss = db.searches(update.effective_chat.id)
    if not ss:
        await update.effective_message.reply_text(
            "Пошуків ще немає. Натисни ➕ Новий пошук.", reply_markup=MAIN_KB)
        return
    for s in ss:
        await update.effective_message.reply_text(summary(s), parse_mode=ParseMode.HTML, reply_markup=search_kb(s))


@guard
async def cb_manage(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    db: DB = ctx.bot_data["db"]
    cid = q.message.chat_id
    act, sid = q.data.split(":", 1)
    if act == "frqset":
        sid, minutes = sid.split(":", 1)
    s = db.get(cid, sid)
    if not s:
        await q.answer("Пошук не знайдено")
        return
    if act == "chk":
        await q.answer("Перевіряю…")
        await q.message.reply_text(f"⏳ Перевіряю «{s['name']}»…")
        asyncio.create_task(check_search(ctx.application, cid, sid, manual=True))
        return
    await q.answer()
    if act in ("pau", "res"):
        with db.lock:
            s["paused"] = act == "pau"
            if act == "res":
                s["next_due"] = 0
        db.save()
        await q.edit_message_text(summary(s), parse_mode=ParseMode.HTML, reply_markup=search_kb(s))
    elif act == "frq":
        rows = [[(t, f"frqset:{sid}:{m}") for m, t in INTERVALS.items()],
                [("🗓 Щотижня, сб 14:00", f"frqset:{sid}:w5"), ("🗓 Щодня, 14:00", f"frqset:{sid}:d")]]
        await q.edit_message_reply_markup(reply_markup=kb(rows))
    elif act == "frqset":
        with db.lock:
            if minutes in ("w5", "d"):
                s["digest"] = {"dow": 5 if minutes == "w5" else None, "hour": 14, "minute": 0}
                s["digest_next"] = next_digest(s["digest"])
                s["interval"] = 180                    # у фоні перевіряємо раз на 3 год, щоб нічого не пропустити
            else:
                s.pop("digest", None)
                s.pop("digest_next", None)
                s["interval"] = int(minutes)
            s["next_due"] = time.time() + int(s["interval"]) * 60
        db.save()
        await q.edit_message_text(summary(s), parse_mode=ParseMode.HTML, reply_markup=search_kb(s))
    elif act == "del":
        await q.edit_message_reply_markup(reply_markup=kb([[("✅ Так, видалити", f"delok:{sid}"),
                                                             ("❌ Ні", f"delno:{sid}")]]))
    elif act == "delok":
        db.delete(cid, sid)
        await q.edit_message_text(f"🗑 Пошук «{esc(s['name'])}» видалено", parse_mode=ParseMode.HTML)
    elif act == "delno":
        await q.edit_message_text(summary(s), parse_mode=ParseMode.HTML, reply_markup=search_kb(s))


# ── Звіт Excel ─────────────────────────────────────────────────
@guard
async def cmd_report(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    db: DB = ctx.bot_data["db"]
    cid = update.effective_chat.id
    searches = copy.deepcopy(db.searches(cid))
    if not searches:
        await update.effective_message.reply_text("Пошуків ще немає — нема з чого робити звіт.")
        return
    await update.effective_message.reply_text("⏳ Формую звіт…")
    stamp = datetime.now(KYIV).strftime("%Y-%m-%d_%H%M")
    out_dir = REPORTS_DIR if db.path.startswith(DATA_DIR) else os.path.dirname(db.path)
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, f"Звіт_авто_{stamp}.xlsx")
    try:
        stats = await asyncio.to_thread(build_report, ctx.bot_data["archive"], searches, path)
    except Exception:
        log.exception("Звіт не сформовано")
        await update.effective_message.reply_text("⚠️ Не вдалося сформувати звіт, деталі в data/bot.log")
        return
    if not stats:
        await update.effective_message.reply_text(
            "Архів поки порожній: оголошення почнуть накопичуватись з наступних перевірок "
            "(можна натиснути «🔍 Перевірити зараз» у пошуку).")
        return
    lines = [f"• {esc(k)}: {v}" for k, v in stats.items()]
    ctx.chat_data["last_report"] = path
    markup = kb([[("📂 Відкрити на комп'ютері", "openrep")]]) \
        if os.name == "nt" and is_owner(cid, db) else None
    with open(path, "rb") as f:
        await update.effective_message.reply_document(
            f, filename=os.path.basename(path),
            caption="📊 Звіт: аркуш «Аналітика» + окремий аркуш з посиланнями на кожен пошук.\n"
                    + "\n".join(lines) + f"\n\n💾 Збережено: <code>{esc(path)}</code>",
            parse_mode=ParseMode.HTML, reply_markup=markup)
    # тримаємо 30 останніх звітів
    old = sorted(f for f in os.listdir(out_dir) if f.startswith("Звіт_авто_"))[:-30]
    for f in old:
        try:
            os.remove(os.path.join(out_dir, f))
        except OSError:
            pass


@owner_only
async def cb_open_report(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    path = ctx.chat_data.get("last_report")
    if not path or not os.path.exists(path):
        await q.answer("Файл не знайдено — сформуй звіт ще раз", show_alert=True)
        return
    try:
        os.startfile(path)          # відкриває в Excel на цьому комп'ютері
        await q.answer("Відкриваю на комп'ютері")
    except Exception as e:
        await q.answer(f"Не вдалося відкрити: {e}"[:190], show_alert=True)


# ── Доступ для інших людей ─────────────────────────────────────
INVITE_TTL = 7 * 24 * 3600


def add_user(db: DB, update: Update, via: str, phone: str = ""):
    u = update.effective_user
    with db.lock:
        db.data.setdefault("users", {})[str(update.effective_chat.id)] = {
            "name": " ".join(x for x in [u.first_name, u.last_name] if x) or (u.username or "?"),
            "username": u.username or "", "phone": phone, "via": via,
            "added": datetime.now(KYIV).strftime("%d.%m.%Y %H:%M")}
    db.save()


async def notify_owner(ctx, text):
    owner = ctx.bot_data["db"].data.get("owner")
    if owner:
        try:
            await ctx.bot.send_message(owner, text, parse_mode=ParseMode.HTML)
        except TelegramError:
            pass


async def redeem_invite(update: Update, ctx: ContextTypes.DEFAULT_TYPE, code: str) -> bool:
    db: DB = ctx.bot_data["db"]
    inv = (db.data.get("invites") or {}).get(code)
    if not inv or inv.get("used_by") or time.time() - inv.get("ts", 0) > INVITE_TTL:
        await update.effective_message.reply_text("⚠️ Посилання-запрошення недійсне або вже використане. "
                                                  "Попроси власника бота надіслати нове.")
        return False
    with db.lock:
        inv["used_by"] = update.effective_chat.id
    add_user(db, update, via="запрошення")
    await notify_owner(ctx, f"👥 За запрошенням приєднався: <b>{esc(db.data['users'][str(update.effective_chat.id)]['name'])}</b>")
    return True


async def on_contact(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    db: DB = ctx.bot_data["db"]
    c = update.message.contact
    if not c or c.user_id != update.effective_user.id:
        await update.message.reply_text("Надішли, будь ласка, саме свій номер — кнопкою «📱 Поділитися номером».",
                                        reply_markup=CONTACT_KB)
        return
    if chat_allowed(update.effective_chat.id, db):
        await update.message.reply_text("У тебе вже є доступ 🙂", reply_markup=MAIN_KB)
        return
    phone = norm_phone(c.phone_number)
    allowed_phones = db.data.get("allowed_phones") or {}
    if phone not in allowed_phones:
        await update.message.reply_text("🔒 Для цього номера доступу немає. Попроси власника бота додати його.",
                                        reply_markup=CONTACT_KB)
        log.info("Відмова за номером …%s", phone[-4:])
        return
    add_user(db, update, via="номер", phone=phone)
    label = allowed_phones.get(phone) or ""
    await notify_owner(ctx, f"👥 За номером +{phone[:5]}…{phone[-2:]} приєднався: "
                            f"<b>{esc(db.data['users'][str(update.effective_chat.id)]['name'])}</b>"
                            + (f" ({esc(label)})" if label else ""))
    await welcome(update, ctx)


def access_kb():
    return kb([[("🔗 Створити запрошення", "acc:invite")],
               [("👥 Користувачі", "acc:users"), ("📞 Дозволені номери", "acc:phones")]])


@owner_only
async def cmd_access(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.effective_message.reply_text(
        "👥 <b>Доступ до бота</b>\n\n"
        "• <b>За номером:</b> напиши <code>/allow +380671234567 Ім'я</code> — людина відкриє бота, "
        "натисне «Поділитися номером» і отримає доступ.\n"
        "• <b>За посиланням:</b> «🔗 Створити запрошення» — одноразове посилання на 7 днів.\n\n"
        "У кожного свої пошуки й сповіщення. Працює, поки увімкнений цей комп'ютер.",
        parse_mode=ParseMode.HTML, reply_markup=access_kb())


@owner_only
async def cmd_allow(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    db: DB = ctx.bot_data["db"]
    if not ctx.args:
        await update.message.reply_text("Формат: /allow +380671234567 Ім'я")
        return
    # номер може бути з пробілами/дужками: «067 123 45 67», далі — ім'я
    num_parts, rest = [], list(ctx.args)
    while rest and re.fullmatch(r"[+\d()\-]+", rest[0]):
        num_parts.append(rest.pop(0))
    phone = norm_phone("".join(num_parts))
    if len(phone) < 11:
        await update.message.reply_text("⚠️ Не схоже на номер. Приклад: /allow +380671234567 Олена")
        return
    label = " ".join(rest)
    with db.lock:
        db.data.setdefault("allowed_phones", {})[phone] = label
    db.save()
    me = ctx.bot.username
    await update.message.reply_text(
        f"✅ Номер +{phone} додано{(' (' + esc(label) + ')') if label else ''}.\n"
        f"Надішли людині посилання на бота: https://t.me/{me} — хай натисне /start і «Поділитися номером».",
        parse_mode=ParseMode.HTML)


@owner_only
async def cb_access(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    db: DB = ctx.bot_data["db"]
    act = q.data.split(":", 1)[1]
    if act == "invite":
        code = uuid.uuid4().hex[:10]
        with db.lock:
            invs = db.data.setdefault("invites", {})
            for k in [k for k, v in invs.items() if time.time() - v.get("ts", 0) > INVITE_TTL]:
                del invs[k]
            invs[code] = {"ts": time.time(), "used_by": None}
        db.save()
        link = f"https://t.me/{ctx.bot.username}?start=inv_{code}"
        await q.message.reply_text(f"🔗 Одноразове запрошення (дійсне 7 днів):\n{link}\n\n"
                                   "Перешли його людині — хто перший перейде, той отримає доступ.",
                                   link_preview_options=NO_PREVIEW)
    elif act == "users":
        users = db.data.get("users") or {}
        if not users:
            await q.message.reply_text("Інших користувачів поки немає.")
            return
        for cid, u in users.items():
            n = len(db.chat(cid)["searches"]) if cid in db.data["chats"] else 0
            txt = (f"👤 <b>{esc(u['name'])}</b>" + (f" @{esc(u['username'])}" if u.get("username") else "")
                   + f"\nдоступ: {u['via']}, з {u['added']} · пошуків: {n}")
            await q.message.reply_text(txt, parse_mode=ParseMode.HTML,
                                       reply_markup=kb([[("🚫 Відключити", f"rev:{cid}")]]))
    elif act == "phones":
        ph = db.data.get("allowed_phones") or {}
        if not ph:
            await q.message.reply_text("Дозволених номерів немає. Додати: /allow +380671234567 Ім'я")
            return
        rows = [[(f"🗑 +{p}" + (f" {l}" if l else ""), f"rmph:{p}")] for p, l in ph.items()]
        await q.message.reply_text("📞 Дозволені номери (натисни, щоб прибрати):", reply_markup=kb(rows))


@owner_only
async def cb_revoke(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    db: DB = ctx.bot_data["db"]
    kind, val = q.data.split(":", 1)
    with db.lock:
        if kind == "rev":
            u = (db.data.get("users") or {}).pop(val, None)
            if u and u.get("phone"):
                (db.data.get("allowed_phones") or {}).pop(u["phone"], None)
        else:
            u = (db.data.get("allowed_phones") or {}).pop(val, None)
    db.save()
    await q.answer("Готово")
    if kind == "rev":
        await q.edit_message_text(f"🚫 Доступ відключено{(': ' + esc(u['name'])) if u else ''}. "
                                  "Його пошуки більше не перевіряються.", parse_mode=ParseMode.HTML)
        try:
            await ctx.bot.send_message(int(val), "🔒 Власник бота відключив тобі доступ.")
        except TelegramError:
            pass
    else:
        await q.edit_message_text(f"🗑 Номер +{val} прибрано зі списку дозволених.")


# ═══════════════════════════════════════════════════════════════
# КОМАНДИ
# ═══════════════════════════════════════════════════════════════
async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    db: DB = ctx.bot_data["db"]
    arg = (ctx.args or [""])[0] if getattr(ctx, "args", None) else ""
    if arg.startswith("inv_") and not allowed(update, db):
        if await redeem_invite(update, ctx, arg[4:]):
            return await welcome(update, ctx)
        return ConversationHandler.END
    return await _start_guarded(update, ctx)


@guard
async def _start_guarded(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    db: DB = ctx.bot_data["db"]
    if not ALLOWED and db.data.get("owner") is None:
        db.data["owner"] = update.effective_chat.id
        db.save()
        log.info("Власник бота: %s", update.effective_chat.id)
    return await welcome(update, ctx)


async def welcome(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    db: DB = ctx.bot_data["db"]
    owner_hint = "\n👥 /access — доступ для інших (номер телефону або посилання)" \
        if is_owner(update.effective_chat.id, db) else ""
    await update.effective_message.reply_text(
        "👋 <b>Пошук авто UA</b>\n\n"
        "Шукаю на AUTO.RIA і в Telegram-каналах-автобазарах і сам пишу про нові авто.\n\n"
        "➕ <b>Новий пошук</b> — марка/модель, рік, ціна, пробіг, пальне, КПП, кузов, регіон\n"
        "📋 <b>Мої пошуки</b> — перевірити, змінити, пауза, частота, видалення\n"
        "📊 <b>Звіт</b> — Excel з посиланнями й аналітикою" + owner_hint,
        parse_mode=ParseMode.HTML, reply_markup=MAIN_KB)
    return ConversationHandler.END


@guard
async def cmd_help(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.effective_message.reply_text(
        "🚗 <b>Як це працює</b>\n\n"
        "• Кожен пошук перевіряється за розкладом (за замовчуванням раз на 30 хв) або приходить дайджестом.\n"
        "• Перший раз показую до 10 найсвіжіших, далі — тільки нові.\n"
        "• Те саме авто з AUTO.RIA і з каналу приходить один раз (за VIN, посиланням або параметрами).\n"
        "• У Telegram-постах дані розпізнаю з тексту — якщо поле не вказане, авто не відсіюю.\n"
        "• Ринок — медіана тієї ж моделі й року (з урахуванням пального й КПП, коли даних досить).\n"
        "• Якщо ціна знизиться — напишу 📉. 📊 Звіт — Excel з посиланнями й аналітикою.\n\n"
        "Команди: /new — новий пошук, /list — мої пошуки, /report — звіт, /cancel — скасувати."
        + ("\nВласнику: /access — доступ для інших, /allow +380… Ім'я — дозволити номер."
           if is_owner(update.effective_chat.id, ctx.bot_data["db"]) else ""),
        parse_mode=ParseMode.HTML, reply_markup=MAIN_KB)


async def on_error(update, ctx: ContextTypes.DEFAULT_TYPE):
    log.error("Помилка обробника: %s", ctx.error, exc_info=ctx.error)


SCHEMA_V = 1


def migrate(db: DB, archive: Archive):
    """Разові зміни налаштувань з data/patch.json."""
    changed = False
    patch_path = os.path.join(os.path.dirname(db.path), "patch.json")
    if os.path.exists(patch_path):
        try:
            patch = json.load(open(patch_path, encoding="utf-8"))
            with db.lock:
                for chat in db.data["chats"].values():
                    for s in chat["searches"]:
                        for k, v in (patch.get("searches", {}).get(s["id"]) or {}).items():
                            s[k] = v
                            changed = True
                        if s.get("digest"):
                            s["digest_next"] = next_digest(s["digest"])
            os.replace(patch_path, patch_path + ".applied")
            log.info("Застосовано %s", patch_path)
        except Exception:
            log.exception("Не вдалося застосувати patch.json")
    if changed:
        db.save()


def build_app(token: str, db: DB = None, **builder_kw) -> Application:
    b = Application.builder().token(token)
    for k, v in builder_kw.items():
        b = getattr(b, k)(v)
    app = b.build()
    app.bot_data["db"] = db or DB()
    app.bot_data["archive"] = Archive(os.path.join(os.path.dirname(app.bot_data["db"].path), "cars.db"))
    app.bot_data["market"] = Market(os.path.join(os.path.dirname(app.bot_data["db"].path), "market.db"))
    migrate(app.bot_data["db"], app.bot_data["archive"])

    conv = ConversationHandler(
        entry_points=[MessageHandler(filters.Regex("^➕ Новий пошук$"), new_search),
                      CommandHandler("new", new_search),
                      CallbackQueryHandler(edit_search, pattern=r"^edt:")],
        states={
            S_CARS: [CallbackQueryHandler(cb_cars, pattern=r"^(cb|cm|ca):|^(cq|cs|cc|cx|cd)$"),
                     MessageHandler(filters.TEXT & ~filters.COMMAND, txt_cars)],
            S_YEAR: [CallbackQueryHandler(cb_range, pattern=r"^rg:"), MessageHandler(filters.TEXT & ~filters.COMMAND, txt_range)],
            S_PRICE: [CallbackQueryHandler(cb_range, pattern=r"^rg:"), MessageHandler(filters.TEXT & ~filters.COMMAND, txt_range)],
            S_MILEAGE: [CallbackQueryHandler(cb_range, pattern=r"^rg:"), MessageHandler(filters.TEXT & ~filters.COMMAND, txt_range)],
            S_ENGINE: [CallbackQueryHandler(cb_range, pattern=r"^rg:"), MessageHandler(filters.TEXT & ~filters.COMMAND, txt_range)],
            S_FUEL: [CallbackQueryHandler(cb_multi, pattern=r"^fu:")],
            S_BODY: [CallbackQueryHandler(cb_multi, pattern=r"^bo:")],
            S_DRIVE: [CallbackQueryHandler(cb_multi, pattern=r"^dr:")],
            S_REGION: [CallbackQueryHandler(cb_multi, pattern=r"^rg2:")],
            S_GEAR: [CallbackQueryHandler(cb_gear, pattern=r"^ge:")],
            S_COND: [CallbackQueryHandler(cb_cond, pattern=r"^co:")],
            S_SOURCES: [CallbackQueryHandler(cb_sources, pattern=r"^so:"),
                        MessageHandler(filters.TEXT & ~filters.COMMAND, txt_sources)],
            S_MARKET: [CallbackQueryHandler(cb_market, pattern=r"^mk:")],
            S_FREQ: [CallbackQueryHandler(cb_freq, pattern=r"^fq:")],
            S_KEYWORDS: [CommandHandler("skip", skip_keywords),
                         MessageHandler(filters.TEXT & ~filters.COMMAND, txt_keywords)],
            S_EDIT: [CallbackQueryHandler(cb_edit_menu, pattern=r"^ed:"),
                     CallbackQueryHandler(cb_freq, pattern=r"^fq:")],
        },
        fallbacks=[CommandHandler("cancel", cancel), CommandHandler("start", cmd_start),
                   MessageHandler(filters.Regex("^📋 Мої пошуки$"), cancel_and_list),
                   MessageHandler(filters.Regex("^📊 Звіт$"), cancel)],
        allow_reentry=True,
    )
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(conv)
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("list", my_searches))
    app.add_handler(MessageHandler(filters.Regex("^📋 Мої пошуки$"), my_searches))
    app.add_handler(MessageHandler(filters.Regex("^❓ Допомога$"), cmd_help))
    app.add_handler(CommandHandler("report", cmd_report))
    app.add_handler(CommandHandler("access", cmd_access))
    app.add_handler(CommandHandler("allow", cmd_allow))
    app.add_handler(CommandHandler("users", lambda u, c: cmd_access(u, c)))
    app.add_handler(MessageHandler(filters.CONTACT, on_contact))
    app.add_handler(CallbackQueryHandler(cb_access, pattern=r"^acc:"))
    app.add_handler(CallbackQueryHandler(cb_revoke, pattern=r"^(rev|rmph):"))
    app.add_handler(CallbackQueryHandler(cb_open_report, pattern=r"^openrep$"))
    app.add_handler(MessageHandler(filters.Regex("^📊 Звіт$"), cmd_report))
    app.add_handler(CallbackQueryHandler(cb_manage, pattern=r"^(chk|pau|res|frq|frqset|del|delok|delno):"))
    app.add_handler(CallbackQueryHandler(cb_freq, pattern=r"^fq:"))
    app.add_error_handler(on_error)
    app.job_queue.run_repeating(tick, interval=60, first=15, name="tick")
    return app


def main():
    setup_logging()
    if not BOT_TOKEN or "ВСТАВ" in BOT_TOKEN:
        print("❌ Немає токена. Впиши BOT_TOKEN=... у файл .env поруч із ботом і запусти ще раз.")
        raise SystemExit(2)      # код 2: start.bat не перезапускає бот у циклі
    import sys
    import telegram
    log.info("Авто-бот запущено. Python %s, python-telegram-bot %s. Дані: %s",
             sys.version.split()[0], telegram.__version__, DATA_DIR)
    # Python 3.14 сам не створює цикл подій — створюємо явно
    asyncio.set_event_loop(asyncio.new_event_loop())
    try:
        app = build_app(BOT_TOKEN)
        app.run_polling(drop_pending_updates=True)
    except Exception:
        log.exception("Бот впав")
        raise


if __name__ == "__main__":
    main()

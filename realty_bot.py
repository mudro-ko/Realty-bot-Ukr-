"""
Telegram-бот пошуку нерухомості UA v4.0
=======================================
Кнопки й діалоги, як у v3, але:
  • робочі джерела: ЛУН (у т.ч. оголошення з OLX і rieltor.ua) + DIM.RIA
  • пошуки й «бачені» оголошення зберігаються у файлі data/db.json (не губляться після перезапуску)
  • перевірка за розкладом усередині бота, нові оголошення приходять самі
  • ботом користується лише власник (той, хто першим натиснув /start)

Запуск на Windows: start.bat.  Вручну:  pip install -r requirements-bot.txt && python realty_bot.py
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

import yaml
from telegram import (InlineKeyboardButton, InlineKeyboardMarkup, KeyboardButton, LinkPreviewOptions,
                      ReplyKeyboardMarkup, Update)
from telegram.constants import ParseMode
from telegram.error import TelegramError
from telegram.ext import (Application, CallbackQueryHandler, CommandHandler, ContextTypes,
                          ConversationHandler, MessageHandler, filters)

from monitor.__main__ import run_search
from monitor.archive import Archive
from monitor.export import build_report
from monitor.filters import DISTRICTS, district_codes, split_keywords
from monitor.telegram import Telegram as Collector, esc

HERE = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(HERE, "data")
REPORTS_DIR = os.path.join(HERE, "Звіти")
os.makedirs(DATA_DIR, exist_ok=True)
KYIV = timezone(timedelta(hours=3))

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


# ── Довідники ──────────────────────────────────────────────────
DEALS = {"sale": "🏷 Купівля", "rent": "🔑 Оренда довгострокова", "daily": "📅 Подобово"}
DEAL_SHORT = {"sale": "продаж", "rent": "оренда", "daily": "подобово"}
CITIES = {"odesa": "Одеса", "mykolaiv": "Миколаїв", "kyiv": "Київ"}
DISTRICT_NAMES = {
    "lisky": "Ліски", "prymorskyi": "Приморський", "kyivskyi": "Київський",
    "khadzhybeiskyi": "Хаджибейський", "peresypskyi": "Пересипський", "tsentr": "Центр",
    "arkadiia": "Аркадія", "serednii-fontan": "Середній Фонтан", "malyi-fontan": "Малий Фонтан",
}
CONDITIONS = {
    "any": "🔄 Будь-який",
    "needs_repair": "🔨 Тільки під ремонт",
    "needs_repair_or_cosmetic": "🖌 Під ремонт або косметика",
    "renovated": "✨ Тільки з ремонтом",
}
SELLERS = {"any": "🔄 Будь-хто", "owner": "👤 Тільки власник / без комісії", "agency": "🏢 Тільки агентства"}
INTERVALS = {15: "15 хв", 30: "30 хв", 60: "1 год", 180: "3 год", 360: "6 год"}
PRICE_HINT = {
    "sale": "<code>30000-55000</code>, <code>від 40000</code>, <code>до 60к</code>",
    "rent": "<code>8000-14000</code>, <code>від 10000</code>, <code>до 15к</code>",
    "daily": "<code>800-1500</code>, <code>до 2000</code>",
}

(S_DEAL, S_CITY, S_DISTRICT, S_ROOMS, S_PRICE, S_CONDITION, S_SELLER, S_KEYWORDS, S_EDIT, S_AREA) = range(10)

MAIN_KB = ReplyKeyboardMarkup(
    [[KeyboardButton("📋 Мої пошуки"), KeyboardButton("➕ Новий пошук")],
     [KeyboardButton("📊 Звіт"), KeyboardButton("❓ Допомога")]],
    resize_keyboard=True, is_persistent=True)


def money(v, cur):
    sym = "$" if cur == "USD" else "₴"
    return f"{sym}{v:,}".replace(",", " ")


def kb(rows):
    return InlineKeyboardMarkup([[InlineKeyboardButton(t, callback_data=d) for t, d in row] for row in rows])


def chunks(items, n):
    return [items[i:i + n] for i in range(0, len(items), n)]


def currency_of(deal):
    return "USD" if deal == "sale" else "UAH"


def price_field(deal):
    return "price_usd" if deal == "sale" else "price_uah"


def parse_price_range(text: str):
    """«30000-55000», «від 40 000», «до 60к», «30к – 55к», «45000» (= до 45000) → (min, max)."""
    t = (text or "").lower().replace(" ", " ").replace(",", ".")
    t = re.sub(r"(\d)\s+(?=\d{3}\b)", r"\1", t)          # «40 000» → «40000»

    def num(x):
        m = re.match(r"\s*(\d+(?:\.\d+)?)\s*(к|k|тис|тыс)?", x)
        if not m:
            return None
        v = float(m.group(1))
        return int(v * 1000) if m.group(2) else int(v)

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


def rooms_label(rooms):
    if not rooms:
        return "будь-яка к-сть кімнат"
    r = sorted(x for x in rooms if x != 5)
    txt = ",".join("4+" if x == 4 and 5 in rooms else str(x) for x in r)
    return f"{txt}к"


def price_label(s):
    rng = s.get(price_field(s["deal"])) or {}
    cur = currency_of(s["deal"])
    lo, hi = rng.get("min"), rng.get("max")
    if lo and hi:
        return f"{money(lo, cur)}–{money(hi, cur)}"
    if lo:
        return f"від {money(lo, cur)}"
    if hi:
        return f"до {money(hi, cur)}"
    return ""


def area_label(s):
    a = s.get("area") or {}
    lo, hi = a.get("min"), a.get("max")
    if lo and hi:
        return f"{lo:g}–{hi:g} м²"
    if lo:
        return f"від {lo:g} м²"
    if hi:
        return f"до {hi:g} м²"
    return ""


def districts_label(s):
    return ", ".join(DISTRICT_NAMES.get(d, d) for d in district_codes(s))


def auto_name(s):
    parts = [CITIES.get(s["city"], s["city"])]
    if district_codes(s):
        parts.append(districts_label(s))
    parts.append(rooms_label(s.get("rooms")))
    parts.append(DEAL_SHORT[s["deal"]])
    if price_label(s):
        parts.append(price_label(s))
    if area_label(s):
        parts.append(area_label(s))
    return " · ".join(parts)


def keywords_label(s):
    inc, exc = split_keywords(s)
    out = []
    if inc:
        out.append("🔑 " + esc(", ".join(inc)))
    if exc:
        out.append("🚫 " + esc(", ".join(exc)))
    return out


def summary(s):
    lines = [f"📋 <b>{esc(s['name'])}</b>"]
    lines.append(f"{DEALS[s['deal']]} · 📍 {CITIES.get(s['city'], s['city'])}"
                 + (f": {districts_label(s)}" if district_codes(s) else ""))
    lines.append(f"🛏 {rooms_label(s.get('rooms'))}" + (f" · 💰 {price_label(s)}" if price_label(s) else "")
                 + (f" · 📐 {area_label(s)}" if area_label(s) else ""))
    if s.get("condition", "any") != "any":
        lines.append(f"🛠 {CONDITIONS[s['condition']]}")
    if (s.get("seller") or "any") != "any":
        lines.append(SELLERS[s["seller"]])
    lines += keywords_label(s)
    src = " + ".join({"lun": "ЛУН", "dimria": "DIM.RIA"}[x] for x in s.get("sources", []))
    status = "⏸ на паузі" if s.get("paused") else f"🟢 кожні {INTERVALS.get(s.get('interval', 30), str(s.get('interval')) + ' хв')}"
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
        outbox, report = [], {}
        collector = Collector("", "", outbox)       # без токена: повідомлення лише збираються
        t0 = time.time()
        try:
            await asyncio.to_thread(run_search, scfg, st, collector, SETTINGS, report, app.bot_data["archive"])
        except Exception:
            log.exception("Перевірка %s впала", sid)
            if manual:
                await app.bot.send_message(chat_id, "⚠️ Не вдалося перевірити, деталі в data/bot.log")
            return
        rep = report.get(sid, {})
        with db.lock:
            if db.get(chat_id, sid) is None:       # пошук видалили, поки перевіряли
                return
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


# ═══════════════════════════════════════════════════════════════
# ДІАЛОГ: СТВОРЕННЯ / РЕДАГУВАННЯ ПОШУКУ
# ═══════════════════════════════════════════════════════════════
async def show(src, text, markup=None):
    """src — CallbackQuery (редагуємо повідомлення) або Message (надсилаємо нове)."""
    if hasattr(src, "edit_message_text"):
        await src.edit_message_text(text, parse_mode=ParseMode.HTML, reply_markup=markup)
    else:
        await src.reply_text(text, parse_mode=ParseMode.HTML, reply_markup=markup)


async def after(src, ctx, next_step):
    """У режимі редагування після кожного кроку — назад у меню, інакше — наступний крок."""
    if ctx.user_data.get("edit"):
        return await ask_edit_menu(src, ctx)
    return await next_step(src, ctx)


@guard
async def new_search(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ctx.user_data["edit"] = False
    ctx.user_data["new"] = {"id": uuid.uuid4().hex[:8], "sources": ["lun", "dimria"],
                            "condition": "any", "seller": "any", "interval": 30}
    await update.effective_message.reply_text(
        "🏠 <b>Новий пошук</b>\n\nТип угоди:", parse_mode=ParseMode.HTML,
        reply_markup=kb([[(v, f"deal:{k}")] for k, v in DEALS.items()]))
    return S_DEAL


@guard
async def edit_search(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    db: DB = ctx.bot_data["db"]
    s = db.get(q.message.chat_id, q.data.split(":", 1)[1])
    if not s:
        await q.message.reply_text("Пошук не знайдено")
        return ConversationHandler.END
    e = copy.deepcopy(s)
    if "districts" not in e:
        e["districts"] = district_codes(e)
        e.pop("district", None)
    if "keywords" not in e:
        inc, exc = split_keywords(e)
        e["keywords"] = inc + [f"-{x}" for x in exc]
        e.pop("include_keywords", None)
        e.pop("exclude_keywords", None)
    e.setdefault("seller", "owner" if (e.get("owner_only") or e.get("no_commission")) else "any")
    e.pop("owner_only", None)
    e.pop("no_commission", None)
    ctx.user_data["edit"] = True
    ctx.user_data["new"] = e
    return await ask_edit_menu(q, ctx, new_message=True)


async def ask_edit_menu(src, ctx, new_message=False):
    s = ctx.user_data["new"]
    rows = []
    if DISTRICTS.get(s["city"]):
        rows.append([("📍 Райони", "ed:dist")])
    rows += [[("🛏 Кімнати", "ed:rooms"), ("💰 Ціна", "ed:price"), ("📐 Площа", "ed:area")],
             [("🛠 Стан", "ed:cond"), ("👤 Продавець", "ed:seller")],
             [("🔑 Ключові слова", "ed:kw")],
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
    if act == "dist":
        return await ask_district(q, ctx)
    if act == "rooms":
        return await ask_rooms(q, ctx)
    if act == "price":
        return await ask_price(q, ctx)
    if act == "area":
        return await ask_area(q, ctx)
    if act == "cond":
        return await ask_condition(q, ctx)
    if act == "seller":
        return await ask_seller(q, ctx)
    if act == "kw":
        return await ask_keywords(q, ctx)
    if act == "cancel":
        ctx.user_data.pop("new", None)
        await q.edit_message_text("Зміни скасовано.")
        return ConversationHandler.END
    return await finish(q, ctx)


async def cb_deal(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    s = ctx.user_data["new"]
    s["deal"] = q.data.split(":", 1)[1]
    if s["deal"] == "daily":
        s["sources"] = ["dimria"]          # на ЛУН подобової оренди немає
    await q.edit_message_text(f"{DEALS[s['deal']]}\n\n📍 Місто:",
                              reply_markup=kb([[(v, f"city:{k}") for k, v in CITIES.items()]]))
    return S_CITY


async def cb_city(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    s = ctx.user_data["new"]
    s["city"] = q.data.split(":", 1)[1]
    s["districts"] = []
    if not DISTRICTS.get(s["city"]):
        return await ask_rooms(q, ctx)
    return await ask_district(q, ctx)


def district_kb(s):
    sel = s.get("districts") or []
    ds = list(DISTRICTS.get(s["city"], {}).keys())
    rows = chunks([(("✅ " if d in sel else "") + DISTRICT_NAMES.get(d, d), f"dsel:{d}") for d in ds], 2)
    rows.append([("🔄 Усе місто", "dsel:all"), ("✔️ Готово", "dsel:done")])
    return kb(rows)


async def ask_district(src, ctx):
    s = ctx.user_data["new"]
    await show(src, f"📍 {CITIES[s['city']]} — райони (можна кілька), потім «Готово»:", district_kb(s))
    return S_DISTRICT


async def cb_district(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    s = ctx.user_data["new"]
    v = q.data.split(":", 1)[1]
    sel = s.setdefault("districts", [])
    if v == "all":
        s["districts"] = []
    elif v != "done":
        sel.remove(v) if v in sel else sel.append(v)
        await q.edit_message_reply_markup(reply_markup=district_kb(s))
        return S_DISTRICT
    s.pop("district", None)
    return await after(q, ctx, ask_rooms)


def rooms_kb(sel):
    opts = [(1, "1"), (2, "2"), (3, "3"), (4, "4+")]
    row = [(("✅ " if r in sel else "") + t, f"rooms:{r}") for r, t in opts]
    return kb([row, [("🔄 Будь-яка", "rooms:any"), ("✔️ Готово", "rooms:done")]])


async def ask_rooms(src, ctx):
    s = ctx.user_data["new"]
    s["rooms"] = [r for r in (s.get("rooms") or []) if r != 5]
    await show(src, "🛏 Кімнати (можна кілька), потім «Готово»:", rooms_kb(s["rooms"]))
    return S_ROOMS


async def cb_rooms(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    s = ctx.user_data["new"]
    v = q.data.split(":", 1)[1]
    if v == "any":
        s["rooms"] = []
    elif v != "done":
        r = int(v)
        s["rooms"].remove(r) if r in s["rooms"] else s["rooms"].append(r)
        await q.edit_message_reply_markup(reply_markup=rooms_kb(s["rooms"]))
        return S_ROOMS
    if 4 in s["rooms"]:
        s["rooms"] = s["rooms"] + [5]
    s["rooms"] = sorted(set(s["rooms"]))
    return await after(q, ctx, ask_price)


async def ask_price(src, ctx):
    s = ctx.user_data["new"]
    sym = "$" if currency_of(s["deal"]) == "USD" else "₴"
    cur = price_label(s)
    await show(src, f"💰 Ціна ({sym}). Напиши діапазон, наприклад: {PRICE_HINT[s['deal']]}"
                    + (f"\nЗараз: <b>{cur}</b>" if cur else ""),
               kb([[("Без обмеження", "price:none")]]))
    return S_PRICE


def set_price(s, lo, hi):
    s.pop("price_usd", None)
    s.pop("price_uah", None)
    if lo or hi:
        s[price_field(s["deal"])] = {"min": lo, "max": hi}


async def cb_price(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    set_price(ctx.user_data["new"], None, None)
    return await after(q, ctx, ask_area)


async def txt_price(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    lo, hi = parse_price_range(update.message.text)
    if not (lo or hi):
        await update.message.reply_text("⚠️ Не зрозумів. Напиши, наприклад, 30000-55000 або до 60к")
        return S_PRICE
    s = ctx.user_data["new"]
    set_price(s, lo, hi)
    await update.message.reply_text(f"💰 {price_label(s)}")
    return await after(update.message, ctx, ask_area)


async def ask_area(src, ctx):
    s = ctx.user_data["new"]
    cur = area_label(s)
    await show(src, "📐 Площа, м². Напиши діапазон: <code>40-50</code>, <code>від 60</code>, <code>до 45</code>"
                    + (f"\nЗараз: <b>{cur}</b>" if cur else ""),
               kb([[("Будь-яка", "area:none")]]))
    return S_AREA


async def cb_area(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    ctx.user_data["new"].pop("area", None)
    return await after(q, ctx, ask_condition)


async def txt_area(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    t = (update.message.text or "").lower().replace("м2", "").replace("м²", "").replace("кв", "")
    lo, hi = parse_price_range(t)
    if not (lo or hi) or (hi and hi > 2000):
        await update.message.reply_text("⚠️ Не зрозумів. Напиши, наприклад, 40-50 або від 60")
        return S_AREA
    s = ctx.user_data["new"]
    s["area"] = {"min": lo, "max": hi}
    await update.message.reply_text(f"📐 {area_label(s)}")
    return await after(update.message, ctx, ask_condition)


async def ask_condition(src, ctx):
    s = ctx.user_data["new"]
    rows = [[(("✅ " if s.get("condition", "any") == k else "") + v, f"cond:{k}")] for k, v in CONDITIONS.items()]
    await show(src, "🛠 Стан ремонту:", kb(rows))
    return S_CONDITION


async def cb_condition(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    ctx.user_data["new"]["condition"] = q.data.split(":", 1)[1]
    return await after(q, ctx, ask_seller)


async def ask_seller(src, ctx):
    s = ctx.user_data["new"]
    rows = [[(("✅ " if (s.get("seller") or "any") == k else "") + v, f"sel:{k}")] for k, v in SELLERS.items()]
    await show(src, "👤 Хто продає / здає?\n<i>Власника визначаю за позначками сайтів і текстом "
                    "(«від власника», «без комісії»). Оголошення, де це неясно, при фільтрі не пройдуть.</i>",
               kb(rows))
    return S_SELLER


async def cb_seller(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    ctx.user_data["new"]["seller"] = q.data.split(":", 1)[1]
    return await after(q, ctx, ask_keywords)


async def ask_keywords(src, ctx):
    s = ctx.user_data["new"]
    cur = ", ".join(s.get("keywords") or [])
    await show(src,
               "🔑 <b>Ключові слова</b> через кому — надсилатиму лише оголошення, де є хоч одне з них.\n"
               "Щоб <b>виключити</b> слово, постав мінус.\n\n"
               "<i>Напр.: сіті центр, вид на море, -гуртожиток, -перший поверх</i>\n"
               + (f"\nЗараз: <code>{esc(cur)}</code>\n" if cur else "")
               + "\n/skip — без ключових слів")
    return S_KEYWORDS


async def txt_keywords(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    s = ctx.user_data["new"]
    s["keywords"] = [w.strip() for w in update.message.text.split(",") if w.strip()]
    s.pop("include_keywords", None)
    s.pop("exclude_keywords", None)
    return await after(update.message, ctx, finish)


async def skip_keywords(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    s = ctx.user_data["new"]
    s["keywords"] = []
    s.pop("include_keywords", None)
    s.pop("exclude_keywords", None)
    return await after(update.message, ctx, finish)


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
            old = next((x for x in lst if x["id"] == s["id"]), None)
            for i, x in enumerate(lst):
                if x["id"] == s["id"]:
                    lst[i] = s
                    break
            else:
                lst.append(s)
            # нові фільтри → переглядаємо все заново; вже надіслане не повторюємо
            st = db.chat(chat_id)["state"].get(s["id"], {})
            keep = {"sent": st.get("sent", {})}
            if old and district_codes(old) == district_codes(s) and old.get("city") == s.get("city"):
                keep["learned_micro"] = st.get("learned_micro", [])
                keep["ria_learned"] = st.get("ria_learned", {})
            db.chat(chat_id)["state"][s["id"]] = keep
        else:
            lst.append(s)
    db.save()
    if editing:
        try:
            ctx.bot_data["archive"].clear(s["id"])
        except Exception:
            log.exception("Не вдалося очистити архів %s", s["id"])
    tail = ("✅ Оновлено. Переглядаю все заново з новими фільтрами (уже надіслане не повторюватиму)."
            if editing else "✅ Збережено. Зараз покажу найновіші, далі — лише нові.")
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


# ═══════════════════════════════════════════════════════════════
# МОЇ ПОШУКИ
# ═══════════════════════════════════════════════════════════════
def search_kb(s):
    sid = s["id"]
    rows = [[("🔍 Перевірити зараз", f"chk:{sid}"), ("✏️ Змінити", f"edt:{sid}")]]
    rows.append([("▶️ Відновити", f"res:{sid}") if s.get("paused") else ("⏸ Пауза", f"pau:{sid}"),
                 ("⏱ Частота", f"frq:{sid}")])
    rows.append([("🗑 Видалити", f"del:{sid}")])
    return kb(rows)


@guard
async def my_searches(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    db: DB = ctx.bot_data["db"]
    ss = db.searches(update.effective_chat.id)
    if not ss:
        await update.effective_message.reply_text(
            "Пошуків ще немає. Натисни ➕ Новий пошук або /presets — готові пошуки.", reply_markup=MAIN_KB)
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
        sid, minutes = sid.split(":")
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
        rows = [[(t, f"frqset:{sid}:{m}") for m, t in INTERVALS.items()]]
        await q.edit_message_reply_markup(reply_markup=kb(rows))
    elif act == "frqset":
        with db.lock:
            s["interval"] = int(minutes)
            s["next_due"] = time.time() + int(minutes) * 60
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


# ── Готові пошуки з searches.yaml ──────────────────────────────
def load_presets():
    path = os.path.join(HERE, "searches.yaml")
    if not os.path.exists(path):
        return []
    cfg = yaml.safe_load(open(path, encoding="utf-8")) or {}
    return cfg.get("searches") or []


@guard
async def presets(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ps = load_presets()
    if not ps:
        await update.effective_message.reply_text("Готових пошуків немає (файл searches.yaml порожній).")
        return
    rows = [[(p["name"], f"pre:{p['id']}")] for p in ps]
    await update.effective_message.reply_text("Готові пошуки з searches.yaml — натисни, щоб додати:",
                                              reply_markup=kb(rows))


@guard
async def cb_preset(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    db: DB = ctx.bot_data["db"]
    pid = q.data.split(":", 1)[1]
    p = next((x for x in load_presets() if x["id"] == pid), None)
    if not p:
        return
    s = {k: v for k, v in copy.deepcopy(p).items() if k != "enabled"}
    s["id"] = f"{pid}-{uuid.uuid4().hex[:4]}"
    s.setdefault("sources", ["lun", "dimria"])
    s.setdefault("interval", 30)
    s["next_due"] = 0
    s["v"] = SCHEMA_V
    with db.lock:
        db.searches(q.message.chat_id).append(s)
    db.save()
    await q.message.reply_text(summary(s) + "\n\n✅ Додано. Зараз покажу найновіші.", parse_mode=ParseMode.HTML,
                               reply_markup=MAIN_KB)
    asyncio.create_task(check_search(ctx.application, q.message.chat_id, s["id"], manual=True))


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
    path = os.path.join(out_dir, f"Звіт_нерухомість_{stamp}.xlsx")
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
    old = sorted(f for f in os.listdir(out_dir) if f.startswith("Звіт_нерухомість_"))[:-30]
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
        "👋 <b>Пошук нерухомості UA v4</b>\n\n"
        "Шукаю на ЛУН (там і оголошення з OLX, rieltor.ua) та DIM.RIA і сам пишу про нові.\n\n"
        "➕ <b>Новий пошук</b> — створити\n"
        "📋 <b>Мої пошуки</b> — перевірити, пауза, частота, видалення\n"
        "📊 <b>Звіт</b> — Excel з посиланнями й аналітикою\n"
        "/presets — готові пошуки (напр. Миколаїв · Ліски)" + owner_hint,
        parse_mode=ParseMode.HTML, reply_markup=MAIN_KB)
    if not db.searches(update.effective_chat.id) and load_presets():
        await presets(update, ctx)
    return ConversationHandler.END


@guard
async def cmd_help(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.effective_message.reply_text(
        "🏠 <b>Як це працює</b>\n\n"
        "• Кожен пошук перевіряється за розкладом (за замовчуванням раз на 30 хв).\n"
        "• Перший раз показую до 10 найновіших, далі — тільки нові.\n"
        "• Той самий об'єкт з різних сайтів приходить один раз.\n"
        "• Стан ремонту визначаю за позначками сайтів і текстом оголошення — це підказка, не гарантія.\n\n"
        "• Усі оголошення, що пройшли фільтри, зберігаються в архів; якщо ціна знизиться — напишу 📉.\n"
        "• 📊 Звіт — Excel з посиланнями й аналітикою (медіани, $/м², динаміка, топ дешевих).\n\n"
        "Команди: /new — новий пошук, /list — мої пошуки, /report — звіт, /presets — готові, /cancel — скасувати."
        + ("\nВласнику: /access — доступ для інших, /allow +380… Ім'я — дозволити номер."
           if is_owner(update.effective_chat.id, ctx.bot_data["db"]) else ""),
        parse_mode=ParseMode.HTML, reply_markup=MAIN_KB)


async def on_error(update, ctx: ContextTypes.DEFAULT_TYPE):
    log.error("Помилка обробника: %s", ctx.error, exc_info=ctx.error)


SCHEMA_V = 2


def migrate(db: DB, archive: Archive):
    """Пошуки, створені старою версією, тихо переглядаємо заново з новою логікою."""
    changed = False
    with db.lock:
        for cid, chat in db.data["chats"].items():
            for s in chat["searches"]:
                if s.get("v", 1) >= SCHEMA_V:
                    continue
                st = chat["state"].get(s["id"], {})
                chat["state"][s["id"]] = {"sent": st.get("sent", {}), "quiet_initial": True,
                                          "learned_micro": st.get("learned_micro", []),
                                          "ria_learned": st.get("ria_learned", {})}
                try:
                    archive.clear(s["id"])
                except Exception:
                    pass
                s["v"], s["next_due"], changed = SCHEMA_V, 0, True
                log.info("Міграція пошуку %s на v%d", s["id"], SCHEMA_V)
    if changed:
        db.save()


def build_app(token: str, db: DB = None, **builder_kw) -> Application:
    b = Application.builder().token(token)
    for k, v in builder_kw.items():
        b = getattr(b, k)(v)
    app = b.build()
    app.bot_data["db"] = db or DB()
    app.bot_data["archive"] = Archive(os.path.join(os.path.dirname(app.bot_data["db"].path), "listings.db"))
    migrate(app.bot_data["db"], app.bot_data["archive"])

    conv = ConversationHandler(
        entry_points=[MessageHandler(filters.Regex("^➕ Новий пошук$"), new_search),
                      CommandHandler("new", new_search),
                      CallbackQueryHandler(edit_search, pattern=r"^edt:")],
        states={
            S_DEAL: [CallbackQueryHandler(cb_deal, pattern=r"^deal:")],
            S_CITY: [CallbackQueryHandler(cb_city, pattern=r"^city:")],
            S_DISTRICT: [CallbackQueryHandler(cb_district, pattern=r"^dsel:")],
            S_ROOMS: [CallbackQueryHandler(cb_rooms, pattern=r"^rooms:")],
            S_PRICE: [CallbackQueryHandler(cb_price, pattern=r"^price:"),
                      MessageHandler(filters.TEXT & ~filters.COMMAND, txt_price)],
            S_AREA: [CallbackQueryHandler(cb_area, pattern=r"^area:"),
                     MessageHandler(filters.TEXT & ~filters.COMMAND, txt_area)],
            S_CONDITION: [CallbackQueryHandler(cb_condition, pattern=r"^cond:")],
            S_SELLER: [CallbackQueryHandler(cb_seller, pattern=r"^sel:")],
            S_KEYWORDS: [CommandHandler("skip", skip_keywords),
                         MessageHandler(filters.TEXT & ~filters.COMMAND, txt_keywords)],
            S_EDIT: [CallbackQueryHandler(cb_edit_menu, pattern=r"^ed:")],
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
    app.add_handler(CommandHandler("presets", presets))
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
    app.add_handler(CallbackQueryHandler(cb_preset, pattern=r"^pre:"))
    app.add_handler(CallbackQueryHandler(cb_manage, pattern=r"^(chk|pau|res|frq|frqset|del|delok|delno):"))
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
    log.info("Бот v4 запущено. Python %s, python-telegram-bot %s. Дані: %s",
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

"""
Telegram-бот пошуку нерухомості UA v3.0
========================================
pip install python-telegram-bot requests beautifulsoup4 lxml
BOT_TOKEN=ВАШ_ТОКЕН python realty_bot.py
"""

import os, re, time, logging, asyncio, uuid
from dataclasses import dataclass, field
from typing import Optional
import requests
from bs4 import BeautifulSoup
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, KeyboardButton
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler,
    MessageHandler, ContextTypes, filters, ConversationHandler
)

logging.basicConfig(format="%(asctime)s %(levelname)s: %(message)s", level=logging.INFO)
log = logging.getLogger(__name__)
BOT_TOKEN = os.environ.get("BOT_TOKEN", "ВСТАВЬТЕ_ТОКЕН_СЮДА")

# ── Стани діалогу ─────────────────────────────────────────────
(S_DEAL, S_PROP, S_CITY, S_DISTRICT, S_ROOMS, S_FLOORS,
 S_CURRENCY, S_PRICE_FROM, S_PRICE_TO, S_AREA_FROM, S_AREA_TO,
 S_CONDITION, S_KEYWORDS, S_NAME, S_MONITOR, S_MONITOR_FREQ,
 S_EDIT_CHOOSE, S_EDIT_FIELD, S_RENAME) = range(19)

# ── Довідники ──────────────────────────────────────────────────
DEAL_TYPES = {"🏷 Купівля": "buy", "🔑 Оренда довгострокова": "rent", "📅 Подобова оренда": "daily"}
PROP_TYPES = {"🏢 Квартира": "flat", "🏡 Будинок": "house"}

CITIES = {
    "Київ":   {"olx": "kiev",    "domria": 1},
    "Одеса":  {"olx": "odesa",   "domria": 12},
    "Львів":  {"olx": "lviv",    "domria": 14},
    "Харків": {"olx": "kharkiv", "domria": 10},
    "Дніпро": {"olx": "dnipro",  "domria": 4},
}

DISTRICTS = {
    "Київ":   ["Печерськ","Шевченківський","Оболонь","Подол","Позняки","Осокорки","Троєщина","Голосіїв","Будь-який"],
    "Одеса":  ["Приморський","Малиновський","Київський","Суворовський","Центр","Аркадія","Таїрово","Будь-який"],
    "Львів":  ["Центр","Сихів","Франківський","Залізничний","Личаківський","Шевченківський","Будь-який"],
    "Харків": ["Центр","Салтівка","ХТЗ","Олексіївка","Холодна Гора","Немишля","Будь-який"],
    "Дніпро": ["Центр","Амур-Нижньодніпровський","Індустріальний","Самарський","Соборний","Будь-який"],
}

CONDITIONS_BUY   = {"🏗 Без ремонту":"без ремонту","🖌 Косметичний":"косметичний ремонт",
                    "✨ Авторський":"авторський ремонт","🏢 Від забудовника":"від забудовника","🔄 Будь-який":""}
CONDITIONS_RENT  = {"🏗 Без ремонту":"без ремонту","🖌 Косметичний":"косметичний ремонт",
                    "✨ Авторський":"авторський ремонт","🔄 Будь-який":""}

PRICE_OPTS = {
    "buy":   {"USD":["10 000","30 000","50 000","80 000","100 000","150 000","200 000","Без обмеження"],
              "UAH":["500 000","1 000 000","2 000 000","3 000 000","5 000 000","8 000 000","Без обмеження"]},
    "rent":  {"UAH":["3 000","5 000","8 000","12 000","15 000","20 000","30 000","Без обмеження"],
              "USD":["100","200","400","600","800","1 000","1 500","Без обмеження"]},
    "daily": {"UAH":["500","800","1 200","2 000","3 000","5 000","Без обмеження"],
              "USD":["20","40","60","100","150","200","Без обмеження"]},
}

HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
MAX_SEARCHES = 3
FREQ_MAP = {"30 хв":30,"1 год":60,"2 год":120,"6 год":360,"12 год":720,"24 год":1440}


# ── Моделі ────────────────────────────────────────────────────
@dataclass
class SearchParams:
    id: str = ""
    name: str = ""
    deal: str = "buy"        # buy | rent | daily
    prop: str = "flat"       # flat | house
    city: str = ""
    district: str = ""
    rooms: str = ""
    floors: str = ""         # тільки для будинку
    currency: str = "USD"
    price_from: int = 0
    price_to: int = 0
    area_from: int = 0
    area_to: int = 0
    conditions: list = field(default_factory=list)
    keywords: list = field(default_factory=list)
    monitor: bool = False
    monitor_freq: int = 60
    paused: bool = False
    seen_ids: list = field(default_factory=list)

    def auto_name(self) -> str:
        symbol = "$" if self.currency == "USD" else "₴"
        deal_emoji = {"buy":"🏷","rent":"🔑","daily":"📅"}.get(self.deal,"")
        prop_emoji = "🏢" if self.prop == "flat" else "🏡"
        price = f" до {symbol}{self.price_to:,}".replace(",","") if self.price_to else ""
        return f"{deal_emoji}{prop_emoji} {self.city} {self.rooms}к{price}"

    def summary(self) -> str:
        symbol = "$" if self.currency == "USD" else "₴"
        deal_name = {"buy":"Купівля","rent":"Оренда","daily":"Подобова"}.get(self.deal,"")
        prop_name = "Квартира" if self.prop == "flat" else "Будинок"
        pf = f"{symbol}{self.price_from:,}".replace(",","") if self.price_from else "—"
        pt = f"{symbol}{self.price_to:,}".replace(",","") if self.price_to else "—"
        af = f"{self.area_from} м²" if self.area_from else "—"
        at = f"{self.area_to} м²"   if self.area_to   else "—"
        lines = [
            f"📋 *{self.name}*",
            f"🏷 {deal_name} | {prop_name}",
            f"📍 {self.city}" + (f", {self.district}" if self.district else ""),
            f"🛏 {self.rooms} кімн." + (f" | 🏠 {self.floors} пов." if self.floors and self.floors != "Будь-яка" else ""),
            f"💰 {pf} – {pt}",
            f"📐 {af} – {at}",
        ]
        if self.conditions:
            lines.append(f"🛠 {', '.join(self.conditions)}")
        if self.keywords:
            lines.append(f"🔑 {', '.join(self.keywords)}")
        if self.monitor:
            freq_str = next((k for k,v in FREQ_MAP.items() if v==self.monitor_freq), f"{self.monitor_freq}хв")
            status = "⏸ пауза" if self.paused else "🟢 активний"
            lines.append(f"🔔 Моніторинг кожні {freq_str} — {status}")
        return "\n".join(lines)


@dataclass
class Listing:
    source: str
    title: str
    price: str
    price_num: int
    area: str
    rooms: str
    floor: str
    address: str
    description: str
    url: str
    photo_url: str = ""


# ── Сховище пошуків ────────────────────────────────────────────
user_searches: dict[int, list[SearchParams]] = {}  # chat_id -> [SearchParams]

def get_searches(chat_id: int) -> list[SearchParams]:
    return user_searches.setdefault(chat_id, [])

def save_search(chat_id: int, sp: SearchParams):
    searches = get_searches(chat_id)
    for i, s in enumerate(searches):
        if s.id == sp.id:
            searches[i] = sp
            return
    searches.append(sp)

def delete_search(chat_id: int, search_id: str):
    user_searches[chat_id] = [s for s in get_searches(chat_id) if s.id != search_id]

def get_search(chat_id: int, search_id: str) -> Optional[SearchParams]:
    return next((s for s in get_searches(chat_id) if s.id == search_id), None)


# ── Парсери ────────────────────────────────────────────────────
def parse_domria(sp: SearchParams, pages: int = 3) -> list:
    results = []
    city_id = CITIES[sp.city]["domria"]
    room_map = {"1":1,"2":2,"3":3,"4+":4,"Будь-яка":0}
    room_num = room_map.get(sp.rooms, 0)

    # Тип операції
    op_map = {"buy":1,"rent":3,"daily":4}
    op = op_map.get(sp.deal, 1)
    # Тип нерухомості
    cat = 1 if sp.prop == "flat" else 4

    for page in range(0, pages):
        p = {"category": cat, "operation_type": op, "state_id": city_id,
             "count": 20, "page": page, "lang_id": 4}
        if room_num > 0:
            p["rooms_count[]"] = room_num
        if sp.price_from > 0:
            p["price_from"] = sp.price_from if sp.currency == "USD" else sp.price_from // 40
        if sp.price_to > 0:
            p["price_to"] = sp.price_to if sp.currency == "USD" else sp.price_to // 40
        if sp.area_from > 0:
            p["square_from"] = sp.area_from
        if sp.area_to > 0:
            p["square_to"] = sp.area_to

        try:
            r = requests.get("https://developers.dom.ria.com/api/realty/search",
                             params=p, headers=HEADERS, timeout=15)
            data = r.json()
        except Exception as e:
            log.warning(f"DOM.RIA: {e}"); break

        items = data.get("items", [])
        if not items:
            break

        for item in items:
            try:
                price_usd = int(item.get("priceArr",{}).get("1",{}).get("$") or item.get("price") or 0)
                price_uah = price_usd * 40
                if sp.currency == "USD":
                    price_str = f"${price_usd:,}".replace(",", " ")
                    price_num = price_usd
                else:
                    price_str = f"₴{price_uah:,}".replace(",", " ")
                    price_num = price_uah

                area = item.get("generalSquare", "")
                floor_str = f"{item.get('floor','')}/{item.get('floorsCount','')}" if item.get("floor") else "—"
                addr = ", ".join(filter(None,[item.get("cityNameUk",""),item.get("districtNameUk",""),item.get("streetNameUk","")]))
                desc = (item.get("description_uk") or item.get("description") or "")[:200]
                url = item.get("beautiful_url") or f"https://dom.ria.com/uk/realty/{item.get('realty_id','')}.html"

                photos = item.get("photos", {})
                photo_url = ""
                if photos:
                    fk = next(iter(photos), None)
                    if fk:
                        photo_url = f"https://cdn.riastatic.com/photosnew/dom/photo/{photos[fk]}fl.jpg"

                # Фільтри
                if sp.keywords and not any(kw.lower() in (desc+addr).lower() for kw in sp.keywords):
                    continue
                if sp.district and sp.district != "Будь-який" and sp.district.lower() not in addr.lower():
                    continue
                if sp.conditions and "" not in sp.conditions:
                    if not any(c.lower() in desc.lower() for c in sp.conditions if c):
                        continue
                if sp.floors and sp.floors != "Будь-яка":
                    floors_count = str(item.get("floorsCount",""))
                    if floors_count != sp.floors:
                        continue

                results.append(Listing("DOM.RIA",
                    f"{sp.rooms} кімн., {area} м²" if area else desc[:60],
                    price_str, price_num, f"{area} м²" if area else "—",
                    sp.rooms, floor_str, addr, desc, url, photo_url))
            except Exception as e:
                log.debug(f"item: {e}")
        time.sleep(0.8)
    return results


def parse_olx(sp: SearchParams, pages: int = 2) -> list:
    results = []
    city_olx = CITIES[sp.city]["olx"]
    room_map = {"1":"1","2":"2","3":"3","4+":"4","Будь-яка":""}
    room_param = room_map.get(sp.rooms, "")

    # URL в залежності від типу угоди та нерухомості
    if sp.prop == "flat":
        if sp.deal == "buy":
            base = f"https://www.olx.ua/uk/nedvizhimost/kvartiry/{city_olx}/prodazha/"
        elif sp.deal == "rent":
            base = f"https://www.olx.ua/uk/nedvizhimost/kvartiry-komnaty/{city_olx}/"
        else:
            base = f"https://www.olx.ua/uk/nedvizhimost/posutochno-pochasovo/{city_olx}/"
    else:
        if sp.deal == "buy":
            base = f"https://www.olx.ua/uk/nedvizhimost/doma/{city_olx}/prodazha/"
        else:
            base = f"https://www.olx.ua/uk/nedvizhimost/doma/{city_olx}/"

    for page in range(1, pages + 1):
        url = base + (f"?rooms={room_param}&page={page}" if room_param else f"?page={page}")
        try:
            r = requests.get(url, headers=HEADERS, timeout=15)
            r.raise_for_status()
        except Exception as e:
            log.warning(f"OLX: {e}"); break

        soup = BeautifulSoup(r.text, "lxml")
        for card in soup.select("div[data-cy='l-card']"):
            try:
                title_el = card.select_one("h4,h6,[data-testid='ad-title']")
                price_el = card.select_one("[data-testid='ad-price']")
                loc_el   = card.select_one("[data-testid='location-date']")
                link_el  = card.select_one("a[href]")
                img_el   = card.select_one("img[src]")

                title   = title_el.get_text(strip=True) if title_el else "—"
                price_s = price_el.get_text(strip=True) if price_el else "—"
                address = loc_el.get_text(strip=True)   if loc_el   else "—"
                href    = link_el["href"]                if link_el  else ""
                if href and not href.startswith("http"):
                    href = "https://www.olx.ua" + href
                photo = img_el.get("src","") if img_el else ""

                nums = re.findall(r"\d+", price_s.replace(" ","").replace("\xa0",""))
                price_num = int("".join(nums[:5])) if nums else 0

                if sp.keywords and not any(kw.lower() in (title+address).lower() for kw in sp.keywords):
                    continue
                if sp.district and sp.district != "Будь-який" and sp.district.lower() not in address.lower():
                    continue

                area_m = re.search(r"(\d{2,4}(?:[.,]\d+)?)\s*м²", title)
                results.append(Listing("OLX", title, price_s, price_num,
                    area_m.group(0) if area_m else "—",
                    sp.rooms, "—", address, "", href, photo))
            except Exception:
                pass
        time.sleep(1.2)
    return results


def run_search(sp: SearchParams) -> list:
    all_listings = parse_domria(sp, pages=3) + parse_olx(sp, pages=2)
    seen, unique = set(), []
    for l in all_listings:
        if l.url not in seen:
            seen.add(l.url)
            unique.append(l)
    unique.sort(key=lambda x: x.price_num)
    return unique


# ── Відправка оголошення ───────────────────────────────────────
async def send_listing(context, chat_id: int, l: Listing):
    text = (
        f"🏠 *{l.title}*\n"
        f"💰 {l.price}\n"
        f"📐 {l.area}" + (f"  |  🏢 {l.floor} пов." if l.floor and l.floor != "—" else "") + "\n"
        f"📍 {l.address}\n"
        + (f"📝 _{l.description[:150]}..._\n" if l.description else "")
    )
    kb = InlineKeyboardMarkup([[InlineKeyboardButton("👁 Переглянути оголошення", url=l.url)]])
    try:
        if l.photo_url and l.photo_url.startswith("http"):
            await context.bot.send_photo(chat_id=chat_id, photo=l.photo_url,
                caption=text, parse_mode="Markdown", reply_markup=kb)
        else:
            await context.bot.send_message(chat_id=chat_id, text=text,
                parse_mode="Markdown", reply_markup=kb, disable_web_page_preview=False)
    except Exception:
        try:
            await context.bot.send_message(chat_id=chat_id, text=text,
                parse_mode="Markdown", reply_markup=kb, disable_web_page_preview=False)
        except Exception as e:
            log.warning(f"send: {e}")


# ── Моніторинг ─────────────────────────────────────────────────
async def monitor_job(context: ContextTypes.DEFAULT_TYPE):
    data = context.job.data
    chat_id = data["chat_id"]
    search_id = data["search_id"]
    sp = get_search(chat_id, search_id)
    if not sp or sp.paused or not sp.monitor:
        return
    loop = asyncio.get_event_loop()
    listings = await loop.run_in_executor(None, lambda: run_search(sp))
    new_ones = [l for l in listings if l.url not in sp.seen_ids]
    if new_ones:
        await context.bot.send_message(chat_id=chat_id,
            text=f"🔔 *{sp.name}* — знайдено {len(new_ones)} нових оголошень!",
            parse_mode="Markdown")
        for l in new_ones[:10]:
            await send_listing(context, chat_id, l)
            sp.seen_ids.append(l.url)
            await asyncio.sleep(0.5)


def start_monitor_job(app, chat_id: int, sp: SearchParams):
    # Видаляємо старі джоби для цього пошуку
    jobs = app.job_queue.get_jobs_by_name(f"mon_{chat_id}_{sp.id}")
    for j in jobs:
        j.schedule_removal()
    if sp.monitor and not sp.paused:
        app.job_queue.run_repeating(
            monitor_job,
            interval=sp.monitor_freq * 60,
            data={"chat_id": chat_id, "search_id": sp.id},
            name=f"mon_{chat_id}_{sp.id}",
        )


# ── Головне меню (постійна клавіатура) ────────────────────────
MAIN_KB = ReplyKeyboardMarkup(
    [[KeyboardButton("📋 Мої пошуки"), KeyboardButton("➕ Новий пошук")],
     [KeyboardButton("❓ Допомога")]],
    resize_keyboard=True, is_persistent=True
)


# ── Допоміжні функції ──────────────────────────────────────────
def make_kb(items, cols=2, prefix="") -> InlineKeyboardMarkup:
    rows, row = [], []
    for item in items:
        row.append(InlineKeyboardButton(item, callback_data=f"{prefix}{item}"))
        if len(row) == cols:
            rows.append(row); row = []
    if row:
        rows.append(row)
    return InlineKeyboardMarkup(rows)

def new_sp(ctx) -> SearchParams:
    sp = SearchParams(id=str(uuid.uuid4())[:8])
    ctx.user_data["sp"] = sp
    return sp

def cur_sp(ctx) -> SearchParams:
    return ctx.user_data.get("sp", SearchParams())


# ═══════════════════════════════════════════════════════════════
# ДІАЛОГ СТВОРЕННЯ ПОШУКУ
# ═══════════════════════════════════════════════════════════════

async def start_new_search(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    searches = get_searches(chat_id)
    if len(searches) >= MAX_SEARCHES:
        await update.effective_message.reply_text(
            f"⚠️ Максимум {MAX_SEARCHES} пошуки. Видаліть один через 📋 Мої пошуки.",
            reply_markup=MAIN_KB)
        return ConversationHandler.END

    new_sp(ctx)
    kb = make_kb(list(DEAL_TYPES.keys()), cols=1, prefix="deal:")
    await update.effective_message.reply_text(
        "🏠 *Новий пошук*\n\nТип угоди:",
        reply_markup=kb, parse_mode="Markdown")
    return S_DEAL


async def cb_deal(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer()
    deal = DEAL_TYPES.get(q.data.replace("deal:",""), "buy")
    cur_sp(ctx).deal = deal
    kb = make_kb(list(PROP_TYPES.keys()), cols=2, prefix="prop:")
    await q.edit_message_text("Тип нерухомості:", reply_markup=kb)
    return S_PROP


async def cb_prop(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer()
    prop = PROP_TYPES.get(q.data.replace("prop:",""), "flat")
    cur_sp(ctx).prop = prop
    kb = make_kb(list(CITIES.keys()), cols=2, prefix="city:")
    await q.edit_message_text("📍 Місто:", reply_markup=kb)
    return S_CITY


async def cb_city(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer()
    city = q.data.replace("city:","")
    cur_sp(ctx).city = city
    districts = DISTRICTS.get(city, [])
    kb = make_kb(districts, cols=2, prefix="dist:")
    await q.edit_message_text(f"📍 *{city}* — Район:", reply_markup=kb, parse_mode="Markdown")
    return S_DISTRICT


async def cb_district(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer()
    d = q.data.replace("dist:","")
    cur_sp(ctx).district = "" if d == "Будь-який" else d
    kb = make_kb(["1","2","3","4+","Будь-яка"], cols=3, prefix="rooms:")
    await q.edit_message_text(f"📍 Район: *{d}*\n\nКількість кімнат:", reply_markup=kb, parse_mode="Markdown")
    return S_ROOMS


async def cb_rooms(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer()
    cur_sp(ctx).rooms = q.data.replace("rooms:","")
    sp = cur_sp(ctx)
    if sp.prop == "house":
        kb = make_kb(["1","2","3","Будь-яка"], cols=2, prefix="floors:")
        await q.edit_message_text("🏠 Кількість поверхів будинку:", reply_markup=kb, parse_mode="Markdown")
        return S_FLOORS
    else:
        return await _ask_currency(q, ctx)


async def cb_floors(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer()
    cur_sp(ctx).floors = q.data.replace("floors:","")
    return await _ask_currency(q, ctx)


async def _ask_currency(src, ctx):
    sp = cur_sp(ctx)
    if sp.deal == "buy":
        opts = ["💵 USD (долар)", "💴 UAH (гривня)"]
    else:
        opts = ["💴 UAH (гривня)", "💵 USD (долар)"]
    kb = make_kb(opts, cols=2, prefix="cur:")
    text = "💱 Валюта ціни:"
    if hasattr(src, "edit_message_text"):
        await src.edit_message_text(text, reply_markup=kb)
    else:
        await src.message.reply_text(text, reply_markup=kb)
    return S_CURRENCY


async def cb_currency(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer()
    cur_sp(ctx).currency = "USD" if "USD" in q.data else "UAH"
    return await _ask_price_from(q, ctx)


async def _ask_price_from(src, ctx):
    sp = cur_sp(ctx)
    opts = PRICE_OPTS.get(sp.deal, PRICE_OPTS["buy"])[sp.currency]
    symbol = "$" if sp.currency == "USD" else "₴"
    text = f"💰 Ціна *від* {symbol}?\n_(або напишіть своє число)_"
    kb = make_kb(opts, cols=2, prefix="pfrom:")
    if hasattr(src, "edit_message_text"):
        await src.edit_message_text(text, reply_markup=kb, parse_mode="Markdown")
    else:
        await src.message.reply_text(text, reply_markup=kb, parse_mode="Markdown")
    return S_PRICE_FROM


async def handle_pfrom_btn(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer()
    val = q.data.replace("pfrom:","").replace(" ","")
    cur_sp(ctx).price_from = 0 if "Без" in val else int(val)
    return await _ask_price_to(q, ctx)

async def handle_pfrom_txt(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    try:
        cur_sp(ctx).price_from = int(update.message.text.strip().replace(" ","").replace("$","").replace("₴",""))
    except:
        await update.message.reply_text("⚠️ Введіть число"); return S_PRICE_FROM
    return await _ask_price_to(update, ctx)

async def _ask_price_to(src, ctx):
    sp = cur_sp(ctx)
    opts = PRICE_OPTS.get(sp.deal, PRICE_OPTS["buy"])[sp.currency]
    symbol = "$" if sp.currency == "USD" else "₴"
    pf = f"{symbol}{sp.price_from:,}".replace(",","") if sp.price_from else "—"
    text = f"Від: *{pf}*\n\n💰 Ціна *до* {symbol}?\n_(або напишіть своє число)_"
    kb = make_kb(opts, cols=2, prefix="pto:")
    if hasattr(src, "edit_message_text"):
        await src.edit_message_text(text, reply_markup=kb, parse_mode="Markdown")
    else:
        await src.message.reply_text(text, reply_markup=kb, parse_mode="Markdown")
    return S_PRICE_TO

async def handle_pto_btn(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer()
    val = q.data.replace("pto:","").replace(" ","")
    cur_sp(ctx).price_to = 0 if "Без" in val else int(val)
    return await _ask_area_from(q, ctx)

async def handle_pto_txt(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    try:
        cur_sp(ctx).price_to = int(update.message.text.strip().replace(" ","").replace("$","").replace("₴",""))
    except:
        await update.message.reply_text("⚠️ Введіть число"); return S_PRICE_TO
    return await _ask_area_from(update, ctx)

async def _ask_area_from(src, ctx):
    kb = make_kb(["20","30","40","50","60","80","Без обмеження"], cols=3, prefix="afrom:")
    text = "📐 Площа *від* м²?\n_(або напишіть своє число)_"
    if hasattr(src, "edit_message_text"):
        await src.edit_message_text(text, reply_markup=kb, parse_mode="Markdown")
    else:
        await src.message.reply_text(text, reply_markup=kb, parse_mode="Markdown")
    return S_AREA_FROM

async def handle_afrom_btn(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer()
    val = q.data.replace("afrom:","")
    cur_sp(ctx).area_from = 0 if "Без" in val else int(val)
    return await _ask_area_to(q, ctx)

async def handle_afrom_txt(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    try:
        cur_sp(ctx).area_from = int(update.message.text.strip())
    except:
        await update.message.reply_text("⚠️ Введіть число"); return S_AREA_FROM
    return await _ask_area_to(update, ctx)

async def _ask_area_to(src, ctx):
    kb = make_kb(["40","60","80","100","120","150","Без обмеження"], cols=3, prefix="ato:")
    text = "📐 Площа *до* м²?\n_(або напишіть своє число)_"
    if hasattr(src, "edit_message_text"):
        await src.edit_message_text(text, reply_markup=kb, parse_mode="Markdown")
    else:
        await src.message.reply_text(text, reply_markup=kb, parse_mode="Markdown")
    return S_AREA_TO

async def handle_ato_btn(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer()
    val = q.data.replace("ato:","")
    cur_sp(ctx).area_to = 0 if "Без" in val else int(val)
    return await _ask_condition(q, ctx)

async def handle_ato_txt(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    try:
        cur_sp(ctx).area_to = int(update.message.text.strip())
    except:
        await update.message.reply_text("⚠️ Введіть число"); return S_AREA_TO
    return await _ask_condition(update, ctx)

async def _ask_condition(src, ctx):
    sp = cur_sp(ctx)
    ctx.user_data["sel_cond"] = []
    conds = CONDITIONS_BUY if sp.deal == "buy" else CONDITIONS_RENT
    rows = [[InlineKeyboardButton(n, callback_data=f"cond:{n}")] for n in conds]
    rows.append([InlineKeyboardButton("✅ Готово", callback_data="cond:done")])
    text = "🛠 Стан _(можна кілька)_:"
    if hasattr(src, "edit_message_text"):
        await src.edit_message_text(text, reply_markup=InlineKeyboardMarkup(rows), parse_mode="Markdown")
    else:
        await src.message.reply_text(text, reply_markup=InlineKeyboardMarkup(rows), parse_mode="Markdown")
    return S_CONDITION

async def cb_condition(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer()
    val = q.data.replace("cond:","")
    sp = cur_sp(ctx)
    conds = CONDITIONS_BUY if sp.deal == "buy" else CONDITIONS_RENT

    if val == "done":
        sp.conditions = ctx.user_data.get("sel_cond", [])
        await q.edit_message_text(
            "🔑 Ключові слова _(необов'язково)_:\n\nНапишіть через кому:\n"
            "_авторський ремонт, вид на море, центр_\n\nАбо /skip",
            parse_mode="Markdown")
        return S_KEYWORDS

    selected = ctx.user_data.get("sel_cond", [])
    cval = conds.get(val, "")
    if val.startswith("🔄"):
        selected = []
    else:
        if cval in selected: selected.remove(cval)
        else: selected.append(cval)
    ctx.user_data["sel_cond"] = selected

    rows = []
    for name, value in conds.items():
        check = "✅ " if value in selected else ""
        rows.append([InlineKeyboardButton(check+name, callback_data=f"cond:{name}")])
    rows.append([InlineKeyboardButton("✅ Готово", callback_data="cond:done")])
    await q.edit_message_reply_markup(reply_markup=InlineKeyboardMarkup(rows))
    return S_CONDITION

async def handle_keywords(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    cur_sp(ctx).keywords = [kw.strip() for kw in update.message.text.split(",") if kw.strip()]
    return await _ask_name(update.message, ctx)

async def cmd_skip(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    cur_sp(ctx).keywords = []
    return await _ask_name(update.message, ctx)

async def _ask_name(msg, ctx):
    sp = cur_sp(ctx)
    auto = sp.auto_name()
    await msg.reply_text(
        f"🏷 Назва пошуку:\n\nАвто-назва: *{auto}*\n\n"
        "Напишіть свою назву або /skip щоб використати авто",
        parse_mode="Markdown")
    return S_NAME

async def handle_name(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    cur_sp(ctx).name = update.message.text.strip()
    return await _ask_monitor_choice(update.message, ctx)

async def cmd_skip_name(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    sp = cur_sp(ctx)
    sp.name = sp.auto_name()
    return await _ask_monitor_choice(update.message, ctx)

async def _ask_monitor_choice(msg, ctx):
    kb = InlineKeyboardMarkup([[
        InlineKeyboardButton("🔔 Увімкнути моніторинг", callback_data="mon:yes"),
        InlineKeyboardButton("🔍 Разовий пошук", callback_data="mon:no"),
    ]])
    await msg.reply_text("🔔 Моніторинг нових оголошень?", reply_markup=kb)
    return S_MONITOR

async def cb_monitor(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer()
    sp = cur_sp(ctx)
    if q.data == "mon:no":
        sp.monitor = False
        await q.edit_message_text("⏳ Зберігаю та шукаю...")
        await _finish_search(q.message, ctx)
        return ConversationHandler.END
    kb = make_kb(list(FREQ_MAP.keys()), cols=3, prefix="freq:")
    await q.edit_message_text("⏱ Як часто перевіряти?", reply_markup=kb)
    return S_MONITOR_FREQ

async def cb_monitor_freq(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer()
    freq_str = q.data.replace("freq:","")
    sp = cur_sp(ctx)
    sp.monitor = True
    sp.monitor_freq = FREQ_MAP.get(freq_str, 60)
    await q.edit_message_text(f"✅ Моніторинг кожні *{freq_str}*\n\n⏳ Зберігаю та шукаю...", parse_mode="Markdown")
    await _finish_search(q.message, ctx)
    return ConversationHandler.END

async def _finish_search(msg, ctx):
    chat_id = msg.chat_id
    sp = cur_sp(ctx)
    save_search(chat_id, sp)
    if sp.monitor:
        start_monitor_job(ctx.application, chat_id, sp)
    await _do_search(ctx.application, chat_id, sp)
    await ctx.application.bot.send_message(
        chat_id=chat_id,
        text=f"✅ Пошук *{sp.name}* збережено!\n\nКерування через 📋 Мої пошуки",
        reply_markup=MAIN_KB, parse_mode="Markdown")


# ═══════════════════════════════════════════════════════════════
# МОЇ ПОШУКИ
# ═══════════════════════════════════════════════════════════════

async def show_my_searches(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    searches = get_searches(chat_id)
    if not searches:
        await update.effective_message.reply_text(
            "У вас немає збережених пошуків.\n\nНатисніть ➕ Новий пошук",
            reply_markup=MAIN_KB)
        return

    await update.effective_message.reply_text(
        f"📋 *Мої пошуки* ({len(searches)}/{MAX_SEARCHES}):",
        parse_mode="Markdown", reply_markup=MAIN_KB)

    for sp in searches:
        status = "⏸ пауза" if sp.paused else ("🟢 моніторинг" if sp.monitor else "🔍 разовий")
        text = sp.summary()
        freq_str = next((k for k,v in FREQ_MAP.items() if v==sp.monitor_freq),"")

        rows = [
            [InlineKeyboardButton("🔍 Шукати зараз", callback_data=f"srch:{sp.id}")],
            [InlineKeyboardButton("✏️ Редагувати", callback_data=f"edit:{sp.id}"),
             InlineKeyboardButton("🏷 Перейменувати", callback_data=f"ren:{sp.id}")],
        ]
        if sp.monitor:
            if sp.paused:
                rows.append([InlineKeyboardButton("▶️ Відновити моніторинг", callback_data=f"resume:{sp.id}")])
            else:
                rows.append([InlineKeyboardButton("⏸ Пауза", callback_data=f"pause:{sp.id}")])
            rows.append([InlineKeyboardButton(f"⏱ Змінити частоту ({freq_str})", callback_data=f"chfreq:{sp.id}")])
        else:
            rows.append([InlineKeyboardButton("🔔 Увімкнути моніторинг", callback_data=f"enmon:{sp.id}")])
        rows.append([InlineKeyboardButton("🗑 Видалити", callback_data=f"del:{sp.id}")])

        await update.effective_message.reply_text(
            text, parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(rows))


async def cb_search_now(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer("Шукаю...")
    search_id = q.data.replace("srch:","")
    sp = get_search(q.message.chat_id, search_id)
    if not sp:
        await q.message.reply_text("Пошук не знайдено"); return
    await q.message.reply_text("⏳ Шукаю, зачекайте ~30 сек...")
    await _do_search(ctx.application, q.message.chat_id, sp)


async def cb_pause(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer()
    search_id = q.data.replace("pause:","")
    chat_id = q.message.chat_id
    sp = get_search(chat_id, search_id)
    if sp:
        sp.paused = True
        jobs = ctx.application.job_queue.get_jobs_by_name(f"mon_{chat_id}_{sp.id}")
        for j in jobs: j.schedule_removal()
        await q.message.reply_text(f"⏸ Моніторинг *{sp.name}* призупинено", parse_mode="Markdown")


async def cb_resume(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer()
    search_id = q.data.replace("resume:","")
    chat_id = q.message.chat_id
    sp = get_search(chat_id, search_id)
    if sp:
        sp.paused = False
        start_monitor_job(ctx.application, chat_id, sp)
        await q.message.reply_text(f"▶️ Моніторинг *{sp.name}* відновлено", parse_mode="Markdown")


async def cb_enable_monitor(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer()
    search_id = q.data.replace("enmon:","")
    chat_id = q.message.chat_id
    sp = get_search(chat_id, search_id)
    if not sp: return
    ctx.user_data["edit_id"] = search_id
    kb = make_kb(list(FREQ_MAP.keys()), cols=3, prefix="enmonfreq:")
    await q.message.reply_text(f"⏱ Частота моніторингу для *{sp.name}*:", reply_markup=kb, parse_mode="Markdown")


async def cb_enable_monitor_freq(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer()
    search_id = ctx.user_data.get("edit_id","")
    chat_id = q.message.chat_id
    sp = get_search(chat_id, search_id)
    if not sp: return
    freq_str = q.data.replace("enmonfreq:","")
    sp.monitor = True
    sp.paused = False
    sp.monitor_freq = FREQ_MAP.get(freq_str, 60)
    start_monitor_job(ctx.application, chat_id, sp)
    await q.edit_message_text(f"🔔 Моніторинг *{sp.name}* увімкнено кожні *{freq_str}*", parse_mode="Markdown")


async def cb_change_freq(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer()
    search_id = q.data.replace("chfreq:","")
    ctx.user_data["edit_id"] = search_id
    sp = get_search(q.message.chat_id, search_id)
    if not sp: return
    kb = make_kb(list(FREQ_MAP.keys()), cols=3, prefix="newfreq:")
    await q.message.reply_text(
        f"⏱ Нова частота для *{sp.name}*:",
        reply_markup=kb, parse_mode="Markdown")


async def cb_new_freq(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer()
    search_id = ctx.user_data.get("edit_id","")
    chat_id = q.message.chat_id
    sp = get_search(chat_id, search_id)
    if not sp: return
    freq_str = q.data.replace("newfreq:","")
    sp.monitor_freq = FREQ_MAP.get(freq_str, 60)
    sp.paused = False
    start_monitor_job(ctx.application, chat_id, sp)
    await q.edit_message_text(f"✅ Частота *{sp.name}* змінена на *{freq_str}*", parse_mode="Markdown")


async def cb_delete(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer()
    search_id = q.data.replace("del:","")
    chat_id = q.message.chat_id
    sp = get_search(chat_id, search_id)
    if not sp: return
    kb = InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ Так, видалити", callback_data=f"delok:{search_id}"),
        InlineKeyboardButton("❌ Скасувати", callback_data=f"delno:{search_id}"),
    ]])
    await q.message.reply_text(f"🗑 Видалити пошук *{sp.name}*?", reply_markup=kb, parse_mode="Markdown")


async def cb_delete_ok(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer()
    search_id = q.data.replace("delok:","")
    chat_id = q.message.chat_id
    sp = get_search(chat_id, search_id)
    if sp:
        jobs = ctx.application.job_queue.get_jobs_by_name(f"mon_{chat_id}_{sp.id}")
        for j in jobs: j.schedule_removal()
        delete_search(chat_id, search_id)
        await q.edit_message_text(f"🗑 Пошук *{sp.name}* видалено", parse_mode="Markdown")

async def cb_delete_no(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer()
    await q.edit_message_text("❌ Скасовано")


# ── Редагування пошуку ─────────────────────────────────────────
EDIT_FIELDS = {
    "📍 Місто/Район": "city",
    "🛏 Кімнати": "rooms",
    "💰 Ціна": "price",
    "📐 Площа": "area",
    "🛠 Стан": "condition",
    "🔑 Ключові слова": "keywords",
    "🏷 Тип угоди": "deal",
}

async def cb_edit(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer()
    search_id = q.data.replace("edit:","")
    chat_id = q.message.chat_id
    sp = get_search(chat_id, search_id)
    if not sp: return
    ctx.user_data["sp"] = sp
    ctx.user_data["edit_id"] = search_id

    rows = [[InlineKeyboardButton(f, callback_data=f"ef:{k}")] for f,k in EDIT_FIELDS.items()]
    rows.append([InlineKeyboardButton("✅ Готово (зберегти)", callback_data="ef:done")])
    await q.message.reply_text(
        f"✏️ *Редагування: {sp.name}*\n\nЩо змінити?",
        reply_markup=InlineKeyboardMarkup(rows), parse_mode="Markdown")
    return S_EDIT_CHOOSE


async def cb_edit_field(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer()
    field = q.data.replace("ef:","")

    if field == "done":
        sp = cur_sp(ctx)
        chat_id = q.message.chat_id
        save_search(chat_id, sp)
        if sp.monitor:
            start_monitor_job(ctx.application, chat_id, sp)
        await q.edit_message_text(f"✅ *{sp.name}* збережено!", parse_mode="Markdown")
        return ConversationHandler.END

    ctx.user_data["edit_field"] = field

    if field == "city":
        kb = make_kb(list(CITIES.keys()), cols=2, prefix="city:")
        await q.edit_message_text("📍 Нове місто:", reply_markup=kb)
        return S_CITY
    elif field == "rooms":
        kb = make_kb(["1","2","3","4+","Будь-яка"], cols=3, prefix="rooms:")
        await q.edit_message_text("🛏 Нова кількість кімнат:", reply_markup=kb)
        return S_ROOMS
    elif field == "price":
        return await _ask_currency(q, ctx)
    elif field == "area":
        return await _ask_area_from(q, ctx)
    elif field == "condition":
        return await _ask_condition(q, ctx)
    elif field == "keywords":
        await q.edit_message_text("🔑 Нові ключові слова через кому\nАбо /skip:")
        return S_KEYWORDS
    elif field == "deal":
        kb = make_kb(list(DEAL_TYPES.keys()), cols=1, prefix="deal:")
        await q.edit_message_text("🏷 Тип угоди:", reply_markup=kb)
        return S_DEAL
    return S_EDIT_CHOOSE


# ── Перейменування ─────────────────────────────────────────────
async def cb_rename(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer()
    search_id = q.data.replace("ren:","")
    ctx.user_data["rename_id"] = search_id
    sp = get_search(q.message.chat_id, search_id)
    await q.message.reply_text(
        f"🏷 Поточна назва: *{sp.name}*\n\nВведіть нову назву:",
        parse_mode="Markdown")
    return S_RENAME

async def handle_rename(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    search_id = ctx.user_data.get("rename_id","")
    sp = get_search(update.effective_chat.id, search_id)
    if sp:
        old = sp.name
        sp.name = update.message.text.strip()
        await update.message.reply_text(f"✅ Перейменовано: *{old}* → *{sp.name}*", parse_mode="Markdown")
    return ConversationHandler.END


# ═══════════════════════════════════════════════════════════════
# ВИКОНАННЯ ПОШУКУ
# ═══════════════════════════════════════════════════════════════

async def _do_search(app, chat_id: int, sp: SearchParams):
    loop = asyncio.get_event_loop()
    listings = await loop.run_in_executor(None, lambda: run_search(sp))

    if not listings:
        await app.bot.send_message(chat_id=chat_id,
            text=f"😕 *{sp.name}* — нічого не знайдено за вашими критеріями.",
            parse_mode="Markdown")
        return

    symbol = "$" if sp.currency == "USD" else "₴"
    pf = f"{symbol}{sp.price_from:,}".replace(",","") if sp.price_from else "—"
    pt = f"{symbol}{sp.price_to:,}".replace(",","") if sp.price_to else "—"

    await app.bot.send_message(chat_id=chat_id, parse_mode="Markdown", text=(
        f"✅ *{sp.name}* — знайдено *{len(listings)}* оголошень\n"
        f"💰 {pf} – {pt}" + (f"\n🔑 {', '.join(sp.keywords)}" if sp.keywords else "")
    ))

    for l in listings[:15]:
        await send_listing(app, chat_id, l)
        await asyncio.sleep(0.3)

    if len(listings) > 15:
        await app.bot.send_message(chat_id=chat_id,
            text=f"📋 Показано 15 з {len(listings)}. Звузьте критерії для кращих результатів.")


# ═══════════════════════════════════════════════════════════════
# КОМАНДИ
# ═══════════════════════════════════════════════════════════════

async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 *Вітаю! Пошук нерухомості UA v3.0*\n\n"
        "📋 *Мої пошуки* — керування збереженими пошуками\n"
        "➕ *Новий пошук* — створити новий\n"
        "❓ *Допомога* — інструкція\n\n"
        "Оберіть дію 👇",
        reply_markup=MAIN_KB, parse_mode="Markdown")

async def cmd_help(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🏠 *Пошук нерухомості UA v3.0*\n\n"
        "📋 *Мої пошуки* — список всіх пошуків\n"
        "   • 🔍 Шукати зараз\n"
        "   • ✏️ Редагувати параметри\n"
        "   • 🏷 Перейменувати\n"
        "   • ⏸/▶️ Пауза/відновлення моніторингу\n"
        "   • ⏱ Змінити частоту\n"
        "   • 🗑 Видалити\n\n"
        "➕ *Новий пошук* — до 3 одночасно\n\n"
        "Джерела: *OLX* та *DOM.RIA*\n"
        "Типи: купівля, оренда, подобова\n"
        "Об'єкти: квартири та будинки",
        parse_mode="Markdown", reply_markup=MAIN_KB)

async def handle_menu(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    if text == "📋 Мої пошуки":
        await show_my_searches(update, ctx)
    elif text == "➕ Новий пошук":
        return await start_new_search(update, ctx)
    elif text == "❓ Допомога":
        await cmd_help(update, ctx)


# ═══════════════════════════════════════════════════════════════
# ЗАПУСК
# ═══════════════════════════════════════════════════════════════

def main():
    app = Application.builder().token(BOT_TOKEN).build()

    # Conversation handler для створення/редагування пошуку
    conv = ConversationHandler(
        entry_points=[
            MessageHandler(filters.Regex("^➕ Новий пошук$"), start_new_search),
            CommandHandler("start", cmd_start),
            CallbackQueryHandler(cb_edit, pattern=r"^edit:"),
            CallbackQueryHandler(cb_rename, pattern=r"^ren:"),
        ],
        states={
            S_DEAL:         [CallbackQueryHandler(cb_deal,         pattern=r"^deal:")],
            S_PROP:         [CallbackQueryHandler(cb_prop,         pattern=r"^prop:")],
            S_CITY:         [CallbackQueryHandler(cb_city,         pattern=r"^city:")],
            S_DISTRICT:     [CallbackQueryHandler(cb_district,     pattern=r"^dist:")],
            S_ROOMS:        [CallbackQueryHandler(cb_rooms,        pattern=r"^rooms:")],
            S_FLOORS:       [CallbackQueryHandler(cb_floors,       pattern=r"^floors:")],
            S_CURRENCY:     [CallbackQueryHandler(cb_currency,     pattern=r"^cur:")],
            S_PRICE_FROM:   [CallbackQueryHandler(handle_pfrom_btn,pattern=r"^pfrom:"),
                             MessageHandler(filters.TEXT & ~filters.COMMAND, handle_pfrom_txt)],
            S_PRICE_TO:     [CallbackQueryHandler(handle_pto_btn,  pattern=r"^pto:"),
                             MessageHandler(filters.TEXT & ~filters.COMMAND, handle_pto_txt)],
            S_AREA_FROM:    [CallbackQueryHandler(handle_afrom_btn,pattern=r"^afrom:"),
                             MessageHandler(filters.TEXT & ~filters.COMMAND, handle_afrom_txt)],
            S_AREA_TO:      [CallbackQueryHandler(handle_ato_btn,  pattern=r"^ato:"),
                             MessageHandler(filters.TEXT & ~filters.COMMAND, handle_ato_txt)],
            S_CONDITION:    [CallbackQueryHandler(cb_condition,    pattern=r"^cond:")],
            S_KEYWORDS:     [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_keywords),
                             CommandHandler("skip", cmd_skip)],
            S_NAME:         [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_name),
                             CommandHandler("skip", cmd_skip_name)],
            S_MONITOR:      [CallbackQueryHandler(cb_monitor,      pattern=r"^mon:")],
            S_MONITOR_FREQ: [CallbackQueryHandler(cb_monitor_freq, pattern=r"^freq:")],
            S_EDIT_CHOOSE:  [CallbackQueryHandler(cb_edit_field,   pattern=r"^ef:")],
            S_RENAME:       [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_rename)],
        },
        fallbacks=[CommandHandler("start", cmd_start)],
        allow_reentry=True,
    )

    app.add_handler(conv)
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help",  cmd_help))
    app.add_handler(MessageHandler(filters.Regex("^(📋 Мої пошуки|❓ Допомога)$"), handle_menu))

    # Inline кнопки поза діалогом
    app.add_handler(CallbackQueryHandler(cb_search_now,         pattern=r"^srch:"))
    app.add_handler(CallbackQueryHandler(cb_pause,              pattern=r"^pause:"))
    app.add_handler(CallbackQueryHandler(cb_resume,             pattern=r"^resume:"))
    app.add_handler(CallbackQueryHandler(cb_enable_monitor,     pattern=r"^enmon:"))
    app.add_handler(CallbackQueryHandler(cb_enable_monitor_freq,pattern=r"^enmonfreq:"))
    app.add_handler(CallbackQueryHandler(cb_change_freq,        pattern=r"^chfreq:"))
    app.add_handler(CallbackQueryHandler(cb_new_freq,           pattern=r"^newfreq:"))
    app.add_handler(CallbackQueryHandler(cb_delete,             pattern=r"^del:"))
    app.add_handler(CallbackQueryHandler(cb_delete_ok,          pattern=r"^delok:"))
    app.add_handler(CallbackQueryHandler(cb_delete_no,          pattern=r"^delno:"))

    log.info("Бот v3.0 запущено ✅ ok")

    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()

"""
Telegram-бот парсинга недвижимости UA
======================================
Установка:
    pip install python-telegram-bot requests beautifulsoup4 lxml pandas openpyxl

Запуск:
    BOT_TOKEN=ВАШ_ТОКЕН python realty_bot.py

Получить токен: @BotFather → /newbot
"""

import os
import io
import time
import json
import logging
import re
import asyncio
from dataclasses import dataclass, asdict
from typing import Optional

import requests
import pandas as pd
from bs4 import BeautifulSoup
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler,
    MessageHandler, ContextTypes, filters, ConversationHandler,
)

logging.basicConfig(
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    level=logging.INFO,
)
log = logging.getLogger(__name__)

BOT_TOKEN = os.environ.get("BOT_TOKEN", "ВСТАВЬТЕ_ТОКЕН_СЮДА")

# ── Состояния диалога ──────────────────────────────────────────
CHOOSE_CITY, CHOOSE_ROOMS, CHOOSE_MAX_PRICE, CHOOSE_MIN_AREA, RUNNING = range(5)

CITIES_OLX = {
    "Київ": ("kiev", "kyiv", 1),
    "Львів": ("lviv", "lviv", 14),
    "Одеса": ("odesa", "odesa", 12),
    "Харків": ("kharkiv", "kharkiv", 10),
    "Дніпро": ("dnipro", "dnipro", 4),
}

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    )
}


# ─────────────────────────────────────────────────────────────
# Модель данных
# ─────────────────────────────────────────────────────────────
@dataclass
class Listing:
    source: str
    title: str
    price: str
    area: str
    rooms: str
    address: str
    url: str


def _extract_area(text: str) -> str:
    m = re.search(r"(\d{2,4}(?:[.,]\d+)?)\s*м²", text)
    return m.group(0) if m else "—"


# ─────────────────────────────────────────────────────────────
# Парсеры
# ─────────────────────────────────────────────────────────────
def parse_olx(city_olx: str, rooms: int, pages: int = 2) -> list[Listing]:
    results = []
    base = f"https://www.olx.ua/uk/nedvizhimost/kvartiry/{city_olx}/prodazha/"
    for page in range(1, pages + 1):
        url = f"{base}?rooms={rooms}&page={page}"
        try:
            r = requests.get(url, headers=HEADERS, timeout=15)
            r.raise_for_status()
        except Exception as e:
            log.warning(f"OLX: {e}")
            break
        soup = BeautifulSoup(r.text, "lxml")
        for card in soup.select("div[data-cy='l-card']"):
            try:
                title_el = card.select_one("h4,h6,[data-testid='ad-title']")
                price_el = card.select_one("[data-testid='ad-price']")
                loc_el   = card.select_one("[data-testid='location-date']")
                link_el  = card.select_one("a[href]")
                title   = title_el.get_text(strip=True) if title_el else "—"
                price   = price_el.get_text(strip=True) if price_el else "—"
                address = loc_el.get_text(strip=True)   if loc_el   else "—"
                href    = link_el["href"]                if link_el  else ""
                if href and not href.startswith("http"):
                    href = "https://www.olx.ua" + href
                results.append(Listing("OLX", title, price, _extract_area(title), str(rooms), address, href))
            except Exception:
                pass
        time.sleep(1.2)
    return results


def parse_domria(city_id: int, rooms: int, pages: int = 2) -> list[Listing]:
    results = []
    api = "https://developers.dom.ria.com/api/realty/search"
    for page in range(0, pages):
        params = dict(category=1, realty_type=2, operation_type=1,
                      state_id=city_id, count=20, page=page)
        params[f"rooms_count[]"] = rooms
        try:
            r = requests.get(api, params=params, headers=HEADERS, timeout=15)
            data = r.json()
        except Exception as e:
            log.warning(f"DOM.RIA: {e}")
            break
        for item in data.get("items", []):
            price = item.get("priceArr", {}).get("1", {}).get("$") or item.get("price", "—")
            area  = item.get("generalSquare", "—")
            addr  = ", ".join(filter(None, [
                item.get("cityNameUk", ""), item.get("districtNameUk", ""), item.get("streetNameUk", "")
            ]))
            url   = item.get("beautiful_url") or f"https://dom.ria.com/uk/realty/{item.get('realty_id','')}.html"
            results.append(Listing("DOM.RIA",
                item.get("description_uk", "—")[:100],
                f"${price}", f"{area} м²", str(rooms), addr, url))
        time.sleep(1)
    return results


def parse_lun(city_lun: str, rooms: int, pages: int = 2) -> list[Listing]:
    results = []
    base = f"https://lun.ua/uk/{city_lun}/prodazha-kvartyr"
    for page in range(1, pages + 1):
        url = f"{base}?кімнат={rooms}&сторінка={page}"
        try:
            r = requests.get(url, headers=HEADERS, timeout=15)
            r.raise_for_status()
        except Exception as e:
            log.warning(f"LUN: {e}")
            break
        soup = BeautifulSoup(r.text, "lxml")
        cards = soup.select(".card-item,.realty-item,[class*='CardItem'],[class*='ListItem']")
        for card in cards:
            try:
                title_el = card.select_one("h2,h3,.title")
                price_el = card.select_one("[class*='price'],[class*='Price']")
                addr_el  = card.select_one("[class*='address'],[class*='location']")
                link_el  = card.select_one("a[href]")
                href = link_el["href"] if link_el else ""
                if href and not href.startswith("http"):
                    href = "https://lun.ua" + href
                title = title_el.get_text(strip=True) if title_el else "—"
                results.append(Listing("LUN", title,
                    price_el.get_text(strip=True) if price_el else "—",
                    _extract_area(title), str(rooms),
                    addr_el.get_text(strip=True) if addr_el else "—", href))
            except Exception:
                pass
        # JSON-LD fallback
        for script in soup.find_all("script", type="application/ld+json"):
            try:
                data = json.loads(script.string or "")
                items = data if isinstance(data, list) else [data]
                for d in items:
                    if d.get("@type") in ("Apartment","RealEstateListing","Product"):
                        results.append(Listing("LUN", d.get("name","—"),
                            str(d.get("offers",{}).get("price","—")),
                            str(d.get("floorSize",{}).get("value","—")) + " м²",
                            str(rooms),
                            d.get("address",{}).get("streetAddress","—"),
                            d.get("url","—")))
            except Exception:
                pass
        time.sleep(1.5)
    return results


def filter_listings(listings, max_price_usd=None, min_area=None):
    def usd(s):
        nums = re.findall(r"\d+", s.replace(" ","").replace("\xa0",""))
        return int("".join(nums[:3])) if nums else None
    def sqm(s):
        nums = re.findall(r"\d+[.,]?\d*", s)
        return float(nums[0].replace(",",".")) if nums else None
    out = []
    for l in listings:
        p, a = usd(l.price), sqm(l.area)
        if max_price_usd and p and p > max_price_usd:
            continue
        if min_area and a and a < min_area:
            continue
        out.append(l)
    return out


def to_excel_bytes(listings: list[Listing]) -> bytes:
    df = pd.DataFrame([asdict(l) for l in listings])
    buf = io.BytesIO()
    df.to_excel(buf, index=False)
    buf.seek(0)
    return buf.read()


# ─────────────────────────────────────────────────────────────
# Хэндлеры бота
# ─────────────────────────────────────────────────────────────
async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    kb = [[InlineKeyboardButton(c, callback_data=f"city:{c}")] for c in CITIES_OLX]
    await update.message.reply_text(
        "🏠 *Парсер нерухомості UA*\n\nОберіть місто:",
        reply_markup=InlineKeyboardMarkup(kb),
        parse_mode="Markdown",
    )
    return CHOOSE_CITY


async def cb_city(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    city = query.data.split(":")[1]
    ctx.user_data["city"] = city
    kb = [[
        InlineKeyboardButton("1", callback_data="rooms:1"),
        InlineKeyboardButton("2", callback_data="rooms:2"),
        InlineKeyboardButton("3", callback_data="rooms:3"),
        InlineKeyboardButton("4+", callback_data="rooms:4"),
    ]]
    await query.edit_message_text(
        f"📍 Місто: *{city}*\n\nКількість кімнат:",
        reply_markup=InlineKeyboardMarkup(kb),
        parse_mode="Markdown",
    )
    return CHOOSE_ROOMS


async def cb_rooms(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    rooms = int(query.data.split(":")[1])
    ctx.user_data["rooms"] = rooms
    kb = [
        [InlineKeyboardButton("до $50 000",  callback_data="price:50000"),
         InlineKeyboardButton("до $80 000",  callback_data="price:80000")],
        [InlineKeyboardButton("до $120 000", callback_data="price:120000"),
         InlineKeyboardButton("Без обмежень",callback_data="price:0")],
    ]
    await query.edit_message_text(
        f"🛏 Кімнат: *{rooms}*\n\nМаксимальна ціна:",
        reply_markup=InlineKeyboardMarkup(kb),
        parse_mode="Markdown",
    )
    return CHOOSE_MAX_PRICE


async def cb_price(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    price = int(query.data.split(":")[1])
    ctx.user_data["max_price"] = price or None
    kb = [
        [InlineKeyboardButton("від 30 м²", callback_data="area:30"),
         InlineKeyboardButton("від 45 м²", callback_data="area:45")],
        [InlineKeyboardButton("від 60 м²", callback_data="area:60"),
         InlineKeyboardButton("Без обмежень", callback_data="area:0")],
    ]
    price_txt = f"до ${price:,}" if price else "без обмежень"
    await query.edit_message_text(
        f"💰 Ціна: *{price_txt}*\n\nМінімальна площа:",
        reply_markup=InlineKeyboardMarkup(kb),
        parse_mode="Markdown",
    )
    return CHOOSE_MIN_AREA


async def cb_area(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    area = float(query.data.split(":")[1])
    ctx.user_data["min_area"] = area or None

    ud = ctx.user_data
    city      = ud["city"]
    rooms     = ud["rooms"]
    max_price = ud.get("max_price")
    min_area  = ud.get("min_area")

    city_olx, city_lun, city_id = CITIES_OLX[city]
    area_txt  = f"від {int(area)} м²" if area else "без обмежень"
    price_txt = f"до ${max_price:,}" if max_price else "без обмежень"

    await query.edit_message_text(
        f"⏳ Запускаю пошук...\n\n"
        f"📍 {city} | 🛏 {rooms} кімн. | 💰 {price_txt} | 📐 {area_txt}\n\n"
        f"Парсинг OLX + DOM.RIA + LUN — зачекайте ~30 сек.",
        parse_mode="Markdown",
    )

    # Запускаємо в executor щоб не блокувати event loop
    loop = asyncio.get_event_loop()
    listings = await loop.run_in_executor(None, lambda: _run_parsers(
        city_olx, city_lun, city_id, rooms, max_price, min_area
    ))

    if not listings:
        await query.message.reply_text("😕 Нічого не знайдено за вашими критеріями.")
        return ConversationHandler.END

    # Надсилаємо Excel-файл
    xlsx = await loop.run_in_executor(None, lambda: to_excel_bytes(listings))
    filename = f"нерухомість_{city}_{rooms}кімн.xlsx"
    await query.message.reply_document(
        document=io.BytesIO(xlsx),
        filename=filename,
        caption=(
            f"✅ Знайдено *{len(listings)}* оголошень\n"
            f"📍 {city} | 🛏 {rooms} кімн. | 💰 {price_txt} | 📐 {area_txt}"
        ),
        parse_mode="Markdown",
    )

    # Перші 5 — текстом в чат
    await query.message.reply_text("🔝 Перші 5 результатів:")
    for i, l in enumerate(listings[:5], 1):
        await query.message.reply_text(
            f"*{i}. [{l.source}]* {l.title[:60]}\n"
            f"💰 {l.price}  📐 {l.area}\n"
            f"📍 {l.address[:50]}\n"
            f"🔗 {l.url}",
            parse_mode="Markdown",
            disable_web_page_preview=True,
        )

    await query.message.reply_text(
        "🔄 Новий пошук? /start",
    )
    return ConversationHandler.END


def _run_parsers(city_olx, city_lun, city_id, rooms, max_price, min_area):
    all_listings = []
    all_listings += parse_olx(city_olx, rooms, pages=2)
    all_listings += parse_domria(city_id, rooms, pages=2)
    all_listings += parse_lun(city_lun, rooms, pages=2)
    return filter_listings(all_listings, max_price_usd=max_price, min_area=min_area)


async def cmd_help(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🏠 *Парсер нерухомості UA*\n\n"
        "/start — новий пошук\n"
        "/help — допомога\n\n"
        "Бот збирає оголошення з OLX, DOM.RIA та LUN.ua "
        "і надсилає результат у вигляді Excel-файлу.",
        parse_mode="Markdown",
    )


async def cmd_cancel(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("❌ Скасовано. /start — почати знову.")
    return ConversationHandler.END


# ─────────────────────────────────────────────────────────────
# Запуск
# ─────────────────────────────────────────────────────────────
def main():
    app = Application.builder().token(BOT_TOKEN).build()

    conv = ConversationHandler(
        entry_points=[CommandHandler("start", cmd_start)],
        states={
            CHOOSE_CITY:      [CallbackQueryHandler(cb_city,  pattern=r"^city:")],
            CHOOSE_ROOMS:     [CallbackQueryHandler(cb_rooms, pattern=r"^rooms:")],
            CHOOSE_MAX_PRICE: [CallbackQueryHandler(cb_price, pattern=r"^price:")],
            CHOOSE_MIN_AREA:  [CallbackQueryHandler(cb_area,  pattern=r"^area:")],
        },
        fallbacks=[CommandHandler("cancel", cmd_cancel)],
    )

    app.add_handler(conv)
    app.add_handler(CommandHandler("help", cmd_help))

    log.info("Бот запущено ✅")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()

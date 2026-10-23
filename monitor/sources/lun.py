"""ЛУН (lun.ua): агрегатор, підтягує оголошення з OLX, rieltor.ua та інших сайтів.

Дані беремо зі сторінки пошуку: картки лежать у JSON усередині Next.js-потоку.
Сортування sort=insert_time дає найновіші першими.
"""
import json
import re
from urllib.parse import urlencode, urlsplit, parse_qsl, urlunsplit

from ..common import Listing, get, log

BASE = "https://lun.ua"
CITY = {"odesa": "odesa", "mykolaiv": "mykolaiv", "kyiv": "kyiv"}
SECTION = {"sale": "sale", "rent": "rent"}   # подобової оренди на ЛУН немає

FLIGHT_RE = re.compile(r'self\.__next_f\.push\(\[1,"((?:[^"\\]|\\.)*)"\]\)')


def build_url(s: dict, page: int) -> str:
    if s.get("lun_url"):
        # користувач вставив готове посилання з фільтрами з сайту ЛУН
        parts = urlsplit(s["lun_url"])
        q = dict(parse_qsl(parts.query))
        q["sort"] = "insert_time"
        if page > 1:
            q["page"] = str(page)
        else:
            q.pop("page", None)
        return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(q, safe=","), ""))

    section = SECTION[s["deal"]]
    path = f"/{section}/{CITY[s['city']]}/flats"
    slug = s.get("_district", {}).get("lun")
    if slug:
        path += f"-{slug}"
    q = {"sort": "insert_time"}
    if s.get("rooms"):
        q["room_count"] = ",".join(str(r) for r in s["rooms"])
    if page > 1:
        q["page"] = str(page)
    return f"{BASE}{path}?{urlencode(q, safe=',')}"


def parse_page(html: str):
    flight = "".join(json.loads('"' + m + '"') for m in FLIGHT_RE.findall(html))
    i = flight.find('{"realties":{"cards":')
    if i < 0:
        return [], 0
    obj, _ = json.JSONDecoder().raw_decode(flight, i)
    r = obj["realties"]
    return r.get("cards") or [], r.get("totalRealtiesCount") or 0


def photo_url(card) -> str:
    imgs = card.get("images") or []
    if not imgs:
        return ""
    im = imgs[0]
    w, h = im.get("width") or 1200, im.get("height") or 900
    # ресайз через шлях: lun-ua/<w>/<h>/images/<id>.jpg
    tw = 1280
    th = max(1, round(tw * h / w))
    return f"https://market-images.lunstatic.net/lun-ua/{tw}/{th}/images/{im['imageId']}.jpg"


def to_listing(c: dict, usd_uah: float) -> Listing:
    cur = (c.get("currency") or "usd").lower()
    price = c.get("price")
    pu = pa = None
    if price:
        if cur == "usd":
            pu, pa = int(price), int(price * usd_uah)
        elif cur == "uah":
            pa, pu = int(price), int(price / usd_uah)
        elif cur == "eur":
            pu = int(price * 1.08)
            pa = int(pu * usd_uah)
    site = (c.get("site") or {}).get("displayName") or "lun.ua"
    extra = []
    if c.get("groupId"):
        extra.append(f"lung:{c['groupId']}")
    return Listing(
        key=f"lun:{c['id']}",
        source="ЛУН",
        origin=site,
        url=c.get("urlRaw") or f"{BASE}/",
        price_usd=pu, price_uah=pa, currency=cur,
        rooms=c.get("roomCount"),
        area=c.get("areaTotal"),
        floor=c.get("floor"), floors=c.get("floorCount"),
        street=c.get("header") or "",
        geo=c.get("geo") or "",
        text=c.get("text") or "",
        photo=photo_url(c),
        published=(c.get("insertTime") or "").replace("T", " ")[:16],
        without_renovation=c.get("withoutRenovation"),
        is_owner=c.get("isOwner"),
        without_commission=c.get("withoutCommission"),
        extra_keys=extra,
    )


def fetch(s: dict, seen: dict, usd_uah: float, first_run: bool, max_pages: int = 3):
    """Повертає (нові, загальна_кількість, посилання_на_пошук, додаткові_бачені_ключі)."""
    if s["deal"] not in SECTION and not s.get("lun_url"):
        return [], 0, "", []
    new, total = [], 0
    first_url = build_url(s, 1)
    for page in range(1, max_pages + 1):
        url = build_url(s, page)
        r = get(url)
        html = r.text
        cards, total = parse_page(html)
        if not cards:
            if page == 1:
                import gzip, os
                dbg = os.path.join(os.environ.get("STATE_DIR", "state"), f"debug_lun_{s['id']}.html.gz")
                with gzip.open(dbg, "wt", encoding="utf-8") as f:
                    f.write(html)
                title = re.search(r"<title>(.*?)</title>", html, re.S)
                raise RuntimeError(
                    f"ЛУН не віддав карток: HTTP {r.status_code}, {len(html)} байт, "
                    f"final={r.url[:120]}, title={(title.group(1) if title else '')[:80]!r}, "
                    f"flight_chunks={len(FLIGHT_RE.findall(html))}, has_realties={'realties' in html}")
            break
        unseen = [c for c in cards if f"lun:{c['id']}" not in seen]
        for c in unseen:
            new.append(to_listing(c, usd_uah))
        log.info("ЛУН %s стор.%d: карток %d, нових %d, всього %d", s["id"], page, len(cards), len(unseen), total)
        # якщо на сторінці вже є бачені — далі старіші, гортати не треба
        if not first_run and len(unseen) < len(cards):
            break
    return new, total, first_url, []

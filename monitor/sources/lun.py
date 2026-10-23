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

REF_RE = re.compile(r"\$[0-9a-f]+")
FLIGHT_RE = re.compile(r'self\.__next_f\.push\(\[1,"((?:[^"\\]|\\.)*)"\]\)')


def build_url(s: dict, page: int, seg: str = None) -> str:
    """Кімнати — повтореним параметром (room_count=3&room_count=4): форму «3,4» ЛУН не розуміє."""
    if s.get("lun_url"):
        # користувач вставив готове посилання з фільтрами з сайту ЛУН
        parts = urlsplit(s["lun_url"])
        q = []
        for k, v in parse_qsl(parts.query):
            if k in ("sort", "page"):
                continue
            if k == "room_count" and "," in v:
                q += [(k, x) for x in v.split(",") if x]
            else:
                q.append((k, v))
        q.append(("sort", "insert_time"))
        if page > 1:
            q.append(("page", str(page)))
        return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(q), ""))

    section = SECTION[s["deal"]]
    if seg is None and (s.get("_district") or {}).get("lun"):
        seg = "flats-" + s["_district"]["lun"]
    path = f"/{section}/{CITY[s['city']]}/{seg or 'flats'}"
    q = [("room_count", str(r)) for r in s.get("rooms") or []]
    # ціна на стороні ЛУН: валюта ОБОВ'ЯЗКОВО великими літерами (USD/UAH), інакше сторінка порожня
    field, cur = ("price_usd", "USD") if s["deal"] == "sale" else ("price_uah", "UAH")
    rng = s.get(field) or {}
    if rng.get("min") or rng.get("max"):
        q.append(("currency", cur))
        if rng.get("min"):
            q.append(("price_min", str(int(rng["min"]))))
        if rng.get("max"):
            q.append(("price_max", str(int(rng["max"]))))
    area = s.get("area") or {}
    if area.get("min"):
        q.append(("area_min", str(area["min"])))
    if area.get("max"):
        q.append(("area_max", str(area["max"])))
    q.append(("sort", "insert_time"))
    if page > 1:
        q.append(("page", str(page)))
    return f"{BASE}{path}?{urlencode(q)}"


ROW_RE = re.compile(rb"([0-9a-f]+):")


def text_rows(flight: str) -> dict:
    """Довгі тексти Next.js виносить в окремі рядки «<id>:T<довжина_hex>,<текст>», а в картці лишає «$<id>»."""
    b = flight.encode("utf-8")
    rows, i, n = {}, 0, len(b)
    while i < n:
        m = ROW_RE.match(b, i)
        if not m:
            j = b.find(b"\n", i)
            i = n if j < 0 else j + 1
            continue
        key, i = m.group(1).decode(), m.end()
        if b[i:i + 1] == b"T":
            comma = b.find(b",", i)
            length = int(b[i + 1:comma], 16)
            rows[key] = b[comma + 1:comma + 1 + length].decode("utf-8", "replace")
            i = comma + 1 + length
        else:
            j = b.find(b"\n", i)
            i = n if j < 0 else j + 1
    return rows


def parse_page(html: str):
    flight = "".join(json.loads('"' + m + '"') for m in FLIGHT_RE.findall(html))
    i = flight.find('{"realties":{')
    if i < 0:
        return None, 0                    # сторінка не та (немає блоку оголошень)
    obj, _ = json.JSONDecoder().raw_decode(flight, i)
    r = obj["realties"]
    cards = r.get("cards") or []
    if any(isinstance(c.get("text"), str) and REF_RE.fullmatch(c["text"]) for c in cards):
        rows = text_rows(flight)
        for c in cards:
            t = c.get("text")
            if isinstance(t, str) and REF_RE.fullmatch(t):
                c["text"] = rows.get(t[1:], "")
    return cards, r.get("totalRealtiesCount") or 0


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
    rc = next((g for g in (c.get("geoEntities") or []) if g.get("type") == "residential_complex"), None)
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
        complex=(rc or {}).get("name") or "",
        without_commission=c.get("withoutCommission"),
        extra_keys=extra,
    )


def fetch(s: dict, seen: dict, usd_uah: float, first_run: bool, backfill: bool = False, max_pages: int = None):
    """Повертає (нові, загальна_кількість, посилання, додаткові_бачені_ключі, оновлені_вже_бачені).
    Кілька районів — окрема сторінка ЛУН на кожен."""
    if s["deal"] not in SECTION and not s.get("lun_url"):
        return [], 0, "", [], []
    d = s.get("_district") or {}
    if s.get("lun_url"):
        slugs = [None]
    elif d.get("zhk"):
        slugs = d.get("lun_segs") or [None]                         # сторінка кожного ЖК
    else:
        slugs = [f"flats-{x}" for x in d.get("lun_slugs") or []] or [None]
    if max_pages is None:
        # перший запуск / після зміни пошуку — глибоко (у звіт), далі — лише свіжі сторінки
        max_pages = 10 if (first_run or backfill) else 3
    new, refreshed, total, keys = [], [], 0, set()
    failed = []
    for slug in slugs:
        try:
            n, t, r = _fetch_one(s, seen, usd_uah, first_run, backfill, max_pages, slug)
        except Exception as e:
            if len(slugs) == 1:
                raise
            failed.append(slug)
            log.warning("ЛУН %s: %s пропущено (%s)", s["id"], slug, type(e).__name__)
            continue
        total += t
        for l in n:
            if l.key not in keys:
                keys.add(l.key); new.append(l)
        for l in r:
            if l.key not in keys:
                keys.add(l.key); refreshed.append(l)
    if failed and len(failed) == len(slugs):
        raise RuntimeError(f"ЛУН не відповів для жодної сторінки ({len(failed)})")
    return new, total, build_url(s, 1, slugs[0]), [], refreshed


def _fetch_one(s, seen, usd_uah, first_run, backfill, max_pages, slug):
    new, refreshed, total = [], [], 0
    for page in range(1, max_pages + 1):
        url = build_url(s, page, slug)
        r = get(url)
        html = r.text
        cards, total = parse_page(html)
        if cards is not None and not cards:
            break                           # оголошень за фільтрами немає — це нормально
        if not cards:
            if page == 1:
                try:
                    import gzip, os
                    d = os.environ.get("STATE_DIR", "state")
                    os.makedirs(d, exist_ok=True)
                    with gzip.open(os.path.join(d, f"debug_lun_{s['id']}.html.gz"), "wt", encoding="utf-8") as f:
                        f.write(html)
                except OSError:
                    pass
                title = re.search(r"<title>(.*?)</title>", html, re.S)
                raise RuntimeError(
                    f"ЛУН не віддав карток: HTTP {r.status_code}, {len(html)} байт, "
                    f"final={r.url[:120]}, title={(title.group(1) if title else '')[:80]!r}, "
                    f"flight_chunks={len(FLIGHT_RE.findall(html))}, has_realties={'realties' in html}")
            break
        unseen = [c for c in cards if f"lun:{c['id']}" not in seen]
        for c in cards:
            (new if f"lun:{c['id']}" not in seen else refreshed).append(to_listing(c, usd_uah))
        log.info("ЛУН %s %sстор.%d: карток %d, нових %d, всього %d", s["id"], f"{slug} " if slug else "",
                 page, len(cards), len(unseen), total)
        # якщо на сторінці вже є бачені — далі старіші, гортати не треба
        if not (first_run or backfill) and len(unseen) < len(cards):
            break
    return new, total, refreshed

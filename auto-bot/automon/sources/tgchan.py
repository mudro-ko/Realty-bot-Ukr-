"""Публічні Telegram-канали з продажем авто: читаємо веб-перегляд t.me/s/<канал> і розбираємо пости."""
import html as H
import re

from .. import catalog as C
from ..common import Car, get, log

DEFAULT_CHANNELS = ["autobazarkyiv1", "KievavtoSale", "kievavto2", "kievautotrade", "PerekupiKievLive",
                    "bazaravtoukr", "ua_autobazar", "prodatu_avto", "autohub_24_autonext", "tachkavplus",
                    "avtorynok_ukraina", "imperiya_auto_1"]

POST_RE = re.compile(r'<div class="tgme_widget_message_wrap.*?data-post="([^"]+)"(.*?)(?=<div class="tgme_widget_message_wrap|\Z)', re.S)
TEXT_RE = re.compile(r'<div class="tgme_widget_message_text js-message_text"[^>]*>(.*?)</div>', re.S)   # не цитата-відповідь
TIME_RE = re.compile(r'<time datetime="([^"]+)"')
PHOTO_RE = re.compile(r"tgme_widget_message_photo_wrap[^>]*background-image:url\('([^']+)'\)")

YEAR_RE = re.compile(r"(?<!\d)(19[89]\d|20[0-3]\d)(?!\d)")
VIN_RE = re.compile(r"\b([A-HJ-NPR-Z0-9]{17})\b")
RIA_RE = re.compile(r"auto\.ria\.com/\S*?(\d{8})\.html")
PRICE_EMOJI_RE = re.compile(r"(?:💵|💰|💲|ціна|цена|price|вартість|стоимость)[\ufe0f :\-–=]{0,4}(\d{1,3}(?:[ .,\u00a0]\d{3})+|\d{3,6})(?![\d])(?!\s*(?:км|km|тис|тыс|грн|uah|₴))", re.I)
NEW_PRICE_RE = re.compile(r"(?:нова|новая|актуальн\w*|зараз|сейчас|тепер|теперь|знижка|скидка|new)\W{0,3}(?:ціна|цена|price)?\W{0,6}$", re.I)
PRICE_RE = re.compile(r"(?<![\w(])(?:\$\s*(\d{1,3}(?:[ ., ]\d{3})+|\d{3,6})|(\d{1,3}(?:[ ., ]\d{3})+|\d{3,6})\s*(?:\$|usd|дол|у\.е))", re.I)
ENGINE_RE = re.compile(r"(?<![\d.])([0-9][.,][0-9])\s*(?:л\b|l\b|тді|tdi|tsi|tfsi|бензин|дизель|газ|гібрид|гибрид|hybrid|d\b|i\b|t\b|$|\s)", re.I)


def clean(body: str) -> str:
    t = re.sub(r"<br\s*/?>", "\n", body)
    t = re.sub(r"<[^>]+>", "", t)
    return H.unescape(t).strip()


def parse_posts(page: str, channel: str):
    out = []
    for pid, chunk in POST_RE.findall(page):
        m = TEXT_RE.search(chunk)
        if not m:
            continue
        t = TIME_RE.search(chunk)
        ph = PHOTO_RE.search(chunk)
        out.append({"id": pid, "text": clean(m.group(1)), "time": t.group(1) if t else "",
                    "photo": ph.group(1) if ph else ""})
    return out


_brand_cache = {}


def _brand_index():
    if not _brand_cache:
        for b in C.brands():
            n = C.norm(b["name"])
            if len(n) >= 3 or n in ("vw", "mg", "ds"):
                _brand_cache[n] = b
        for canon, al in C.BRAND_ALIASES.items():
            b = C.brand_by_name(canon)
            if b:
                for a in al:
                    _brand_cache[C.norm(a)] = b
    return _brand_cache


FIELD_BRAND_RE = re.compile(r"(?:марка|brand)\s*[:\-]\s*([^\n|,]+)", re.I)
FIELD_MODEL_RE = re.compile(r"(?:модель|model)\s*[:\-]\s*([^\n|,]+)", re.I)


def find_brand(text):
    fb = FIELD_BRAND_RE.search(text)
    if fb:
        fm = FIELD_MODEL_RE.search(text)
        text = f"{fb.group(1).strip()} {fm.group(1).strip() if fm else ''}\n" + text
    lines = [ln for ln in text.replace("#", " ").splitlines() if ln.strip()]
    head = C.norm(" ".join(lines[:4]))[:240]
    best = None
    for key, b in _brand_index().items():
        i = head.find(key)
        if i >= 0 and (i == 0 or not head[i - 1].isalnum()) and (best is None or i < best[0] or
                                                                   (i == best[0] and len(key) > best[2])):
            best = (i, b, len(key))
    return (best[1], best[0] + best[2], head) if best else (None, 0, head)


MB_CLASS_RE = re.compile(r"(?<![a-z])(gle|glc|gls|gla|glb|cla|cls|slk|sl|gl|ml|eqe|eqs|eqc|amg gt|[abcegsv])[\s-]?(\d{2,3})(?!\d)", re.I)
BMW_SERIES_RE = re.compile(r"(?<![a-z\d])(?:m)?([1-8])(\d{2})(?:[a-z]{0,2})(?![\d])", re.I)
LEX_RE = re.compile(r"(?<![a-z])(rx|nx|es|gx|lx|is|ux|ls|gs|ct)\s?(\d{3})", re.I)


def _designation(brand, rest, ms):
    """E220d → E-Class, 530e/M550i → 5 Series, RX350 → RX, GLE 450 → GLE-Class."""
    name = brand["name"]
    want = None
    if name == "Mercedes-Benz":
        m = MB_CLASS_RE.search(rest)
        if m:
            cls = m.group(1).upper()
            want = [f"{cls}-Class", f"{cls}-Class Coupe", cls]
    elif name == "BMW":
        if re.search(r"(?<![a-z])x\s?([1-7])\b", rest):
            want = ["X" + re.search(r"(?<![a-z])x\s?([1-7])\b", rest).group(1)]
        else:
            m = BMW_SERIES_RE.search(rest)
            if m:
                want = [f"{m.group(1)} Series"]
    elif name == "Lexus":
        m = LEX_RE.search(rest)
        if m:
            want = [m.group(1).upper()]
    if not want:
        return None
    for w in want:
        hit = next((x for x in ms if C.norm(x["name"]) == C.norm(w)), None)
        if hit:
            return hit
    return None


def find_model(brand, head, after):
    rest = head[after:after + 60]
    best = None
    for m in C.models(brand["value"]):
        n = C.norm(m["name"])
        if not n:
            continue
        for v in {n, n.replace("-", " "), n.replace(" ", "-"), n.replace("-class", "").replace(" class", "")}:
            if len(v) < 1:
                continue
            j = rest.find(v)
            if j >= 0 and (j == 0 or not rest[j - 1].isalnum()) and (j + len(v) == len(rest) or not rest[j + len(v)].isalnum()):
                if best is None or len(v) > best[1]:
                    best = (m, len(v))
    if best and len(C.norm(best[0]["name"])) >= 2:
        return best[0]
    return _designation(brand, rest, C.models(brand["value"])) or (best[0] if best else None)


def _int(s):
    return int(re.sub(r"[ ., ]", "", s))


def parse_price(text):
    vals, fresh = [], None
    for m in list(PRICE_RE.finditer(text)) + list(PRICE_EMOJI_RE.finditer(text)):
        raw = m.group(1) or m.group(2)
        start = m.start()
        before = text[max(0, start - 12):start].lower()
        if "ріа" in before or "ria" in before or "рынок" in before or "ринок" in before:
            continue
        try:
            v = _int(raw)
        except ValueError:
            continue
        if 500 <= v <= 3_000_000:
            vals.append((start, v))
            if NEW_PRICE_RE.search(text[max(0, start - 30):start]):
                fresh = v
    if fresh:
        return fresh                          # «НОВА ЦІНА: 55.000$»
    vals.sort()
    return vals[-1][1] if vals else None      # остання ціна — зазвичай актуальна («37.999$ → 36.999$»)


MIL_RE = re.compile(
    r"(?:(пробі[гж]\w*|пробег\w*)\W{0,4})?(\d{1,3}(?:[ .,]\d{3})+|\d{1,6})(?:[.,]\d)?\s*"
    r"(тис\w*|тыс\w*|т(?=[\s.]|км|миль)\.?|k(?=\s|$|\b)|к(?=\s|$|\W))?\s*\.?\s*(км|km|миль|mi\b)?\.?\s*(пробі\w*|пробег\w*)?", re.I)


def parse_mileage(text):
    for m in MIL_RE.finditer(text):
        kw, num, unit, dist = m.group(1) or m.group(5), m.group(2), m.group(3), m.group(4)
        if not (kw or dist):
            continue
        if not unit and not dist and re.fullmatch(r"(19|20)\d\d", num):
            continue
        try:
            n = int(re.sub(r"[ .,]", "", num)) if re.search(r"[ .,]\d{3}$", num) else float(num.replace(",", "."))
        except ValueError:
            continue
        km = n if unit else (n / 1000 if n >= 1000 else n)
        if dist and dist.lower() in ("миль", "mi"):
            km *= 1.609
        if 0 < km <= 1500:
            return int(round(km))
    return None


def parse_fuel(t):
    t = t.lower()
    f = re.search(r"(?:паливо|топливо|двигун|двигатель)\s*[:\-]?\s*(?:\d[.,]\d\s*)?(бензин|дизель|газ|електро|электро|гібрид|гибрид)", t)
    if f:
        w = f.group(1)
        return {"бензин": "petrol", "дизель": "diesel", "газ": "lpg", "електро": "electric", "электро": "electric"}.get(w, "hybrid")
    if re.search(r"plug-?in|phev|плагін|плагин|підзаряд", t):
        return "phev"
    if re.search(r"електро|электро|electric|\bev\b|kwh|квт\W?год", t) and not re.search(r"бензин|дизель", t):
        return "electric"
    if re.search(r"гібрид|гибрид|hybrid|hev\b", t):
        return "hybrid"
    if re.search(r"газ\W*(?:/|-|\+)?\W*бенз|бенз\W*(?:/|-|\+)?\W*газ|гбо|пропан|метан", t):
        return "lpg"
    if re.search(r"дизел|diesel|\btdi\b|\bcrdi\b|\bdci\b|\bhdi\b|\bcdi\b|\b\d\.\dd\b|\b\d{3}d\b", t):
        return "diesel"
    if re.search(r"бензин|petrol|gasoline|\btsi\b|\btfsi\b", t):
        return "petrol"
    return ""


def parse_gearbox(t):
    t = t.lower()
    if re.search(r"автомат|акпп|\bat\b|робот|варіатор|вариатор|типтронік|типтроник|\bdsg\b|\bcvt\b|\bdct\b|s-tronic|steptronic", t):
        return "auto"
    if re.search(r"механік|механик|мкпп|ручна|ручная|\bmt\b", t):
        return "manual"
    return ""


def parse_drive(t):
    t = t.lower()
    if re.search(r"повний|полный|4x4|4х4|4wd|awd|4motion|quattro|x-?drive|4matic|all4", t):
        return "awd"
    if re.search(r"передн\w*\s+прив", t):
        return "fwd"
    if re.search(r"задн\w*\s+прив", t):
        return "rwd"
    return ""


def parse_city(t):
    low = t.lower()
    for key, rid in sorted(C.CITY_REGION.items(), key=lambda kv: -len(kv[0])):
        if re.search(r"(?<!\w)" + re.escape(key), low):
            line = next((ln for ln in t.splitlines() if key in ln.lower()), "")
            line = re.sub(r"^[\W_]*(?:(?:місто|город|місцезнаходження|местонахождение|локація|локация|м\.)\s*[:\-]?\s*)?", "", line.strip(), flags=re.I)
            if len(line) > 30 or re.search(r"\d{4}|\$", line):        # рядок не про місто — показуємо лише назву
                line = key.capitalize()
            line = re.sub(r"^#", "", line).split("|")[0].split(",")[0].strip(" 📍🏙🇺🇦")
            return line[:30], rid
    return "", None


def to_car(post, channel):
    t = post["text"]
    low = t.lower()
    price = parse_price(t)
    brand, after, head = find_brand(t)
    years = [int(y) for y in YEAR_RE.findall(t)]
    year = next((y for y in years if 1985 <= y <= 2031), None)
    if not price or not (brand or year):
        return None                                   # не оголошення (новини, «продано» тощо)
    if re.search(r"❌\s*продано|^продано|#продано|продано ❌", low):
        return None
    model = find_model(brand, head, after) if brand else None
    vin = VIN_RE.search(t.upper())
    ria = RIA_RE.search(t)
    eng = ENGINE_RE.search(t)
    city, rid = parse_city(t)
    customs = False if re.search(r"не\s*розмитн|нерозмитн|не\s*растамож|нерастамож|в\s+дорозі|в\s+пути|на\s+(?:польськ|литовськ|європ)\w*\s+номер", low) \
        else True if re.search(r"розмитн|растамож", low) else None
    damaged = False if re.search(r"не\s*бит|не\s*фарбован|без\s*дтп|без\s*пошкоджень|рідн\w*\s+фарб", low) \
        else True if re.search(r"після\s*дтп|после\s*дтп|\bбит[аи]?\b|пошкоджен|поврежд|тотал|на\s+запчаст", low) else None
    pid = post["id"]
    return Car(
        key=f"tg:{pid}", source=f"@{channel}", url=f"https://t.me/{pid}",
        brand=brand["name"] if brand else "", brand_id=brand["value"] if brand else None,
        model=model["name"] if model else "", model_id=model["value"] if model else None,
        year=year, price_usd=price, mileage=parse_mileage(t), fuel=parse_fuel(t), gearbox=parse_gearbox(t),
        drive=parse_drive(t), engine=float(eng.group(1).replace(",", ".")) if eng else None,
        city=city, region_id=rid, vin=vin.group(1) if vin else "", text=t, photo=post.get("photo") or "",
        published=post.get("time", "")[:16].replace("T", " "), customs_ok=customs, damaged=damaged,
        ria_id=int(ria.group(1)) if ria else None, dealer=None,
    )


def fetch(s, seen, first_run, backfill=False, channels=None, pages=None):
    """→ (нові, всього_постів, оновлені, пропущені)."""
    chans = channels or s.get("channels") or DEFAULT_CHANNELS
    pages = pages or (3 if (first_run or backfill) else 1)
    new, refreshed, total, failed = [], [], 0, 0
    for ch in chans:
        before = None
        try:
            for _ in range(pages):
                url = f"https://t.me/s/{ch}" + (f"?before={before}" if before else "")
                r = get(url, pause=0.8)
                posts = parse_posts(r.text, ch)
                if not posts:
                    break
                total += len(posts)
                for p in posts:
                    c = to_car(p, ch)
                    if not c:
                        continue
                    (refreshed if c.key in seen else new).append(c)
                ids = [int(p["id"].split("/")[-1]) for p in posts if p["id"].split("/")[-1].isdigit()]
                if not ids:
                    break
                if not (first_run or backfill) and any(f"tg:{p['id']}" in seen for p in posts):
                    break
                before = min(ids)
        except Exception as e:
            failed += 1
            log.warning("TG @%s: %s", ch, type(e).__name__)
    if failed and failed == len(chans):
        raise RuntimeError("Жоден Telegram-канал не відповів")
    log.info("TG %s: постів %d, оголошень нових %d", s.get("id"), total, len(new))
    return new, total, refreshed, []

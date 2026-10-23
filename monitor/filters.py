"""Фільтри оголошень і довідник районів."""
import re

from .common import Listing

# Райони: slug сторінки на ЛУН, ID району на DIM.RIA, назви для перевірки адреси
DISTRICTS = {
    "mykolaiv": {
        "lisky": {"lun": "lisky", "ria_ids": [15797], "names": ["Ліски"]},
    },
    "odesa": {
        # приклади; slug ЛУН видно в адресі сторінки району: lun.ua/sale/odesa/flats-<slug>
        # admin: адмінрайон — DIM.RIA часто вказує лише мікрорайон, тож бот «вчиться» складу району з адрес ЛУН
        "prymorskyi": {"lun": "prymorskyi-district", "names": ["Приморськ"], "admin": True},
        "kyivskyi": {"lun": "kyivskyi-district", "names": ["Київськ"], "admin": True},
        "khadzhybeiskyi": {"lun": "khadzhybeiskyi-district", "names": ["Хаджибейськ", "Малинов"], "admin": True},
        "peresypskyi": {"lun": "peresypskyi-district", "names": ["Пересипськ", "Суворов"], "admin": True},
        "tsentr": {"lun": "tsentr", "names": ["Центр"]},
        "arkadiia": {"lun": "arkadiia", "names": ["Аркаді"]},
        "serednii-fontan": {"lun": "serednii-fontan", "names": ["Середній Фонтан"]},
        "malyi-fontan": {"lun": "malyi-fontan", "names": ["Малий Фонтан"]},
    },
}

# Ознаки «гарного» ремонту — щоб відсіювати їх для пошуків «під ремонт / трохи краще»
RENOVATED_WORDS = [
    "євроремонт", "евроремонт", "дизайнерськ", "дизайнерск", "авторськ", "авторск",
    "дизайн-проект", "дизайн проект", "новий ремонт", "новый ремонт", "свіжий ремонт",
    "свежий ремонт", "свіжим ремонтом", "свежим ремонтом", "новим ремонтом", "новым ремонтом",
    "сучасний ремонт", "современный ремонт", "сучасним ремонтом", "современным ремонтом",
    "ремонт: євроремонт", "ремонт: дизайнерський", "ремонт: авторський",
]
NEEDS_REPAIR_WORDS = [
    "потребує ремонту", "требует ремонта", "під ремонт", "под ремонт", "без ремонту", "без ремонта",
    "після будівельників", "после строителей", "чорнов", "черновая",
]
COSMETIC_WORDS = [
    "житловий стан", "жилое состояние", "косметичн", "косметическ",
    "бабусин", "бабушкин", "старий ремонт", "старый ремонт", "радянськ", "советск",
]


CITY_NAMES = {"odesa": "Одеса", "mykolaiv": "Миколаїв", "kyiv": "Київ"}


def micro_from_geo(geo: str, city: str):
    """«вул. Х, 12, Таїрова, Київський, Одеса, Одеська область» → «Таїрова» (мікрорайон перед адмінрайоном)."""
    parts = [p.strip() for p in (geo or "").split(",")]
    name = CITY_NAMES.get(city)
    if name not in parts:
        return None
    i = parts.index(name)
    if i < 2:
        return None
    m = parts[i - 2]
    if not m or re.search(r"\d", m) or re.search(STREET_HINTS, m.lower()):
        return None
    return m


STREET_HINTS = r"(вул|просп|пров|бульв|узвіз|шосе|дорога|площа|набережна|алея|тупик|спуск|линия|лінія|ж/м|жк )"


GENERIC_WORDS = {"сел", "селище", "поселок", "посёлок", "містечко", "масив", "мкр", "мікрорайон", "район",
                 "фонтан", "ж/м", "житловий", "жилмассив", "місто"}


def district_match(district: str, wanted: list) -> bool:
    d = (district or "").lower()
    for w in wanted or []:
        toks = [t for t in re.findall(r"[a-zа-яіїєґ']+", w.lower()) if t not in GENERIC_WORDS and len(t) > 2]
        if toks and all(t in d for t in toks):
            return True
    return False


def district_codes(s: dict) -> list:
    """Підтримує і старе поле district: "x", і нове districts: ["x", "y"]."""
    ds = s.get("districts")
    if ds is None:
        ds = [s["district"]] if s.get("district") else []
    return [d for d in ds if d]


def resolve_district(s: dict) -> dict:
    """Об'єднує кілька районів: сторінки ЛУН, ID і назви для DIM.RIA.
    Якщо обрано ЖК — шукаємо саме в них (райони тоді не враховуються)."""
    if s.get("zhk"):
        from . import zhk as Z
        items = [Z.by_key(s["city"], k) or {"key": k, "name": k} for k in s["zhk"]]
        return {"zhk": True, "lun_segs": [z["key"] for z in items],
                "zhk_names": [z["name"] for z in items],
                "ria_nb_ids": [z["ria_id"] for z in items if z.get("ria_id")]}
    codes = district_codes(s)
    if not codes:
        return {}
    out = {"lun_slugs": [], "ria_ids": [], "names": [], "admin": False}
    for d in codes:
        spec = DISTRICTS.get(s["city"], {}).get(d) or {"lun": d, "names": [s.get("district_name") or d]}
        if spec.get("lun"):
            out["lun_slugs"].append(spec["lun"])
        out["ria_ids"] += spec.get("ria_ids", [])
        out["names"] += spec.get("names", [])
        out["admin"] = out["admin"] or bool(spec.get("admin"))
    out["lun"] = out["lun_slugs"][0] if out["lun_slugs"] else None
    return out


OWNER_WORDS = ["від власника", "от собственника", "от хозяина", "без комісії", "без комиссии",
               "без посередників", "без посредников", "власник", "собственник", "хазяїн", "хозяин"]
AGENCY_WORDS = ["комісія", "комиссия", "комісії", "комиссии", "агентств", "агентство", "ріелтор", "рієлтор",
                "риелтор", "риэлтор", "агенція", "послуги агент", "услуги агент"]


def seller_of(l: Listing) -> str:
    """owner | agency | unknown — за позначками сайтів і текстом."""
    if l.is_owner or l.without_commission:
        return "owner"
    if l.agency:
        return "agency"
    t = (l.text or "").lower()
    if any(w in t for w in OWNER_WORDS[:7]):      # чіткі фрази «від власника», «без комісії»
        return "owner"
    if l.is_owner is False and l.without_commission is False:
        return "agency"
    if any(w in t for w in AGENCY_WORDS):
        return "agency"
    if any(w in t for w in OWNER_WORDS[7:]):
        return "owner"
    return "unknown"


def split_keywords(s: dict):
    """keywords: ["вид на море", "-гуртожиток"] → (потрібні, заборонені). Підтримує й старі поля."""
    inc = list(s.get("include_keywords") or [])
    exc = list(s.get("exclude_keywords") or [])
    for k in s.get("keywords") or []:
        k = k.strip()
        if k.startswith("-") and len(k) > 1:
            exc.append(k[1:].strip())
        elif k:
            inc.append(k.lstrip("+").strip())
    return [k.lower() for k in inc if k], [k.lower() for k in exc if k]


def condition_of(l: Listing) -> str:
    t = (l.text or "").lower()
    if l.without_renovation:
        return "needs_repair"
    if any(w in t for w in NEEDS_REPAIR_WORDS):
        return "needs_repair"
    if any(w in t for w in COSMETIC_WORDS):
        return "cosmetic"
    if any(w in t for w in RENOVATED_WORDS):
        return "renovated"
    return "unknown"


CONDITION_LABEL = {"needs_repair": "🔨 без ремонту / під ремонт", "cosmetic": "🖌 житловий стан / косметика",
                   "renovated": "✨ з ремонтом", "unknown": ""}


def _in(v, rng):
    if not rng or v is None:
        return True
    lo, hi = rng.get("min"), rng.get("max")
    return (lo is None or v >= lo) and (hi is None or v <= hi)


def passes(l: Listing, s: dict):
    """Повертає (True/False, причина_відсіву)."""
    if s.get("rooms") and l.rooms and l.rooms not in s["rooms"]:
        return False, "кімнати"
    if not _in(l.price_usd, s.get("price_usd")):
        return False, "ціна $"
    if not _in(l.price_uah, s.get("price_uah")):
        return False, "ціна ₴"
    if not _in(l.area, s.get("area")):
        return False, "площа"
    fl = s.get("floors") or {}
    if l.floor and fl.get("not_first") and l.floor == 1:
        return False, "перший поверх"
    if l.floor and l.floors and fl.get("not_last") and l.floor == l.floors:
        return False, "останній поверх"
    text = f"{l.text} {l.geo} {l.street}".lower()
    inc, exc = split_keywords(s)
    if inc and not any(k in text for k in inc):
        return False, "ключові слова"
    if any(k in text for k in exc):
        return False, "стоп-слова"
    seller = s.get("seller") or ("owner" if (s.get("owner_only") or s.get("no_commission")) else "any")
    if seller != "any" and seller_of(l) != seller:
        return False, "продавець"
    want = s.get("condition", "any")
    cond = condition_of(l)
    if want == "needs_repair" and cond != "needs_repair":
        return False, "стан"
    if want == "needs_repair_or_cosmetic" and cond == "renovated":
        return False, "стан"
    if want == "renovated" and cond == "needs_repair":
        return False, "стан"
    return True, ""

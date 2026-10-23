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


def resolve_district(s: dict) -> dict:
    d = s.get("district")
    if not d:
        return {}
    known = DISTRICTS.get(s["city"], {}).get(d)
    if known:
        return dict(known, names=list(known.get("names", [])))
    # невідомий район: вважаємо, що це slug ЛУН, а назву перевіряємо по адресі
    return {"lun": d, "names": [s.get("district_name") or d]}


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
    inc = [k.lower() for k in s.get("include_keywords") or []]
    if inc and not any(k in text for k in inc):
        return False, "ключові слова"
    exc = [k.lower() for k in s.get("exclude_keywords") or []]
    if any(re.search(re.escape(k), text) for k in exc):
        return False, "стоп-слова"
    if s.get("owner_only") and l.is_owner is False:
        return False, "не власник"
    if s.get("no_commission") and l.without_commission is False:
        return False, "комісія"
    want = s.get("condition", "any")
    cond = condition_of(l)
    if want == "needs_repair" and cond != "needs_repair":
        return False, "стан"
    if want == "needs_repair_or_cosmetic" and cond == "renovated":
        return False, "стан"
    if want == "renovated" and cond == "needs_repair":
        return False, "стан"
    return True, ""

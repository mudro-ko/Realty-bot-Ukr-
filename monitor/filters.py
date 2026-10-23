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
        "prymorskyi": {"lun": "prymorskyi-district", "names": ["Приморськ"]},
        "kyivskyi": {"lun": "kyivskyi-district", "names": ["Київськ"]},
        "khadzhybeiskyi": {"lun": "khadzhybeiskyi-district", "names": ["Хаджибейськ", "Малинов"]},
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


def resolve_district(s: dict) -> dict:
    d = s.get("district")
    if not d:
        return {}
    known = DISTRICTS.get(s["city"], {}).get(d)
    if known:
        return known
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

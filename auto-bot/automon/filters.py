"""Фільтри авто."""
from . import catalog as C
from .common import Car
from .keywords import matches as kw_match


def _in(v, rng):
    if not rng or v is None:
        return True
    lo, hi = rng.get("min"), rng.get("max")
    return (lo is None or v >= lo) and (hi is None or v <= hi)


def split_keywords(s):
    """→ (обов'язкові, стоп-слова). Слова з «+» — бажані, див. soft_keywords()."""
    inc, exc = [], []
    for k in s.get("keywords") or []:
        k = k.strip()
        if k.startswith("-") and len(k) > 1:
            exc.append(k[1:].strip())
        elif k and not k.startswith("+"):
            inc.append(k)
    return inc, exc


def soft_keywords(s):
    """Бажані слова («+шкіра»): не відсіюють авто, лише позначають і піднімають вгору."""
    return [k.lstrip("+").strip() for k in s.get("keywords") or [] if k.strip().startswith("+") and len(k.strip()) > 1]


def car_matches_pair(c: Car, pair) -> bool:
    if c.brand_id and pair.get("brand_id"):
        if c.brand_id != pair["brand_id"]:
            return False
    elif C.norm(c.brand) != C.norm(pair.get("brand", "")):
        return False
    if not pair.get("model_id"):
        return True
    if c.model_id:
        return c.model_id == pair["model_id"]
    return bool(c.model) and C.norm(c.model) == C.norm(pair.get("model", ""))


def passes(c: Car, s: dict):
    """(True/False, причина). Для Telegram невідоме поле не відсіює (крім марки/моделі)."""
    pairs = s.get("cars") or []
    if pairs and not any(car_matches_pair(c, p) for p in pairs):
        return False, "марка/модель"
    if not _in(c.year, s.get("year")):
        return False, "рік"
    if not _in(c.price_usd, s.get("price_usd")):
        return False, "ціна"
    if not _in(c.mileage, s.get("mileage")):
        return False, "пробіг"
    if not _in(c.engine, s.get("engine")):
        return False, "двигун"
    if s.get("fuels") and c.fuel and c.fuel not in s["fuels"]:
        return False, "пальне"
    if s.get("gearbox") and c.gearbox and c.gearbox != s["gearbox"]:
        return False, "КПП"
    if s.get("drives") and c.drive and c.drive not in s["drives"]:
        return False, "привід"
    if s.get("bodies") and c.body and c.body not in s["bodies"]:
        return False, "кузов"
    if s.get("regions") and c.region_id and c.region_id not in s["regions"]:
        return False, "регіон"
    if s.get("customs_only", True) and c.customs_ok is False:
        return False, "не розмитнений"
    if s.get("no_damage", True) and c.damaged:
        return False, "після ДТП"
    if s.get("in_ukraine", True) and c.abroad:
        return False, "не в Україні"
    if s.get("seller") == "owner" and c.dealer:
        return False, "автосалон"
    text = f"{c.text} {c.generation} {c.city}"
    inc, exc = split_keywords(s)
    if inc and not any(kw_match(k, text) for k in inc):
        return False, "ключові слова"
    if any(kw_match(k, text) for k in exc):
        return False, "стоп-слова"
    soft = soft_keywords(s)
    c.wanted = [k for k in soft if kw_match(k, text)]
    c.wanted_miss = [k for k in soft if k not in c.wanted]
    return True, ""

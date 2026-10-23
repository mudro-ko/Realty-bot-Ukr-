"""DIM.RIA (dom.ria.com).

1) Пошуковий ендпоінт сайту віддає лише ID оголошень (найновіші першими).
2) Деталі тягнемо окремим запитом — тільки для ID, яких ще не бачили.
"""
from ..common import Listing, get, log
from ..filters import district_match

SEARCH = "https://dom.ria.com/node/searchEngine/v2/"
DATA = "https://dom.ria.com/realty/data/{id}"
CITY = {"odesa": (12, 12), "mykolaiv": (19, 19), "kyiv": (10, 10)}   # (state_id, city_id)
OPERATION = {"sale": 1, "rent": 3, "daily": 4}
USD = 239            # код валюти USD у характеристиці 242
NO_RENOVATION = {511, 512, 513}   # характеристика 516 «стан»: варіанти «без ремонту»


def search_params(s: dict, usd_uah: float, limit: int):
    state_id, city_id = CITY[s["city"]]
    p = [("category", 1), ("realty_type", 2), ("operation", OPERATION[s["deal"]]),
         ("state_id", state_id), ("city_id", city_id), ("wo_dupl", 1),
         ("sort", "created_at"), ("limit", limit), ("page", 0)]
    rooms = s.get("rooms")
    if rooms:
        p += [("characteristic[209][from]", min(rooms)), ("characteristic[209][to]", max(rooms))]
    # ціну фільтруємо на сервері в USD (з запасом), точно — вже у нас
    lo, hi = price_bounds_usd(s, usd_uah)
    if lo or hi:
        p.append(("characteristic[242]", USD))
        if lo:
            p.append(("characteristic[234][from]", int(lo * 0.9)))
        if hi:
            p.append(("characteristic[234][to]", int(hi * 1.1)))
    for i, did in enumerate(s.get("_district", {}).get("ria_ids", [])):
        p.append((f"district_id[{i}]", did))
    return p


def price_bounds_usd(s, usd_uah):
    pu, pa = s.get("price_usd") or {}, s.get("price_uah") or {}
    lo = pu.get("min") or (pa.get("min") and pa["min"] / usd_uah)
    hi = pu.get("max") or (pa.get("max") and pa["max"] / usd_uah)
    return lo, hi


def _int(v):
    try:
        return int(str(v).replace(" ", "").replace(" ", ""))
    except (TypeError, ValueError):
        return None


def to_listing(d: dict):
    """Повертає (Listing, назва_району)."""
    pa = d.get("priceArr") or {}
    photo = ""
    mp = d.get("main_photo") or ""
    if mp.endswith(".jpg"):
        photo = "https://cdn.riastatic.com/photos/" + mp[:-4] + "b.jpg"
    street = " ".join(x for x in [d.get("street_name_uk") or d.get("street_name") or "",
                                  d.get("building_number_str") or ""] if x).strip()
    geo = ", ".join(x for x in [street, d.get("district_name_uk") or "", d.get("city_name_uk") or ""] if x)
    cond = (d.get("characteristics_values") or {}).get("516")
    url = d.get("beautiful_url") or f"realty-{d.get('realty_id')}.html"
    return Listing(
        key=f"ria:{d['realty_id']}",
        source="DIM.RIA",
        origin="dom.ria.com",
        url="https://dom.ria.com/uk/" + url.lstrip("/"),
        price_usd=_int(pa.get("1")), price_uah=_int(pa.get("3")),
        currency={"$": "usd", "грн": "uah", "€": "eur"}.get(d.get("currency_type"), "usd"),
        rooms=d.get("rooms_count"),
        area=d.get("total_square_meters"),
        floor=d.get("floor"), floors=d.get("floors_count"),
        street=street, geo=geo,
        text=d.get("description_uk") or d.get("description") or "",
        photo=photo,
        published=(d.get("publishing_date") or "")[:16],
        without_renovation=(cond in NO_RENOVATION) if cond else None,
        agency=True if (d.get("agencyVerified") or d.get("realtorVerified") or d.get("agency_id")) else None,
        extra_keys=[],
    ), (d.get("district_name_uk") or "")


def fetch(s: dict, seen: dict, usd_uah: float, first_run: bool, backfill: bool = False, details_cap: int = 25):
    """Повертає (нові, загальна_кількість, посилання, додаткові_бачені_ключі, оновлені_вже_бачені).

    backfill=True — архів для пошуку порожній: тягнемо деталі й для вже бачених ID, щоб його заповнити.
    """
    if s["city"] not in CITY or s["deal"] not in OPERATION:
        return [], 0, "", [], []
    data = get(SEARCH, params=search_params(s, usd_uah, limit=60 if (first_run or backfill) else 40)).json()
    ids, total = data.get("items") or [], data.get("count") or 0
    unseen = [i for i in ids if f"ria:{i}" not in seen]
    log.info("DIM.RIA %s: ID %d, нових %d, всього %d", s["id"], len(ids), len(unseen), total)

    targets = [(i, True) for i in unseen]
    if backfill:
        targets += [(i, False) for i in ids if f"ria:{i}" in seen]
    new, refreshed, skipped_ids = [], [], []
    wanted = s.get("_district", {}).get("names")
    for n, (rid, is_new) in enumerate(targets):
        if n >= details_cap:
            if is_new:
                skipped_ids.append(f"ria:{rid}")   # при першому запуску решту просто позначаємо баченими
            continue
        try:
            d = get(DATA.format(id=rid), params={"lang_id": 4}, pause=0.8).json()
            l, district = to_listing(d)
        except Exception as e:
            log.warning("DIM.RIA деталі %s: %s", rid, e)
            continue
        if wanted and not district_match(district, wanted):
            if is_new:
                skipped_ids.append(l.key)
            continue
        (new if is_new else refreshed).append(l)
    return new, total, "https://dom.ria.com/uk/", skipped_ids, refreshed

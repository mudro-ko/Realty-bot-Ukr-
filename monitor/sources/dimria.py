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


def search_params(s: dict, usd_uah: float, limit: int, district_ids=None):
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
    ids = s.get("_district", {}).get("ria_ids", []) if district_ids is None else district_ids
    for i, did in enumerate(ids):
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
        district_name=d.get("district_name_uk") or "",
        district_id=d.get("district_id"),
        extra_keys=[],
    ), (d.get("district_name_uk") or "")


def _details(rid):
    d = get(DATA.format(id=rid), params={"lang_id": 4}, pause=0.8).json()
    return to_listing(d)


def fetch(s: dict, seen: dict, usd_uah: float, first_run: bool, backfill: bool = False, details_cap: int = None):
    """Повертає (нові, загальна_кількість, посилання, додаткові_бачені_ключі, оновлені_вже_бачені).

    Район: якщо відомі ID районів/мікрорайонів (вбудовані або вивчені) — фільтруємо на сайті.
    Для адмінрайонів додатково переглядаємо найновіші по місту, щоб знаходити нові мікрорайони
    (знайдені ID записуються в s["_district"]["ria_learned"]).
    backfill=True — архів порожній: тягнемо деталі й для вже бачених, щоб його заповнити.
    """
    if s["city"] not in CITY or s["deal"] not in OPERATION:
        return [], 0, "", [], []
    dist = s.get("_district") or {}
    wanted = dist.get("names")
    learned = dist.setdefault("ria_learned", {})          # назва → ID
    ids_known = sorted(set(dist.get("ria_ids", [])) | set(learned.values()))
    deep = first_run or backfill
    cap = details_cap or (60 if deep else 25)

    queries = []
    if not wanted:
        queries.append((None, 100 if deep else 40))           # без району: усе місто
    else:
        if ids_known:
            queries.append((ids_known, 100 if deep else 40))  # точний запит по відомих районах
        if dist.get("admin") or not ids_known:
            queries.append(([], 60 if deep else 30))          # розвідка по місту

    total, ordered, kinds = 0, [], {}
    for ids, limit in queries:
        data = get(SEARCH, params=search_params(s, usd_uah, limit=limit, district_ids=ids)).json()
        if ids is None or ids:
            total = max(total, data.get("count") or 0)
        for i in data.get("items") or []:
            if i not in kinds:
                kinds[i] = "exact" if ids else "scan"
                ordered.append(i)
    unseen = [i for i in ordered if f"ria:{i}" not in seen]
    log.info("DIM.RIA %s: ID %d, нових %d, відомих районів %d, всього %d",
             s["id"], len(ordered), len(unseen), len(ids_known), total)

    targets = [(i, True) for i in unseen]
    if backfill:
        targets += [(i, False) for i in ordered if f"ria:{i}" in seen]
    new, refreshed, skipped_ids = [], [], []
    for n, (rid, is_new) in enumerate(targets):
        if n >= cap:
            if is_new:
                skipped_ids.append(f"ria:{rid}")
            continue
        try:
            l, district = _details(rid)
        except Exception as e:
            log.warning("DIM.RIA деталі %s: %s", rid, e)
            continue
        if wanted and kinds.get(rid) != "exact" and not district_match(district, wanted):
            if is_new:
                skipped_ids.append(l.key)
            continue
        if wanted and l.district_id and district:
            learned.setdefault(district, l.district_id)
        (new if is_new else refreshed).append(l)
    if not total:
        total = len(ordered)
    return new, total, "https://dom.ria.com/uk/", skipped_ids, refreshed

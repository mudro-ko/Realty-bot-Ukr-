"""AUTO.RIA: пошуковий API сайту (ID найновіших оголошень) + JSON-картка кожного нового авто."""
from .. import catalog as C
from ..common import Car, get, log

SEARCH = "https://auto.ria.com/api/search/auto"
VIEW = "https://auto.ria.com/demo/bu/searchPage/v2/view/auto/{id}/"
GEAR = {"Автомат": "auto", "Типтронік": "auto", "Робот": "auto", "Варіатор": "auto", "Редуктор": "auto",
        "Ручна / Механіка": "manual"}
DRIVE = {"Повний": "awd", "Передній": "fwd", "Задній": "rwd"}


def params(s, pair=None, size=40, page=0, with_price=True):
    p = [("indexName", "auto"), ("categories.main.id", 1), ("size", size), ("page", page), ("order_by", 7)]
    if pair:
        p += [("marka_id[0]", pair["brand_id"]), ("model_id[0]", pair.get("model_id") or 0)]
    y = s.get("year") or {}
    if y.get("min"):
        p.append(("s_yers[0]", y["min"]))
    if y.get("max"):
        p.append(("po_yers[0]", y["max"]))
    pr = s.get("price_usd") or {}
    if with_price and (pr.get("min") or pr.get("max")):
        p.append(("currency", 1))
        if pr.get("min"):
            p.append(("price_ot", int(pr["min"])))
        if pr.get("max"):
            p.append(("price_do", int(pr["max"])))
    for i, rid in enumerate(s.get("regions") or []):
        p.append((f"state[{i}]", rid))
    i = 0
    for f in s.get("fuels") or []:
        for fid in C.FUELS[f][1]:
            p.append((f"type[{i}]", fid)); i += 1
    i = 0
    for g in ([s["gearbox"]] if s.get("gearbox") else []):
        for gid in C.GEARBOXES[g][1]:
            p.append((f"gearbox[{i}]", gid)); i += 1
    for i, d in enumerate(s.get("drives") or []):
        p.append((f"drive_id[{i}]", C.DRIVES[d][1][0]))
    i = 0
    for b in s.get("bodies") or []:
        for bid in C.BODIES[b][1]:
            p.append((f"body_id[{i}]", bid)); i += 1
    e = s.get("engine") or {}
    if e.get("min"):
        p.append(("engineVolumeFrom", e["min"]))
    if e.get("max"):
        p.append(("engineVolumeTo", e["max"]))
    return p


def to_car(d) -> Car:
    a = d.get("autoData") or {}
    bar = d.get("autoInfoBar") or {}
    st = d.get("stateData") or {}
    ph = d.get("photoData") or {}
    body = C.BODY_FROM_NAME.get((d.get("subCategoryName") or "").split(" /")[0].strip().lower(), "")
    return Car(
        key=f"ria:{a.get('autoId')}", source="AUTO.RIA",
        url="https://auto.ria.com/uk" + (d.get("linkToView") or f"/auto___{a.get('autoId')}.html"),
        brand=d.get("markName") or "", model=d.get("modelName") or "",
        brand_id=d.get("markId"), model_id=d.get("modelId"), year=a.get("year"),
        price_usd=d.get("USD"), price_uah=d.get("UAH"), mileage=a.get("raceInt"),
        fuel=C.FUEL_FROM_ID.get(a.get("fuelId"), "other" if a.get("fuelId") else ""),
        gearbox=GEAR.get(a.get("gearboxName"), ""), drive=DRIVE.get(a.get("driveName"), ""), body=body,
        city=d.get("locationCityName") or st.get("name") or "", region_id=st.get("stateId"),
        vin=d.get("VIN") or "", generation=a.get("generationName") or "", text=a.get("description") or "",
        photo=ph.get("seoLinkB") or ph.get("seoLinkM") or "", published=(d.get("addDate") or "")[:16],
        customs_ok=(not bar.get("custom")) if "custom" in bar else None,
        damaged=bool(bar.get("damage")) if "damage" in bar else None,
        abroad=bool(bar.get("abroad")) if "abroad" in bar else None,
        dealer=bool((d.get("dealer") or {}).get("id")), ria_id=a.get("autoId"),
    )


def details(rid):
    r = get(VIEW.format(id=rid), params={"lang_id": 4}, pause=0.8, ok404=True)
    if r.status_code == 404:
        return None                  # ще на модерації — спробуємо наступного разу
    return to_car(r.json())


def fetch(s, seen, first_run, backfill=False, cap=None, with_price=True, pages=1):
    """→ (нові, всього, оновлені_бачені, пропущені_ключі)."""
    pairs = s.get("cars") or [None]
    deep = first_run or backfill
    cap = cap or (60 if deep else 25)
    total, ordered = 0, []
    for pair in pairs[:8]:
        for page in range(pages):
            data = get(SEARCH, params=params(s, pair, size=60 if deep else 40, page=page, with_price=with_price)).json()
            sr = (data.get("result") or {}).get("search_result") or {}
            if page == 0:
                total += sr.get("count") or 0
            for i in sr.get("ids") or []:
                if i not in ordered:
                    ordered.append(i)
    unseen = [i for i in ordered if f"ria:{i}" not in seen]
    targets = [(i, True) for i in unseen] + ([(i, False) for i in ordered if f"ria:{i}" in seen] if backfill else [])
    log.info("AUTO.RIA %s: ID %d, нових %d, всього %d", s.get("id"), len(ordered), len(unseen), total)
    new, refreshed, skipped = [], [], []
    for n, (rid, is_new) in enumerate(targets):
        if n >= cap:
            if is_new and first_run:
                skipped.append(f"ria:{rid}")
            continue
        try:
            c = details(rid)
        except Exception as e:
            log.warning("AUTO.RIA деталі %s: %s", rid, e)
            continue
        if c is None:
            continue
        (new if is_new else refreshed).append(c)
    return new, total, refreshed, skipped

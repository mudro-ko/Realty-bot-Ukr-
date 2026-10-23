"""Одна перевірка пошуку авто: джерела → фільтри → ринок → дедуплікація → архів → повідомлення."""
import time
from datetime import datetime, timedelta, timezone

from .caption import esc, money
from .common import fingerprint, log
from .filters import passes
from .sources import autoria, tgchan

KYIV = timezone(timedelta(hours=3))
KEEP_DAYS = 120
SOURCES = {"autoria": autoria, "tg": tgchan}


def _prune(d):
    cutoff = (datetime.now(KYIV) - timedelta(days=KEEP_DAYS)).strftime("%Y-%m-%d")
    for k in [k for k, v in d.items() if v < cutoff]:
        del d[k]


def run_search(s, st, out, settings, report, archive=None, market=None):
    first = not st.get("initialized")
    inited = st.setdefault("init_sources", [])
    seen, sent, fps = st.setdefault("seen", {}), st.setdefault("sent", {}), st.setdefault("fps", {})
    errors = st.setdefault("errors", {})
    today = datetime.now(KYIV).strftime("%Y-%m-%d")
    try:
        backfill = archive is not None and archive.count(s["id"]) == 0
    except Exception:
        backfill = False
    rep = {"first_run": first, "sources": {}}
    candidates, refreshed_all, totals = [], [], {}

    for name in s.get("sources") or ["autoria", "tg"]:
        mod = SOURCES[name]
        src_first = name not in inited
        try:
            new, total, refreshed, skipped = mod.fetch(s, seen, src_first, backfill=backfill)
            errors[name] = 0
        except Exception as e:
            errors[name] = errors.get(name, 0) + 1
            log.exception("%s / %s: помилка", s["id"], name)
            rep["sources"][name] = {"error": f"{type(e).__name__}: {e}"[:300], "fails_in_row": errors[name]}
            if errors[name] == 3:
                out.text(f"⚠️ <b>{esc(s['name'])}</b>: джерело {name} не відповідає 3 перевірки поспіль.")
            continue
        if src_first:
            inited.append(name)
        totals[name] = total
        for k in skipped:
            seen[k] = today
        if market is not None:
            try:
                market.add(new + refreshed)
            except Exception:
                log.exception("ринок: запис")
        passed, reasons = 0, {}
        for c in new:
            seen[c.key] = today
            ok, why = passes(c, s)
            if not ok:
                reasons[why] = reasons.get(why, 0) + 1
                continue
            passed += 1
            c.initial = src_first
            candidates.append(c)
        refreshed_all += [c for c in refreshed if passes(c, s)[0]]
        rep["sources"][name] = {"total": total, "new": len(new), "passed": passed, "filtered": reasons, "first_run": src_first}

    # ринок: раз на добу — вибірка обраних моделей без фільтра ціни; оцінка кожного кандидата
    bm = s.get("below_market")
    if market is not None:
        if s.get("cars") and time.time() - st.get("market_scan", 0) > 20 * 3600:
            try:
                got, _, ref, _ = autoria.fetch(s, {}, False, cap=50, with_price=False)
                rep["market_scan"] = market.add(got + ref)
                st["market_scan"] = time.time()
            except Exception:
                log.exception("%s: ринкова вибірка", s["id"])
        for c in candidates + refreshed_all:
            try:
                market.evaluate(c)
            except Exception:
                log.exception("ринок: оцінка")
        if bm is not None:
            keep, cut, nodata = [], 0, 0
            for c in candidates:
                if c.market_pct is None:
                    nodata += 1
                elif c.market_pct <= -max(1, int(bm)):
                    keep.append(c)
                else:
                    cut += 1
            candidates = keep
            rep["market_filter"] = {"нижче ринку": len(keep), "не дешевше": cut, "мало даних": nodata}

    # дедуплікація: те саме авто з AUTO.RIA і з каналу (VIN, посилання на AUTO.RIA, або збіг параметрів)
    fresh, uniq, run_fps = [], [], set()
    for c in sorted(candidates, key=lambda x: x.source != "AUTO.RIA"):
        fp = fingerprint(c)
        keys = {c.key, f"fp:{fp}" if fp else None, f"ria:{c.ria_id}" if c.ria_id else None}
        keys.discard(None)
        if (fp and fp in run_fps) or (c.ria_id and f"ria:{c.ria_id}" in run_fps):
            continue                                   # дубль у межах цієї перевірки
        run_fps.update(k for k in (fp, f"ria:{c.ria_id}" if c.ria_id else None) if k)
        uniq.append(c)                                 # в архів — навіть якщо вже надсилали
        if any(k in sent for k in keys) or (fp and fp in fps):
            continue
        if fp:
            fps[fp] = today
        if c.ria_id:
            fps[f"ria:{c.ria_id}"] = today
        fresh.append(c)
    fresh.sort(key=lambda x: x.published or "", reverse=True)

    drops = []
    if archive is not None:
        try:
            drops = archive.record(s["id"], uniq + refreshed_all)
        except Exception:
            log.exception("%s: архів", s["id"])

    initial = [c for c in fresh if c.initial][: int(settings.get("first_run_send", 10))]
    really_new = [c for c in fresh if not c.initial]
    limit = int(settings.get("max_per_search", 15))
    quiet = bool(st.get("quiet_initial")) and first
    if quiet:
        initial = []
    if first and totals:
        st.pop("quiet_initial", None)
        n_init = len([c for c in fresh if c.initial])
        parts = [f"{'AUTO.RIA' if k == 'autoria' else 'Telegram-канали'}: {v}" for k, v in totals.items()]
        out.text(f"🔎 Запустив пошук «<b>{esc(s['name'])}</b>».\n"
                 f"На сайтах зараз: {'; '.join(parts)}.\n"
                 f"Переглянув найновіші, фільтри пройшли <b>{n_init}</b>. "
                 + (f"Надсилаю {len(initial)} найсвіжіших. " if initial else "")
                 + "Усі — у «📊 Звіт». Далі пишу лише про нові.")
    rep["to_send"] = len(initial) + min(limit, len(really_new))
    for c in initial + really_new[:limit]:
        out.car(c, s["name"])
        sent[c.key] = today
        fp = fingerprint(c)
        if fp:
            sent[f"fp:{fp}"] = today
    for c, old in drops[:10]:
        out.text(f"📉 <b>Ціна знизилась</b> · {esc(s['name'])}\n{money(old)} → <b>{money(c.price_usd)}</b>\n"
                 f"{esc(c.title)} · {c.mileage if c.mileage is not None else '?'} тис. км\n"
                 f'<a href="{esc(c.url)}">відкрити</a>')
    if len(really_new) > limit:
        out.text(f"…і ще {len(really_new) - limit} нових у «{esc(s['name'])}» — у «📊 Звіт».")
    if totals:
        st["initialized"] = True
    _prune(seen); _prune(sent); _prune(fps)
    report[s["id"]] = rep

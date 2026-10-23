"""Монітор нерухомості: один запуск = перевірити всі пошуки, надіслати нове в Telegram.

Запуск:  python -m monitor
Змінні середовища:
  TELEGRAM_BOT_TOKEN  — токен бота від @BotFather (без нього — «сухий» режим)
  TELEGRAM_CHAT_ID    — куди слати (необов'язково: візьметься з того, хто написав боту /start)
  SEARCHES_YAML       — вміст конфігу пошуків (необов'язково, інакше файл searches.yaml)
  STATE_DIR           — де лежить стан (за замовчуванням ./state)
  ENABLE_ALL=1        — увімкнути всі пошуки, навіть вимкнені (для тестів)
"""
import copy
import logging
import os
import sys
import time
from datetime import datetime, timedelta, timezone

import yaml

from .common import fingerprint, log
from .filters import micro_from_geo, passes, resolve_district
from .sources import dimria, lun
from .telegram import Telegram, dump_json, esc, outbox_markdown

SOURCES = {"lun": lun, "dimria": dimria}
KYIV = timezone(timedelta(hours=3))
KEEP_DAYS = 120


def now_iso():
    return datetime.now(KYIV).strftime("%Y-%m-%d %H:%M")


def load_config():
    raw = os.environ.get("SEARCHES_YAML") or open("searches.yaml", encoding="utf-8").read()
    cfg = yaml.safe_load(raw) or {}
    cfg.setdefault("settings", {})
    return cfg


def load_state(path):
    if os.path.exists(path):
        import json
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    return {"version": 1, "searches": {}}


def prune(d: dict):
    cutoff = (datetime.now(KYIV) - timedelta(days=KEEP_DAYS)).strftime("%Y-%m-%d")
    for k in [k for k, v in d.items() if v < cutoff]:
        del d[k]


def run_search(s, st, tg, settings, report, archive=None, market=None):
    usd_uah = float(settings.get("usd_uah", 41.5))
    first = not st.get("initialized")          # перший запуск пошуку взагалі
    inited = st.setdefault("init_sources", [])  # джерела, які вже хоч раз відпрацювали
    seen, fps = st.setdefault("seen", {}), st.setdefault("fps", {})
    groups = st.setdefault("groups", {})
    s["_district"] = resolve_district(s)
    learned = st.setdefault("learned_micro", [])
    if s["_district"].get("admin"):
        s["_district"]["names"] += learned
    if s["_district"]:
        s["_district"]["ria_learned"] = st.setdefault("ria_learned", {})   # назва мікрорайону → ID на DIM.RIA
        s["_district"]["ria_zhk_learned"] = st.setdefault("ria_zhk_learned", {})   # назва ЖК → newbuild_id
    sent = st.setdefault("sent", {})
    today = datetime.now(KYIV).strftime("%Y-%m-%d")

    candidates, totals, links, rep = [], {}, {}, {"first_run": first, "sources": {}}
    refreshed_all = []
    errors = st.setdefault("errors", {})
    try:
        backfill = archive is not None and archive.count(s["id"]) == 0
    except Exception:
        backfill = False
    order = sorted(s.get("sources") or ["lun", "dimria"], key=lambda x: x != "lun")   # ЛУН першим
    for name in order:
        mod = SOURCES[name]
        src_first = name not in inited
        try:
            new, total, link, also_seen, refreshed = mod.fetch(s, seen, usd_uah, src_first, backfill=backfill)
            errors[name] = 0
        except Exception as e:
            errors[name] = errors.get(name, 0) + 1
            log.exception("%s / %s: помилка", s["id"], name)
            rep["sources"][name] = {"error": f"{type(e).__name__}: {e}"[:400], "fails_in_row": errors[name]}
            if errors[name] == 3:
                tg.text(f"⚠️ <b>{esc(s['name'])}</b>: джерело {name} не відповідає вже 3 запуски поспіль. "
                        f"Можливо, змінилась структура сайту.")
            continue
        if src_first:
            inited.append(name)
        if name == "lun" and s["_district"].get("admin"):
            for l in new + refreshed:
                m = micro_from_geo(l.geo, s["city"])
                if m and m not in learned and len(learned) < 80:
                    learned.append(m)
                    s["_district"]["names"].append(m)
        totals[name], links[name] = total, link
        for k in also_seen:
            seen[k] = today
        passed, reasons = 0, {}
        for l in new:
            seen[l.key] = today
            ok, why = passes(l, s)
            if not ok:
                reasons[why] = reasons.get(why, 0) + 1
                continue
            passed += 1
            l.initial = src_first
            candidates.append(l)
        if market is not None:
            try:
                market.add(new + refreshed, s["city"], s["deal"], usd_uah)
            except Exception:
                log.exception("ринок: не вдалося записати")
        refreshed_all += [l for l in refreshed if passes(l, s)[0]]
        rep["sources"][name] = {"total": total, "new": len(new), "passed": passed,
                                "filtered": reasons, "first_run": src_first}

    # ринок: окрема вибірка без фільтрів ціни (раз на добу) + оцінка кожного кандидата
    bm = s.get("below_market")
    if market is not None and s["deal"] in ("sale", "rent"):
        if bm is not None and time.time() - st.get("market_scan", 0) > 20 * 3600:
            try:
                scope = {k: v for k, v in s.items() if k not in ("price_usd", "price_uah", "area", "floors", "rooms")}
                n_scan = 0
                for name, mod in (("lun", lun),):
                    deep = 4 if (s.get("_district") or {}).get("zhk") else 8
                    got, _, _, _, ref = mod.fetch(scope, {}, usd_uah, False, max_pages=deep)
                    n_scan += market.add(got + ref, s["city"], s["deal"], usd_uah)
                st["market_scan"] = time.time()
                rep["market_scan"] = n_scan
            except Exception:
                log.exception("%s: ринкова вибірка не вдалася", s["id"])
        for l in candidates + refreshed_all:
            try:
                market.evaluate(l, s["city"], s["deal"], usd_uah)
            except Exception:
                log.exception("ринок: оцінка")
        if bm is not None:
            keep, cut, nodata = [], 0, 0
            for l in candidates:
                if l.market_pct is None:
                    nodata += 1
                elif l.market_pct <= -max(1, int(bm)):
                    keep.append(l)
                else:
                    cut += 1
            candidates = keep
            rep["market_filter"] = {"нижче ринку": len(keep), "не дешевше": cut, "мало даних": nodata}

    # дедуплікація: той самий об'єкт з різних сайтів / повторні публікації
    fresh = []
    for l in candidates:
        fp = fingerprint(l)
        if l.key in sent or (fp and f"fp:{fp}" in sent) or any(k in groups for k in l.extra_keys) or (fp and fp in fps):
            continue
        if fp:
            fps[fp] = today
        for k in l.extra_keys:
            groups[k] = today
        fresh.append(l)
    fresh.sort(key=lambda x: x.published or "", reverse=True)

    # архів + історія цін (лише в боті; GitHub-монітор працює без архіву)
    drops = []
    if archive is not None:
        try:
            drops = archive.record(s["id"], fresh + refreshed_all)
            rep["archived"] = len(fresh) + len(refreshed_all)
        except Exception:
            log.exception("%s: не вдалося записати архів", s["id"])

    # «стартові» (з джерел, що запустились уперше) — лише кілька найновіших; справді нові — всі до ліміту
    initial = [l for l in fresh if l.initial][: int(settings.get("first_run_send", 10))]
    really_new = [l for l in fresh if not l.initial]
    limit = int(settings.get("max_per_search", 15))
    rep["to_send"] = len(initial) + min(limit, len(really_new))

    quiet = bool(st.get("quiet_initial")) and first
    if quiet:
        initial = []
        rep["to_send"] = min(limit, len(really_new))
    if first and totals:
        st.pop("quiet_initial", None)
    if quiet and totals:
        n_init = len([l for l in fresh if l.initial])
        tg.text(f"♻️ Оновив пошук «<b>{esc(s['name'])}</b>» новою логікою: переглянув заново глибше, "
                f"твої фільтри пройшли <b>{n_init}</b> оголошень — усі в «📊 Звіт». Далі пишу лише про нові.")
    elif first and (totals or initial):
        n_init = len([l for l in fresh if l.initial])
        parts = []
        if "lun" in totals:
            parts.append(f"ЛУН: {totals['lun']} за кімнатами/ціною/районом")
        if totals.get("dimria"):
            parts.append(f"DIM.RIA: {totals['dimria']}")
        archive_note = " Усі — у «📊 Звіт»." if archive is not None else ""
        tg.text(f"🔎 Запустив моніторинг «<b>{esc(s['name'])}</b>».\n"
                f"На сайтах зараз: {'; '.join(parts) or '—'}.\n"
                f"Переглянув найновіші, твої фільтри пройшли <b>{n_init}</b>. "
                f"Надсилаю {len(initial)} найсвіжіших.{archive_note} Далі пишу лише про нові.")
    for l in initial + really_new[:limit]:
        tg.listing(l, s["name"])
        sent[l.key] = today
        if fingerprint(l):
            sent[f"fp:{fingerprint(l)}"] = today
    for l, old_usd, old_uah in drops[:10]:
        if l.currency == "uah":
            was, now_ = f"₴{old_uah:,}", f"₴{l.price_uah:,}"
        else:
            was, now_ = f"${old_usd:,}", f"${l.price_usd:,}"
        tg.text(f"📉 <b>Ціна знизилась</b> · {esc(s['name'])}\n"
                f"{was.replace(',', ' ')} → <b>{now_.replace(',', ' ')}</b>\n"
                f"{esc(l.geo or l.street)} · {l.rooms or '?'}к · {l.area or '?'} м²\n"
                f'<a href="{esc(l.url)}">відкрити на {esc(l.origin)}</a>')
    if len(really_new) > limit:
        tg.text(f"…і ще {len(really_new) - limit} нових у «{esc(s['name'])}». "
                + " ".join(f'<a href="{esc(u)}">{k}</a>' for k, u in links.items() if u))

    if totals:
        st["initialized"] = True
    st["last_run"] = now_iso()
    prune(seen); prune(fps); prune(groups); prune(sent)
    report[s["id"]] = rep


def main():
    logging.basicConfig(format="%(asctime)s %(levelname)s %(message)s", level=logging.INFO, stream=sys.stdout)
    state_dir = os.environ.get("STATE_DIR", "state")
    os.makedirs(state_dir, exist_ok=True)
    state_path = os.path.join(state_dir, "seen.json")
    cfg, state = load_config(), load_state(os.path.join(state_dir, "seen.json"))
    settings = cfg["settings"]

    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "").strip() or state.get("chat_id")
    outbox = []
    tg = Telegram(token, chat_id, outbox)
    if token and not chat_id:
        chat_id = tg.discover_chat_id()
        if chat_id:
            tg.chat_id = chat_id
            state["chat_id"] = chat_id
            tg.text("✅ Бот підключено. Сюди приходитимуть нові оголошення.")
        else:
            log.warning("Немає chat_id: напишіть своєму боту /start у Telegram і запустіть ще раз.")
    log.info("Режим: %s", "СУХИЙ (без Telegram)" if tg.dry else "Telegram")

    enable_all = os.environ.get("ENABLE_ALL") == "1"
    report = {"time": now_iso(), "dry_run": tg.dry, "searches": {}}
    if tg.dry and token and not chat_id:
        report["note"] = "токен є, але chat_id не знайдено — напишіть боту /start"
    for s in cfg.get("searches") or []:
        if not (s.get("enabled", True) or enable_all):
            continue
        st = state["searches"].setdefault(s["id"], {})
        if tg.dry and not enable_all:
            # у сухому режимі не позначаємо «бачене», щоб перший справжній запуск показав свіже
            st = copy.deepcopy(st)
        t0 = time.time()
        run_search(s, st, tg, settings, report["searches"])
        report["searches"][s["id"]]["seconds"] = round(time.time() - t0, 1)

    import json
    with open(state_path, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False)
    with open(os.path.join(state_dir, "last_run.json"), "w", encoding="utf-8") as f:
        f.write(dump_json(report))
    if tg.dry:
        with open(os.path.join(state_dir, "outbox.md"), "w", encoding="utf-8") as f:
            f.write(outbox_markdown(outbox))
    log.info("Готово: %s", dump_json(report))


if __name__ == "__main__":
    main()

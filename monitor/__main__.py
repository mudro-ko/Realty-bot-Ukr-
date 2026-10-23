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
from .filters import passes, resolve_district
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


def run_search(s, st, tg, settings, report):
    usd_uah = float(settings.get("usd_uah", 41.5))
    first = not st.get("initialized")
    seen, fps = st.setdefault("seen", {}), st.setdefault("fps", {})
    s["_district"] = resolve_district(s)
    today = datetime.now(KYIV).strftime("%Y-%m-%d")

    candidates, totals, links, rep = [], {}, {}, {"first_run": first, "sources": {}}
    errors = st.setdefault("errors", {})
    for name in s.get("sources") or ["lun", "dimria"]:
        mod = SOURCES[name]
        try:
            new, total, link, also_seen = mod.fetch(s, seen, usd_uah, first)
            errors[name] = 0
        except Exception as e:
            errors[name] = errors.get(name, 0) + 1
            log.exception("%s / %s: помилка", s["id"], name)
            rep["sources"][name] = {"error": f"{type(e).__name__}: {e}"[:300], "fails_in_row": errors[name]}
            if errors[name] == 3:
                tg.text(f"⚠️ <b>{esc(s['name'])}</b>: джерело {name} не відповідає вже 3 запуски поспіль. "
                        f"Можливо, змінилась структура сайту.")
            continue
        totals[name], links[name] = total, link
        for k in also_seen:
            seen[k] = today
        passed = 0
        reasons = {}
        for l in new:
            seen[l.key] = today
            ok, why = passes(l, s)
            if not ok:
                reasons[why] = reasons.get(why, 0) + 1
                continue
            passed += 1
            candidates.append(l)
        rep["sources"][name] = {"total": total, "new": len(new), "passed": passed, "filtered": reasons}

    # дедуплікація: той самий об'єкт з різних сайтів / повторні публікації
    fresh = []
    for l in candidates:
        fp = fingerprint(l)
        if any(k in st.get("groups", {}) for k in l.extra_keys) or (fp and fp in fps):
            continue
        if fp:
            fps[fp] = today
        for k in l.extra_keys:
            st.setdefault("groups", {})[k] = today
        fresh.append(l)
    fresh.sort(key=lambda x: x.published or "", reverse=True)
    rep["to_send"] = len(fresh)

    limit = int(settings.get("first_run_send", 10)) if first else int(settings.get("max_per_search", 15))
    if first:
        tot = " + ".join(f"{k}: {v}" for k, v in totals.items())
        tg.text(f"🔎 Запустив моніторинг «<b>{esc(s['name'])}</b>».\n"
                f"Актуальних зараз: {tot or '—'} (до фільтрів).\n"
                f"Показую {min(limit, len(fresh))} найновіших, що пройшли фільтри; далі пишу лише про нові.")
    for l in fresh[:limit]:
        tg.listing(l, s["name"])
    if len(fresh) > limit:
        tg.text(f"…і ще {len(fresh) - limit} нових у «{esc(s['name'])}». "
                + " ".join(f'<a href="{esc(u)}">{k}</a>' for k, u in links.items() if u))

    st["initialized"] = True
    st["last_run"] = now_iso()
    prune(seen); prune(fps); prune(st.get("groups", {}))
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

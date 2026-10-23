"""Відправка в Telegram. Без токена працює «сухий» режим: повідомлення пишуться у файл."""
import html
import json
import time

import requests

from .common import Listing, log
from .filters import CONDITION_LABEL, condition_of

API = "https://api.telegram.org/bot{token}/{method}"


def esc(s) -> str:
    return html.escape(str(s or ""), quote=False)


def fmt_money(v, sym):
    return f"{sym}{v:,}".replace(",", " ") if v else "—"


def listing_caption(l: Listing, search_name: str) -> str:
    if l.currency == "uah":
        price = fmt_money(l.price_uah, "₴")
    else:
        price = fmt_money(l.price_usd, "$")
    per_m = f" · {fmt_money(round(l.price_usd / l.area), '$')}/м²" if l.price_usd and l.area and l.currency != "uah" else ""
    parts = [f"🆕 <b>{esc(search_name)}</b>", f"💰 <b>{price}</b>{per_m}"]
    line = []
    if l.rooms:
        line.append(f"{l.rooms}к")
    if l.area:
        line.append(f"{l.area:g} м²")
    if l.floor:
        line.append(f"пов. {l.floor}/{l.floors}" if l.floors else f"пов. {l.floor}")
    if line:
        parts.append("📐 " + " · ".join(line))
    if l.geo:
        parts.append(f"📍 {esc(l.geo)}")
    tags = [CONDITION_LABEL.get(condition_of(l), "")]
    if l.is_owner:
        tags.append("👤 власник")
    if l.without_commission:
        tags.append("без комісії")
    tags = [t for t in tags if t]
    if tags:
        parts.append(" · ".join(tags))
    src = l.source if l.origin in ("dom.ria.com", "lun.ua") else f"{l.origin} через {l.source}"
    footer = f"🔗 {esc(src)}" + (f" · {esc(l.published)}" if l.published else "")
    head = "\n".join(parts)
    txt = " ".join((l.text or "").split())
    room = 1000 - len(head) - len(footer) - 20      # підпис до фото в Telegram ≤ 1024 символів
    if txt and room > 60:
        cut = txt[:min(280, room)]
        while len(esc(cut)) > room:
            cut = cut[:-20]
        head += f"\n<i>{esc(cut)}{'…' if len(cut) < len(txt) else ''}</i>"
    return f"{head}\n{footer}"


class Telegram:
    def __init__(self, token: str, chat_id: str, outbox: list):
        self.token, self.chat_id, self.outbox = token, chat_id, outbox
        self.sent = 0

    @property
    def dry(self):
        return not (self.token and self.chat_id)

    def _call(self, method, payload, retries=3):
        for _ in range(retries):
            r = requests.post(API.format(token=self.token, method=method), json=payload, timeout=30)
            if r.status_code == 429:
                wait = (r.json().get("parameters") or {}).get("retry_after", 5)
                time.sleep(wait + 1)
                continue
            data = r.json()
            if not data.get("ok"):
                raise RuntimeError(f"Telegram {method}: {data.get('description')}")
            return data["result"]
        raise RuntimeError(f"Telegram {method}: too many retries")

    def discover_chat_id(self):
        """Якщо TELEGRAM_CHAT_ID не задано — беремо чат того, хто написав боту /start."""
        if not self.token:
            return None
        try:
            ups = self._call("getUpdates", {"limit": 50, "timeout": 0})
        except Exception as e:
            log.warning("getUpdates: %s", e)
            return None
        for u in reversed(ups):
            msg = u.get("message") or u.get("edited_message") or {}
            chat = msg.get("chat") or {}
            if chat.get("type") == "private":
                return str(chat["id"])
        return None

    def text(self, text: str):
        self.outbox.append({"type": "text", "text": text})
        if self.dry:
            return
        self._call("sendMessage", {"chat_id": self.chat_id, "text": text, "parse_mode": "HTML",
                                   "disable_web_page_preview": True})
        self.sent += 1
        time.sleep(1.1)

    def listing(self, l: Listing, search_name: str):
        cap = listing_caption(l, search_name)
        btn = {"inline_keyboard": [[{"text": f"Відкрити на {l.origin}", "url": l.url}]]}
        self.outbox.append({"type": "listing", "caption": cap, "url": l.url, "photo": l.photo})
        if self.dry:
            return
        try:
            if l.photo:
                self._call("sendPhoto", {"chat_id": self.chat_id, "photo": l.photo, "caption": cap,
                                         "parse_mode": "HTML", "reply_markup": btn})
            else:
                raise RuntimeError("no photo")
        except Exception:
            self._call("sendMessage", {"chat_id": self.chat_id, "text": cap, "parse_mode": "HTML",
                                       "reply_markup": btn, "disable_web_page_preview": True})
        self.sent += 1
        time.sleep(1.1)


def outbox_markdown(outbox: list) -> str:
    out = []
    for m in outbox:
        if m["type"] == "text":
            out.append("### ✉️ " + m["text"].replace("\n", "  \n"))
        else:
            out.append(m["caption"].replace("\n", "  \n") + f"  \n[посилання]({m['url']})" +
                       (f" · [фото]({m['photo']})" if m.get("photo") else ""))
        out.append("\n---\n")
    return "\n".join(out)


def dump_json(obj) -> str:
    return json.dumps(obj, ensure_ascii=False, indent=1)

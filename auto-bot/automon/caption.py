"""Текст повідомлення про авто і збирач повідомлень (бот сам їх надсилає)."""
import html

FUEL_UA = {"petrol": "бензин", "diesel": "дизель", "lpg": "газ/бензин", "hybrid": "гібрид", "phev": "плагін-гібрид",
           "electric": "електро", "other": "інше"}
GEAR_UA = {"auto": "автомат", "manual": "механіка"}
DRIVE_UA = {"awd": "повний", "fwd": "передній", "rwd": "задній"}


def esc(s):
    return html.escape(str(s or ""), quote=False)


def money(v):
    return f"${v:,}".replace(",", " ") if v else "—"


def car_caption(c, search_name: str) -> str:
    parts = [f"🆕 <b>{esc(search_name)}</b>", f"🚗 <b>{esc(c.title) or 'Авто'}</b>" + (f" · {esc(c.generation)}" if c.generation else "")]
    parts.append(f"💰 <b>{money(c.price_usd)}</b>")
    tech = []
    if c.mileage is not None:
        tech.append(f"{c.mileage} тис. км")
    if c.engine:
        tech.append(f"{c.engine:g} л")
    if c.fuel:
        tech.append(FUEL_UA.get(c.fuel, c.fuel))
    if c.gearbox:
        tech.append(GEAR_UA[c.gearbox])
    if c.drive:
        tech.append(f"{DRIVE_UA[c.drive]} привід")
    if tech:
        parts.append("⚙️ " + " · ".join(tech))
    if c.city:
        parts.append(f"📍 {esc(c.city)}")
    flags = []
    if c.customs_ok is False:
        flags.append("🛃 не розмитнений")
    if c.damaged:
        flags.append("💥 після ДТП")
    if c.abroad:
        flags.append("🌍 не в Україні")
    if c.vin:
        flags.append(f"VIN {esc(c.vin)}")
    if flags:
        parts.append(" · ".join(flags))
    if c.market_pct is not None and c.market_ref:
        p = c.market_pct
        if p <= -35:
            tag = f"⚠️ <b>на {-p}% дешевше ринку</b> — підозріло дешево, перевір"
        elif p <= -10:
            tag = f"📉 <b>на {-p}% дешевше ринку</b>"
        elif p < 0:
            tag = f"📉 на {-p}% дешевше ринку"
        elif p == 0:
            tag = "≈ на рівні ринку"
        else:
            tag = f"📈 на {p}% дорожче ринку"
        mil = ""
        if c.market_mileage and c.mileage is not None:
            mil = f", пробіг у них ~{round(c.market_mileage)} тис."
        parts.append(f"{tag} · медіана {esc(c.market_label)}: {money(round(c.market_ref))} ({c.market_n}{mil})")
    footer = f"🔗 {esc(c.source)}" + (f" · {esc(c.published)}" if c.published else "")
    head = "\n".join(parts)
    txt = " ".join((c.text or "").split())
    room = 1000 - len(head) - len(footer) - 20
    if txt and room > 60:
        cut = txt[:min(260 if c.source == "AUTO.RIA" else 380, room)]
        while len(esc(cut)) > room:
            cut = cut[:-20]
        head += f"\n<i>{esc(cut)}{'…' if len(cut) < len(txt) else ''}</i>"
    return f"{head}\n{footer}"


class Collector:
    def __init__(self):
        self.outbox = []

    def text(self, t):
        self.outbox.append({"type": "text", "text": t})

    def car(self, c, search_name):
        self.outbox.append({"type": "listing", "caption": car_caption(c, search_name), "url": c.url, "photo": c.photo,
                            "origin": "AUTO.RIA" if c.source == "AUTO.RIA" else "Telegram"})

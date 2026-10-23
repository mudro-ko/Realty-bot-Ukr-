"""Архів оголошень (SQLite): усе, що пройшло фільтри пошуку, плюс історія цін."""
import os
import sqlite3
import threading
from datetime import datetime, timedelta, timezone

from .common import Listing, fingerprint
from .filters import condition_of

KYIV = timezone(timedelta(hours=3))

SCHEMA = """
CREATE TABLE IF NOT EXISTS listings (
    search_id TEXT NOT NULL,
    key TEXT NOT NULL,
    source TEXT, origin TEXT, url TEXT, photo TEXT,
    price_usd INTEGER, price_uah INTEGER, currency TEXT,
    first_price_usd INTEGER, first_price_uah INTEGER,
    rooms INTEGER, area REAL, floor INTEGER, floors INTEGER,
    street TEXT, geo TEXT, condition TEXT,
    is_owner INTEGER, without_commission INTEGER,
    published TEXT, first_seen TEXT, last_seen TEXT,
    text TEXT, fp TEXT,
    PRIMARY KEY (search_id, key)
);
CREATE TABLE IF NOT EXISTS prices (
    search_id TEXT, key TEXT, seen_at TEXT, price_usd INTEGER, price_uah INTEGER
);
CREATE INDEX IF NOT EXISTS prices_key ON prices(search_id, key);
CREATE INDEX IF NOT EXISTS listings_fp ON listings(search_id, fp);
"""


def _now():
    return datetime.now(KYIV).strftime("%Y-%m-%d %H:%M")


def _b(v):
    return None if v is None else int(bool(v))


class Archive:
    def __init__(self, path):
        self.path = path
        self.lock = threading.Lock()
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with self.connect() as c:
            c.executescript(SCHEMA)

    def connect(self):
        c = sqlite3.connect(self.path, timeout=30)
        c.row_factory = sqlite3.Row
        return c

    def count(self, search_id) -> int:
        with self.connect() as c:
            return c.execute("SELECT COUNT(*) FROM listings WHERE search_id=?", (search_id,)).fetchone()[0]

    def record(self, search_id: str, listings: list) -> list:
        """Додає/оновлює оголошення. Повертає [(listing, стара_ціна_$, стара_ціна_₴)] для тих, що подешевшали."""
        drops, now = [], _now()
        with self.lock, self.connect() as c:
            for l in listings:
                row = c.execute("SELECT price_usd, price_uah FROM listings WHERE search_id=? AND key=?",
                                (search_id, l.key)).fetchone()
                if row is None:
                    fp = fingerprint(l)
                    if fp and c.execute("SELECT 1 FROM listings WHERE search_id=? AND fp=?", (search_id, fp)).fetchone():
                        continue      # той самий об'єкт уже є в архіві з іншого сайту
                    c.execute("""INSERT INTO listings VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", (
                        search_id, l.key, l.source, l.origin, l.url, l.photo,
                        l.price_usd, l.price_uah, l.currency, l.price_usd, l.price_uah,
                        l.rooms, l.area, l.floor, l.floors, l.street, l.geo, condition_of(l),
                        _b(l.is_owner), _b(l.without_commission), l.published, now, now, (l.text or "")[:1500], fp))
                    c.execute("INSERT INTO prices VALUES (?,?,?,?,?)", (search_id, l.key, now, l.price_usd, l.price_uah))
                    continue
                old_usd, old_uah = row["price_usd"], row["price_uah"]
                changed = (l.price_usd and old_usd and l.price_usd != old_usd) or \
                          (l.currency == "uah" and l.price_uah and old_uah and l.price_uah != old_uah)
                c.execute("""UPDATE listings SET last_seen=?, price_usd=COALESCE(?,price_usd),
                             price_uah=COALESCE(?,price_uah), url=?, photo=COALESCE(NULLIF(?,''),photo)
                             WHERE search_id=? AND key=?""",
                          (now, l.price_usd, l.price_uah, l.url, l.photo, search_id, l.key))
                if changed:
                    c.execute("INSERT INTO prices VALUES (?,?,?,?,?)", (search_id, l.key, now, l.price_usd, l.price_uah))
                    if l.currency == "uah":
                        if l.price_uah < old_uah * 0.98:
                            drops.append((l, old_usd, old_uah))
                    elif l.price_usd < old_usd * 0.98:
                        drops.append((l, old_usd, old_uah))
        return drops

    def clear(self, search_id):
        with self.lock, self.connect() as c:
            c.execute("DELETE FROM listings WHERE search_id=?", (search_id,))
            c.execute("DELETE FROM prices WHERE search_id=?", (search_id,))

    def rows(self, search_id=None):
        q = "SELECT * FROM listings" + (" WHERE search_id=?" if search_id else "") + " ORDER BY first_seen DESC"
        with self.connect() as c:
            return [dict(r) for r in c.execute(q, (search_id,) if search_id else ())]

    def search_ids(self):
        with self.connect() as c:
            return [r[0] for r in c.execute("SELECT DISTINCT search_id FROM listings")]

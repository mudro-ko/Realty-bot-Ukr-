"""Архів знайдених авто (SQLite) з історією цін."""
import os
import sqlite3
import threading
from datetime import datetime, timedelta, timezone

from .common import fingerprint

KYIV = timezone(timedelta(hours=3))
COLS = ["search_id", "key", "source", "url", "photo", "brand", "model", "year", "price_usd", "first_price_usd",
        "mileage", "fuel", "gearbox", "drive", "body", "engine", "city", "vin", "generation", "customs_ok", "damaged",
        "market_pct", "market_label", "market_ref", "published", "first_seen", "last_seen", "text", "fp"]
SCHEMA = f"""
CREATE TABLE IF NOT EXISTS cars ({", ".join(c + (" TEXT" if c not in ("year", "price_usd", "first_price_usd", "mileage", "market_pct") else " INTEGER") for c in COLS)},
    PRIMARY KEY (search_id, key));
CREATE TABLE IF NOT EXISTS prices (search_id TEXT, key TEXT, seen_at TEXT, price_usd INTEGER);
CREATE INDEX IF NOT EXISTS cars_fp ON cars(search_id, fp);
"""


def _now():
    return datetime.now(KYIV).strftime("%Y-%m-%d %H:%M")


class Archive:
    def __init__(self, path):
        self.path = path
        self.lock = threading.Lock()
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with self._c() as c:
            c.executescript(SCHEMA)

    def _c(self):
        c = sqlite3.connect(self.path, timeout=30)
        c.row_factory = sqlite3.Row
        return c

    def count(self, sid):
        with self._c() as c:
            return c.execute("SELECT COUNT(*) FROM cars WHERE search_id=?", (sid,)).fetchone()[0]

    def clear(self, sid):
        with self.lock, self._c() as c:
            c.execute("DELETE FROM cars WHERE search_id=?", (sid,))
            c.execute("DELETE FROM prices WHERE search_id=?", (sid,))

    def record(self, sid, cars):
        """→ [(car, стара_ціна)] для тих, що подешевшали ≥2%."""
        drops, now = [], _now()
        with self.lock, self._c() as c:
            for car in cars:
                row = c.execute("SELECT price_usd FROM cars WHERE search_id=? AND key=?", (sid, car.key)).fetchone()
                if row is None:
                    fp = fingerprint(car)
                    if fp and c.execute("SELECT 1 FROM cars WHERE search_id=? AND fp=?", (sid, fp)).fetchone():
                        continue
                    vals = {"search_id": sid, "key": car.key, "source": car.source, "url": car.url, "photo": car.photo,
                            "brand": car.brand, "model": car.model, "year": car.year, "price_usd": car.price_usd,
                            "first_price_usd": car.price_usd, "mileage": car.mileage, "fuel": car.fuel, "gearbox": car.gearbox,
                            "drive": car.drive, "body": car.body, "engine": car.engine, "city": car.city, "vin": car.vin,
                            "generation": car.generation, "customs_ok": car.customs_ok, "damaged": car.damaged,
                            "market_pct": car.market_pct, "market_label": car.market_label, "market_ref": car.market_ref,
                            "published": car.published, "first_seen": now, "last_seen": now,
                            "text": (car.text or "")[:1500], "fp": fp}
                    c.execute(f"INSERT INTO cars ({', '.join(COLS)}) VALUES ({', '.join('?' * len(COLS))})",
                              [vals[k] for k in COLS])
                    c.execute("INSERT INTO prices VALUES (?,?,?,?)", (sid, car.key, now, car.price_usd))
                    continue
                old = row["price_usd"]
                c.execute("""UPDATE cars SET last_seen=?, price_usd=COALESCE(?,price_usd),
                             market_pct=COALESCE(?,market_pct), market_label=COALESCE(NULLIF(?,''),market_label)
                             WHERE search_id=? AND key=?""",
                          (now, car.price_usd, car.market_pct, car.market_label, sid, car.key))
                if car.price_usd and old and car.price_usd != old:
                    c.execute("INSERT INTO prices VALUES (?,?,?,?)", (sid, car.key, now, car.price_usd))
                    if car.price_usd < old * 0.98:
                        drops.append((car, old))
        return drops

    def rows(self, sid):
        with self._c() as c:
            return [dict(r) for r in c.execute("SELECT * FROM cars WHERE search_id=? ORDER BY first_seen DESC", (sid,))]

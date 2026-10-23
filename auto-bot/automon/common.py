"""Спільне: HTTP з повторами, модель оголошення авто."""
import logging
import re
import time
from dataclasses import dataclass, field
from typing import Optional

import requests

log = logging.getLogger("automon")

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/129.0 Safari/537.36")
_session: Optional[requests.Session] = None


def http() -> requests.Session:
    global _session
    if _session is None:
        _session = requests.Session()
        _session.headers.update({"User-Agent": UA, "Accept-Language": "uk-UA,uk;q=0.9"})
    return _session


def get(url, params=None, timeout=25, pause=1.0, retries=3, ok404=False):
    """GET з паузою; при обриві, 429 чи 5xx — повтор з дедалі довшою паузою."""
    global _session
    last = None
    for attempt in range(retries + 1):
        try:
            r = http().get(url, params=params, timeout=timeout)
            time.sleep(pause)
            if ok404 and r.status_code == 404:
                return r
            if r.status_code in (429, 500, 502, 503, 504) and attempt < retries:
                raise requests.HTTPError(f"HTTP {r.status_code}", response=r)
            r.raise_for_status()
            return r
        except (requests.ConnectionError, requests.Timeout, requests.HTTPError) as e:
            last = e
            if attempt >= retries or (isinstance(e, requests.HTTPError) and e.response is not None
                                      and e.response.status_code < 429 and e.response.status_code != 408):
                raise
            wait = (5, 15, 40)[min(attempt, 2)]
            log.info("Повтор %s через %dс (%s)", url[:80], wait, type(e).__name__)
            _session = None
            time.sleep(wait)
    raise last


@dataclass
class Car:
    key: str                       # "ria:40167587" / "tg:kievavto2/123"
    source: str                    # "AUTO.RIA" / "@channel"
    url: str
    brand: str = ""
    model: str = ""
    brand_id: Optional[int] = None
    model_id: Optional[int] = None
    year: Optional[int] = None
    price_usd: Optional[int] = None
    price_uah: Optional[int] = None
    mileage: Optional[int] = None  # тис. км
    fuel: str = ""                 # petrol / diesel / lpg / hybrid / phev / electric / other
    gearbox: str = ""              # auto / manual
    drive: str = ""                # awd / fwd / rwd
    body: str = ""
    engine: Optional[float] = None  # л
    city: str = ""
    region_id: Optional[int] = None
    vin: str = ""
    generation: str = ""
    text: str = ""
    photo: str = ""
    published: str = ""
    customs_ok: Optional[bool] = None   # розмитнений
    damaged: Optional[bool] = None      # після ДТП / пошкоджений
    abroad: Optional[bool] = None       # авто не в Україні
    dealer: Optional[bool] = None
    ria_id: Optional[int] = None        # для дедуплікації (пост у каналі з посиланням на AUTO.RIA)
    market_pct: Optional[int] = None
    market_ref: Optional[float] = None
    market_n: int = 0
    market_label: str = ""
    market_mileage: Optional[float] = None
    initial: bool = False
    extra_keys: list = field(default_factory=list)
    wanted: list = field(default_factory=list)          # бажані слова, знайдені в описі
    wanted_miss: list = field(default_factory=list)     # бажані слова, яких в описі немає

    @property
    def title(self):
        return " ".join(x for x in [self.brand, self.model, str(self.year or "")] if x).strip()


def fingerprint(c: Car):
    """Той самий автомобіль з різних джерел: VIN або марка+модель+рік+пробіг+ціна."""
    if c.vin and len(c.vin) >= 11:
        return "vin:" + c.vin.upper()
    if c.ria_id:
        return f"ria:{c.ria_id}"
    if c.brand and c.model and c.year and c.mileage is not None and c.price_usd:
        return f"{c.brand.lower()}|{c.model.lower()}|{c.year}|{round(c.mileage / 5)}|{round(c.price_usd / 500)}"
    return None


def num(s):
    try:
        return int(re.sub(r"[^\d]", "", str(s)))
    except (TypeError, ValueError):
        return None

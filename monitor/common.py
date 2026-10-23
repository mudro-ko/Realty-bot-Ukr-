"""Спільні речі: HTTP-сесія, модель оголошення, нормалізація."""
import re
import time
import logging
from dataclasses import dataclass, field
from typing import Optional

import requests

log = logging.getLogger("monitor")

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/129.0 Safari/537.36")

_session: Optional[requests.Session] = None


def http() -> requests.Session:
    global _session
    if _session is None:
        _session = requests.Session()
        _session.headers.update({"User-Agent": UA, "Accept-Language": "uk-UA,uk;q=0.9"})
    return _session


def get(url, params=None, timeout=25, pause=1.2):
    """GET з паузою між запитами, щоб не навантажувати сайти."""
    r = http().get(url, params=params, timeout=timeout)
    time.sleep(pause)
    r.raise_for_status()
    return r


@dataclass
class Listing:
    key: str                      # унікальний ключ, напр. "lun:123" / "ria:456"
    source: str                   # "ЛУН" / "DIM.RIA"
    origin: str                   # сайт-першоджерело (olx.ua, rieltor.ua, dom.ria.com …)
    url: str
    price_usd: Optional[int] = None
    price_uah: Optional[int] = None
    currency: str = "usd"         # валюта, в якій вказана ціна в оголошенні
    rooms: Optional[int] = None
    area: Optional[float] = None
    floor: Optional[int] = None
    floors: Optional[int] = None
    street: str = ""
    geo: str = ""
    text: str = ""
    photo: str = ""
    published: str = ""           # ISO-подібна дата появи
    without_renovation: Optional[bool] = None
    is_owner: Optional[bool] = None
    without_commission: Optional[bool] = None
    agency: Optional[bool] = None                    # явна позначка ріелтора/агентства
    district_name: str = ""                          # район/мікрорайон як його називає сайт
    district_id: Optional[int] = None                # ID району на сайті (DIM.RIA)
    complex: str = ""                                # ЖК, як його називає сайт
    complex_id: Optional[int] = None                 # ID ЖК на сайті (DIM.RIA newbuild_id)
    extra_keys: list = field(default_factory=list)   # додаткові ключі для дедуплікації (groupId тощо)
    initial: bool = False                            # з першого запуску джерела (стартова підбірка)


STREET_WORDS = r"\b(вул|вулиця|ул|улица|просп|проспект|пр-т|пров|провулок|переулок|пер|бульв|бульвар|б-р|пл|площа|площадь|узвіз|шосе|дорога|набережна|майдан)\b\.?"


def street_token(street: str) -> str:
    s = (street or "").lower()
    s = re.sub(r"\(.*?\)", " ", s)
    s = re.sub(STREET_WORDS, " ", s)
    s = re.sub(r"[^a-zа-яіїєґ' ]", " ", s)
    words = [w for w in s.split() if len(w) > 2]
    return words[-1] if words else ""


def fingerprint(l: Listing) -> Optional[str]:
    """Відбиток квартири, щоб не слати той самий об'єкт з двох сайтів."""
    if not (l.rooms and l.area and l.price_usd):
        return None
    st = street_token(l.street)
    if not st:
        return None
    return f"{l.rooms}|{round(l.area / 2)}|{round(l.price_usd / 1000)}|{st}"

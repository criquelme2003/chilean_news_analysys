import feedparser
import requests
from dataclasses import dataclass
from pathlib import Path
# ========= Classes ===========

@dataclass
class Feed:
    medio: str
    url: str
    orientacion_referencial: str | None = None

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "application/rss+xml, application/xml, text/xml, */*",
}
REQUEST_TIMEOUT = 15  # segundos

FEEDS = [
    Feed("The Clinic", "https://www.theclinic.cl/feed/"),
    Feed("La Nación", "https://www.lanacion.cl/feed/"),
    Feed("Diario Financiero", "https://www.df.cl/noticias/site/list/port/rss.xml", "económico"),
    Feed("BioBioChile", "https://www.biobiochile.cl/static/feed-rss"),
]

FEEDS_DICT = {
  "the_clinic":FEEDS[0],
  "la_nacion":FEEDS[1],
  "diario_financiero":FEEDS[2],
  "biobio_chile":FEEDS[3],
}

DB_PATH = Path(__file__).resolve().parent.parent / "DB/noticias.db"


# ========= Functions ===========
def fetch_feed(feed: Feed) -> feedparser.FeedParserDict | None:
    """Descarga y parsea un feed. Devuelve None si falla."""
    try:
        resp = requests.get(feed.url, headers=HEADERS, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
    except requests.exceptions.RequestException:
        return None

    parsed = feedparser.parse(resp.content)

    if parsed.bozo and not parsed.entries:
        return None

    return parsed

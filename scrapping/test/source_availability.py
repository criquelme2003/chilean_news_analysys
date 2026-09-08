"""
source_availability.py

Smoke test de disponibilidad de las fuentes RSS de medios chilenos.
Hace una única petición por feed y revisa que responda y traiga al menos
una noticia. No persiste nada.

Uso:
    pytest scrapping/test/source_availability.py -v

Dependencias:
    pip install feedparser requests pytest --break-system-packages
"""

from __future__ import annotations

import pytest

from ..shared import FEEDS, Feed, fetch_feed


@pytest.mark.parametrize("feed", FEEDS, ids=[feed.medio for feed in FEEDS])
def test_fuente_disponible(feed: Feed) -> None:
    """El feed debe responder y traer al menos una entrada."""
    parsed = fetch_feed(feed)
    assert parsed is not None, f"No se pudo descargar/parsear el feed de {feed.medio}"
    assert len(parsed.entries) > 0, f"El feed de {feed.medio} no trae ninguna entrada"

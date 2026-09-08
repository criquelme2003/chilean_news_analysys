"""
biobiochile.py

Recorre todas las entradas de BioBioChile guardadas en noticias_raw y
scrapea cada una por su URL (columna "link") para extraer la mayor
cantidad de información posible: cuerpo completo del artículo, autor,
fecha de publicación/modificación, palabras clave, sección e imagen
principal. Guarda el resultado en la tabla noticias_biobiochile,
enlazada a noticias_raw por "link".

A diferencia de The Clinic/La Nación, el JSON-LD de BioBioChile trae el
cuerpo completo del artículo embebido directamente en "articleBody", así
que no hace falta buscar un contenedor HTML aparte.

Uso:
    python3 -m scrapping.modular_sites.biobiochile
"""

from __future__ import annotations

import html
import json
import sqlite3
import time
from datetime import datetime, timezone

import requests
from bs4 import BeautifulSoup

from ..extract_schema import TABLA_RAW
from ..shared import DB_PATH, HEADERS, REQUEST_TIMEOUT

MEDIO = "BioBioChile"
TABLA_DETALLE = "noticias_biobiochile"


def crear_tabla_detalle(conn: sqlite3.Connection) -> None:
    """Crea (si no existe) la tabla con el detalle scrapeado por noticia de BioBioChile."""
    conn.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {TABLA_DETALLE} (
            link TEXT PRIMARY KEY NOT NULL,
            titulo TEXT,
            autor TEXT,
            fecha_publicacion TEXT,
            fecha_modificacion TEXT,
            seccion TEXT,
            keywords TEXT,
            imagen_principal TEXT,
            texto_completo TEXT,
            fecha_scraping TEXT NOT NULL
        )
        """
    )
    conn.commit()


def obtener_links_pendientes(conn: sqlite3.Connection) -> list[str]:
    """Devuelve los links de BioBioChile en noticias_raw que aún no tienen detalle scrapeado."""
    cursor = conn.execute(
        f"""
        SELECT link FROM {TABLA_RAW}
        WHERE medio = ?
          AND link IS NOT NULL
          AND link NOT IN (SELECT link FROM {TABLA_DETALLE})
        """,
        (MEDIO,),
    )
    return [row[0] for row in cursor.fetchall()]


def extraer_json_ld_articulo(soup: BeautifulSoup) -> dict:
    """
    Busca el bloque JSON-LD de tipo Article/NewsArticle (schema.org) que
    BioBioChile inyecta en el <head>, y devuelve sus campos más útiles. Si
    no lo encuentra o no puede parsearlo, devuelve un dict vacío.
    """
    for script in soup.find_all("script", type="application/ld+json"):
        if not script.string:
            continue
        try:
            data = json.loads(script.string)
        except json.JSONDecodeError:
            continue

        nodos = data.get("@graph", [data]) if isinstance(data, dict) else []
        for nodo in nodos:
            if nodo.get("@type") in ("Article", "NewsArticle"):
                return nodo

    return {}


def html_a_texto(fragmento_html: str | None) -> str | None:
    """Convierte el HTML de articleBody a texto plano legible, uniendo párrafos."""
    if not fragmento_html:
        return None

    soup_fragmento = BeautifulSoup(fragmento_html, "lxml")
    parrafos = soup_fragmento.find_all("p")
    if not parrafos:
        return soup_fragmento.get_text(separator=" ", strip=True) or None

    return "\n\n".join(p.get_text(separator=" ", strip=True) for p in parrafos)


def normalizar_keywords(keywords) -> str | None:
    """El JSON-LD de BioBioChile trae keywords como lista; se unen en un string separado por comas."""
    if not keywords:
        return None
    if isinstance(keywords, str):
        return keywords
    return ", ".join(keywords)


def normalizar_seccion(seccion) -> str | None:
    """articleSection en BioBioChile viene como string simple (no lista, a diferencia de otros medios)."""
    if not seccion:
        return None
    if isinstance(seccion, list):
        return ", ".join(seccion)
    return seccion


def scrapear_noticia(link: str) -> dict | None:
    """Descarga y parsea una noticia de BioBioChile. Devuelve None si falla la petición."""
    try:
        resp = requests.get(link, headers=HEADERS, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
    except requests.exceptions.RequestException as exc:
        print(f"  No se pudo descargar {link}: {exc}")
        return None

    soup = BeautifulSoup(resp.content, "lxml")
    articulo = extraer_json_ld_articulo(soup)

    def limpiar(texto: str | None) -> str | None:
        """Decodifica entidades HTML (ej. &#8220;) que vienen crudas en el JSON-LD."""
        return html.unescape(texto) if texto else texto

    return {
        "link": link,
        "titulo": limpiar(articulo.get("headline")),
        "autor": limpiar((articulo.get("author") or {}).get("name")),
        "fecha_publicacion": articulo.get("datePublished"),
        "fecha_modificacion": articulo.get("dateModified"),
        "seccion": limpiar(normalizar_seccion(articulo.get("articleSection"))),
        "keywords": limpiar(normalizar_keywords(articulo.get("keywords"))),
        "imagen_principal": (articulo.get("image") or {}).get("url"),
        "texto_completo": html_a_texto(articulo.get("articleBody")),
        "fecha_scraping": datetime.now(timezone.utc).isoformat(),
    }


def guardar_detalle(conn: sqlite3.Connection, detalle: dict) -> None:
    columnas = list(detalle.keys())
    placeholders = ", ".join("?" for _ in columnas)
    columnas_sql = ", ".join(columnas)
    conn.execute(
        f"INSERT OR REPLACE INTO {TABLA_DETALLE} ({columnas_sql}) VALUES ({placeholders})",
        [detalle[c] for c in columnas],
    )
    conn.commit()


def main() -> None:
    conn = sqlite3.connect(DB_PATH)
    crear_tabla_detalle(conn)

    links = obtener_links_pendientes(conn)
    print(f"{len(links)} noticias de {MEDIO} pendientes de scrapear.")

    exitosas = 0
    for link in links:
        print(f"Scrapeando: {link}")
        detalle = scrapear_noticia(link)
        if detalle is None:
            continue

        guardar_detalle(conn, detalle)
        exitosas += 1
        time.sleep(1)  # pequeña pausa entre requests, buena práctica al scrapear

    conn.close()
    print(f"Listo. {exitosas}/{len(links)} noticias scrapeadas y guardadas en '{TABLA_DETALLE}'.")


if __name__ == "__main__":
    main()

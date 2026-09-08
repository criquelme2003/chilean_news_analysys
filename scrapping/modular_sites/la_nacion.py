"""
la_nacion.py

Recorre todas las entradas de La Nación guardadas en noticias_raw y
scrapea cada una por su URL (columna "link") para extraer la mayor
cantidad de información posible: cuerpo completo del artículo, autor,
fecha de publicación/modificación, palabras clave, sección, cantidad de
palabras e imagen principal. Guarda el resultado en la tabla
noticias_la_nacion, enlazada a noticias_raw por "link".

Uso:
    python3 -m scrapping.modular_sites.la_nacion
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

MEDIO = "La Nación"
TABLA_DETALLE = "noticias_la_nacion"


def crear_tabla_detalle(conn: sqlite3.Connection) -> None:
    """Crea (si no existe) la tabla con el detalle scrapeado por noticia de La Nación."""
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
            word_count INTEGER,
            imagen_principal TEXT,
            texto_completo TEXT,
            fecha_scraping TEXT NOT NULL
        )
        """
    )
    conn.commit()


def obtener_links_pendientes(conn: sqlite3.Connection) -> list[str]:
    """Devuelve los links de La Nación en noticias_raw que aún no tienen detalle scrapeado."""
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
    Busca el bloque JSON-LD de tipo Article (schema.org) que WordPress/Yoast
    inyecta en el <head>, y devuelve sus campos más útiles. Si no lo
    encuentra o no puede parsearlo, devuelve un dict vacío.
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
            if nodo.get("@type") == "Article":
                return nodo

    return {}


def extraer_texto_completo(soup: BeautifulSoup) -> str | None:
    """Extrae el cuerpo del artículo desde el contenedor .td-post-content (tema tagDiv/Newspaper)."""
    contenedor = soup.select_one(".td-post-content")
    if contenedor is None:
        return None

    parrafos = contenedor.find_all("p")
    if not parrafos:
        return contenedor.get_text(separator=" ", strip=True) or None

    return "\n\n".join(p.get_text(separator=" ", strip=True) for p in parrafos)


def extraer_imagen_principal(soup: BeautifulSoup) -> str | None:
    """
    Extrae la imagen principal. La Nación no expone og:image ni "image" en
    el JSON-LD, así que se usa como fallback la primera imagen del artículo.
    """
    meta_og_image = soup.find("meta", property="og:image")
    if meta_og_image and meta_og_image.get("content"):
        return meta_og_image["content"]

    img = soup.select_one("article img") or soup.select_one(".td-post-content img")
    return img.get("src") if img else None


def scrapear_noticia(link: str) -> dict | None:
    """Descarga y parsea una noticia de La Nación. Devuelve None si falla la petición."""
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
        "seccion": limpiar(", ".join(articulo.get("articleSection", [])) or None),
        "keywords": limpiar(", ".join(articulo.get("keywords", [])) or None),
        "word_count": articulo.get("wordCount"),
        "imagen_principal": extraer_imagen_principal(soup),
        "texto_completo": extraer_texto_completo(soup),
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

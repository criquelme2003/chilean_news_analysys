"""
extract_schema.py

Extrae el esquema global de los feeds RSS de medios chilenos (unión de todas
las claves que retorna feedparser en cada entry) y guarda cada entrada del
feed en una tabla SQLite con una columna por cada clave del esquema global,
usando NULL donde un medio no trae ese campo. También genera un JSON con el
detalle del esquema por medio y un CSV comparativo (clave x medio) para
detectar diferencias entre las estructuras que retorna cada sitio.

Uso:
    python3 -m scrapping.extract_schema [salida.json] [salida.csv]
"""

from __future__ import annotations

import csv
import json
import re
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

from .shared import FEEDS, Feed, fetch_feed, DB_PATH

DEFAULT_OUTPUT_JSON = Path(__file__).parent / "feed_schemas.json"
DEFAULT_OUTPUT_CSV = Path(__file__).parent / "feed_schemas_comparacion.csv"


TABLA_RAW = "noticias_raw"

AUSENTE = "ausente"
NULO = "null"
PRESENTE = "presente"

# Significado de cada clave tal como la entrega feedparser (RSS 2.0/Atom),
# basado en los ejemplos reales vistos en feed_schemas.json.
DESCRIPCION_CLAVES = {
    "author": "Nombre del autor de la noticia (texto simple).",
    "author_detail": "Autor en formato estructurado, ej. {'name': ...}.",
    "authors": "Lista de autores estructurados (normalmente un solo elemento, igual a author_detail).",
    "content": "Cuerpo completo de la noticia en HTML (texto extenso, con tags <p>, <img>, etc.).",
    "df_fechap": "Fecha de publicación en formato YYYYMMDD (campo propio de Diario Financiero).",
    "df_fechapl": "Fecha de publicación en texto largo, ej. 'Viernes 4 de septiembre de 2026' (campo propio de DF).",
    "df_fid": "Identificador interno de tipo de contenido usado por DF (ej. 'fid_noticia').",
    "df_foto600": "Ruta relativa a una imagen destacada de 600px (campo propio de DF).",
    "df_horap": "Hora de publicación en formato HH:MM (campo propio de DF).",
    "df_tagnames": "Lista de tags/categorías como string separado por comas (campo propio de DF).",
    "df_ts": "Timestamp interno de publicación en formato YYYYMMDDHHMMSS (campo propio de DF).",
    "guidislink": "Booleano: indica si el GUID del item también es una URL válida.",
    "id": "Identificador único de la noticia (GUID), normalmente una URL.",
    "link": "URL de la noticia.",
    "links": "Lista de enlaces relacionados a la noticia (alternate, etc.), con rel/type/href.",
    "published": "Fecha de publicación en formato RFC 822/2822 (texto), ej. 'Sat, 05 Sep 2026 02:55:32 +0000'.",
    "published_parsed": "Fecha de publicación parseada como struct_time (tupla año, mes, día, hora, min, seg, ...).",
    "summary": "Resumen/extracto de la noticia en HTML (más corto que content).",
    "summary_detail": "Resumen en formato estructurado: {'type', 'language', 'base', 'value'}.",
    "tags": "Lista de categorías/etiquetas de la noticia, cada una como {'term', 'scheme', 'label'}.",
    "title": "Título de la noticia (texto plano).",
    "title_detail": "Título en formato estructurado: {'type', 'language', 'base', 'value'}.",
}


def estado_clave(schema: dict, clave: str) -> str:
    """Determina si una clave está ausente, presente pero null, o presente con valor."""
    if clave not in schema.get("claves", []):
        return AUSENTE
    if schema.get("ejemplo", {}).get(clave) is None:
        return NULO
    return PRESENTE


def generar_csv_comparacion(schemas: list[dict], salida: Path) -> None:
    """
    Genera un CSV con una fila por cada clave distinta vista en cualquier medio,
    y una columna por medio indicando si esa clave viene ausente, null o con valor.
    """
    medios = [schema["medio"] for schema in schemas]

    todas_las_claves: set[str] = set()
    for schema in schemas:
        todas_las_claves.update(schema.get("claves", []))

    with salida.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["clave", "descripcion", *medios])
        for clave in sorted(todas_las_claves):
            descripcion = DESCRIPCION_CLAVES.get(clave, "")
            fila = [estado_clave(schema, clave) for schema in schemas]
            writer.writerow([clave, descripcion, *fila])


def nombre_columna(clave: str) -> str:
    """Convierte una clave de feedparser en un nombre de columna SQL seguro."""
    return re.sub(r"[^a-zA-Z0-9_]", "_", clave)


def asegurar_claves_obligatorias(claves_globales: list[str]) -> list[str]:
    """
    Garantiza que el esquema global incluya siempre "id" (identificador único
    del entry) y "link" (URL de la noticia), ya que la tabla se construye
    enteramente a partir de las claves del esquema, sin columnas aparte.
    """
    claves = set(claves_globales)
    claves.update({"id", "link"})
    return sorted(claves)


def crear_tabla_raw(conn: sqlite3.Connection, claves_globales: list[str]) -> None:
    """
    Crea (si no existe) la tabla noticias_raw con una columna por cada clave
    del esquema global (incluye "id" y "link"), más medio/fecha_recoleccion.
    El resto de las columnas de esquema son NULLABLE porque no todos los
    medios traen todas las claves; "id" es PRIMARY KEY NOT NULL (GUID único
    del entry) y "link" se marca UNIQUE para deduplicar por URL.
    """
    columnas_esquema = ", ".join(
        f'"{nombre_columna(clave)}" TEXT'
        + (" PRIMARY KEY NOT NULL" if clave == "id" else "")
        + (" UNIQUE" if clave == "link" else "")
        for clave in claves_globales
    )
    conn.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {TABLA_RAW} (
            medio TEXT NOT NULL,
            fecha_recoleccion TEXT NOT NULL,
            {columnas_esquema}
        )
        """
    )
    conn.commit()


def serializar_valor(valor) -> str | None:
    """Serializa el valor crudo de feedparser a texto para guardarlo en SQLite."""
    if valor is None:
        return None
    if isinstance(valor, str):
        return valor
    return json.dumps(valor, ensure_ascii=False, default=str)


def guardar_entries_raw(
    conn: sqlite3.Connection,
    feed: Feed,
    entries: list,
    claves_globales: list[str],
) -> int:
    """Inserta cada entry del feed en noticias_raw, con NULL en las claves que no trae. Devuelve cuántas se insertaron."""
    columnas = ["medio", "fecha_recoleccion", *(nombre_columna(c) for c in claves_globales)]
    placeholders = ", ".join("?" for _ in columnas)
    columnas_sql = ", ".join(f'"{c}"' for c in columnas)

    nuevas = 0
    for entry in entries:
        link = (entry.get("link") or "").strip()
        if not link:
            continue
        if not entry.get("id"):
            print(f"  Advertencia: entrada de {feed.medio} sin 'id' (GUID), se omite: {link}")
            continue

        valores = [
            feed.medio,
            datetime.now(timezone.utc).isoformat(),
            *(serializar_valor(entry.get(clave)) for clave in claves_globales),
        ]
        try:
            conn.execute(
                f"INSERT INTO {TABLA_RAW} ({columnas_sql}) VALUES ({placeholders})",
                valores,
            )
            nuevas += 1
        except sqlite3.IntegrityError:
            # ya existe (mismo link) -> no es un error, solo se omite
            continue

    conn.commit()
    return nuevas


def poblar_tabla_raw(schemas: list[dict], feeds_entries: dict[str, list]) -> int:
    """Crea la tabla (si hace falta) y guarda todas las entradas de todos los feeds."""
    claves_vistas: set[str] = set()
    for schema in schemas:
        claves_vistas.update(schema.get("claves", []))
    claves_globales = asegurar_claves_obligatorias(sorted(claves_vistas))

    conn = sqlite3.connect(DB_PATH)
    crear_tabla_raw(conn, claves_globales)

    total_nuevas = 0
    for schema in schemas:
        medio = schema["medio"]
        entries = feeds_entries.get(medio, [])
        feed = next(f for f in FEEDS if f.medio == medio)
        nuevas = guardar_entries_raw(conn, feed, entries, claves_globales)
        print(f"  -> {nuevas} entradas nuevas de {medio} en {TABLA_RAW}")
        total_nuevas += nuevas

    conn.close()
    return total_nuevas


def main() -> None:
    salida_json = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_OUTPUT_JSON
    salida_csv = Path(sys.argv[2]) if len(sys.argv) > 2 else DEFAULT_OUTPUT_CSV

    schemas = []
    feeds_entries: dict[str, list] = {}
    for feed in FEEDS:
        parsed = fetch_feed(feed)
        entries = list(parsed.entries) if parsed is not None else []
        feeds_entries[feed.medio] = entries

        claves: set[str] = set()
        for entry in entries:
            claves.update(entry.keys())
        primer_entry = dict(entries[0]) if entries else {}

        if parsed is None:
            schemas.append({"medio": feed.medio, "url": feed.url, "error": "no se pudo descargar/parsear el feed"})
        else:
            schemas.append({
                "medio": feed.medio,
                "url": feed.url,
                "total_entries": len(entries),
                "claves": sorted(claves),
                "ejemplo": {clave: primer_entry.get(clave) for clave in sorted(claves)},
            })

    salida_json.write_text(
        json.dumps(schemas, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    print(f"Schema de {len(schemas)} feeds guardado en {salida_json}")

    generar_csv_comparacion(schemas, salida_csv)
    print(f"Comparación de claves guardada en {salida_csv}")

    print(f"Guardando entradas en la tabla '{TABLA_RAW}' de {DB_PATH}...")
    total_nuevas = poblar_tabla_raw(schemas, feeds_entries)
    print(f"Listo. {total_nuevas} entradas nuevas guardadas en total.")


if __name__ == "__main__":
    main()

import sqlite3
DB_PATH = "noticias.db"

def init_db(conn: sqlite3.Connection) -> None:
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS noticias (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            medio TEXT NOT NULL,
            orientacion_referencial TEXT,
            titulo TEXT NOT NULL,
            url TEXT NOT NULL UNIQUE,
            resumen TEXT,
            fecha_publicacion TEXT,
            fecha_recoleccion TEXT NOT NULL
        )
        """
    )
    conn.commit()
    print(f"{DB_PATH} INITIALIZED")
    



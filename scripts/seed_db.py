from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import bcrypt

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from british_museum_agent.config import Settings, get_settings  # noqa: E402

_BCRYPT_PREFIXES = ("$2a$", "$2b$", "$2y$")


def main() -> None:
    settings = get_settings()
    db_path = settings.sqlite_path
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(db_path) as conn:
        conn.execute("PRAGMA foreign_keys = ON")
        create_schema(conn)
        seed_data(conn, settings)
    print(f"Base SQLite inicializada -> {db_path}")


def create_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            email TEXT NOT NULL UNIQUE,
            password TEXT NOT NULL,
            role TEXT NOT NULL CHECK(role IN ('staff')),
            active INTEGER NOT NULL DEFAULT 1
        );

        CREATE TABLE IF NOT EXISTS galleries (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            floor TEXT NOT NULL,
            department TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'open',
            accessibility_notes TEXT NOT NULL DEFAULT ''
        );

        CREATE TABLE IF NOT EXISTS tours (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            duration_minutes INTEGER NOT NULL,
            description TEXT NOT NULL,
            gallery_ids TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS incidents (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            gallery_id TEXT NOT NULL,
            category TEXT NOT NULL,
            description TEXT NOT NULL,
            priority TEXT NOT NULL CHECK(priority IN ('low', 'medium', 'high')),
            reported_by TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'open',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(gallery_id) REFERENCES galleries(id)
        );
        """
    )


def seed_data(conn: sqlite3.Connection, settings: Settings) -> None:
    _seed_staff_user(conn, settings)

    conn.executemany(
        """
        INSERT INTO galleries(id, name, floor, department, status, accessibility_notes)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(id) DO NOTHING
        """,
        [
            (
                "room-4",
                "Sala 4 - Escultura egipcia",
                "Planta baja",
                "Egipto y Sudán",
                "open",
                "Galería muy concurrida; verificá la afluencia y la ruta sin escalones "
                "antes de dar una recomendación final.",
            ),
            (
                "rooms-6-10",
                "Salas 6-10 - Galerías asirias",
                "Planta baja",
                "Medio Oriente",
                "open",
                "El recorrido puede combinarse con la Sala 4 para visitar destacados "
                "de la planta baja.",
            ),
            (
                "rooms-61-66",
                "Salas 61-66 - Egipto en la planta superior",
                "Planta superior",
                "Egipto y Sudán",
                "open",
                "Recorrido egipcio en la planta superior; se debe verificar la ruta accesible.",
            ),
            (
                "rooms-42-43-52-59",
                "Salas 42-43 y 52-59 - Medio Oriente",
                "Planta superior",
                "Medio Oriente",
                "open",
                "Usá la ubicación informada para evitar retrocesos innecesarios.",
            ),
        ],
    )

    conn.executemany(
        """
        INSERT INTO tours(id, name, duration_minutes, description, gallery_ids)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(id) DO NOTHING
        """,
        [
            (
                "egypt-45",
                "Destacados de Egipto",
                45,
                "Recorrido concentrado por la Sala 4 y galerías egipcias seleccionadas "
                "de la planta superior.",
                "room-4,rooms-61-66",
            ),
            (
                "middle-east-60",
                "Recorrido por Medio Oriente",
                60,
                "Galerías asirias de la planta baja y salas seleccionadas de Medio Oriente "
                "en la planta superior.",
                "rooms-6-10,rooms-42-43-52-59",
            ),
        ],
    )


def _seed_staff_user(conn: sqlite3.Connection, settings: Settings) -> None:
    username = settings.staff_demo_username.strip()
    row = conn.execute(
        "SELECT password FROM users WHERE email = ?",
        (username,),
    ).fetchone()

    if row is not None and _is_bcrypt_hash(str(row[0])):
        return

    password = settings.staff_demo_password_value
    if password is None:
        action = "migrar la credencial staff existente" if row else "crear el usuario staff"
        raise RuntimeError(
            f"STAFF_DEMO_PASSWORD es obligatorio para {action}; "
            "configuralo antes de ejecutar el seed."
        )

    encoded_password = password.encode("utf-8")
    if len(encoded_password) > 72:
        raise RuntimeError("STAFF_DEMO_PASSWORD no puede superar 72 bytes para bcrypt.")
    password_hash = bcrypt.hashpw(encoded_password, bcrypt.gensalt()).decode("utf-8")

    if row is None:
        conn.execute(
            """
            INSERT INTO users(email, password, role, active)
            VALUES (?, ?, 'staff', 1)
            """,
            (username, password_hash),
        )
        return

    conn.execute(
        "UPDATE users SET password = ? WHERE email = ?",
        (password_hash, username),
    )


def _is_bcrypt_hash(value: str) -> bool:
    return value.startswith(_BCRYPT_PREFIXES)


if __name__ == "__main__":
    main()

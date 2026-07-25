import sqlite3
from pathlib import Path

import bcrypt
import pytest

from british_museum_agent.config import Settings
from british_museum_agent.infrastructure.sqlite_repository import SQLiteRepository
from scripts.seed_db import create_schema, seed_data


def _settings(tmp_path: Path, password: str | None) -> Settings:
    return Settings(
        _env_file=None,
        sqlite_path=tmp_path / "test.db",
        staff_demo_username="staff@example.com",
        staff_demo_password=password,
    )


def _stored_password(conn: sqlite3.Connection) -> str:
    row = conn.execute(
        "SELECT password FROM users WHERE email = 'staff@example.com'"
    ).fetchone()
    assert row is not None
    return str(row[0])


def test_new_database_stores_and_verifies_only_bcrypt(
    tmp_path: Path,
    staff_password: str,
):
    settings = _settings(tmp_path, staff_password)
    with sqlite3.connect(settings.sqlite_path) as conn:
        create_schema(conn)
        seed_data(conn, settings)
        stored = _stored_password(conn)

    assert stored.startswith(("$2a$", "$2b$", "$2y$"))
    assert stored != staff_password
    assert bcrypt.checkpw(staff_password.encode("utf-8"), stored.encode("utf-8"))
    repository = SQLiteRepository(settings.sqlite_path)
    assert repository.validate_staff_credentials("staff@example.com", staff_password) is True
    assert repository.validate_staff_credentials("staff@example.com", "incorrecta") is False


def test_seed_migrates_plaintext_once_and_preserves_existing_bcrypt(
    tmp_path: Path,
    staff_password: str,
):
    settings = _settings(tmp_path, staff_password)
    with sqlite3.connect(settings.sqlite_path) as conn:
        create_schema(conn)
        conn.execute(
            "INSERT INTO users(email, password, role, active) VALUES (?, ?, 'staff', 1)",
            ("staff@example.com", "legacy-plaintext"),
        )
        seed_data(conn, settings)
        migrated_hash = _stored_password(conn)
        seed_data(conn, _settings(tmp_path, "otra-credencial-segura"))
        preserved_hash = _stored_password(conn)

    assert migrated_hash.startswith(("$2a$", "$2b$", "$2y$"))
    assert bcrypt.checkpw(staff_password.encode("utf-8"), migrated_hash.encode("utf-8"))
    assert preserved_hash == migrated_hash


@pytest.mark.parametrize("existing_plaintext", [False, True])
def test_seed_fails_clearly_without_password_for_bootstrap_or_migration(
    tmp_path: Path,
    existing_plaintext: bool,
):
    settings = _settings(tmp_path, None)
    with sqlite3.connect(settings.sqlite_path) as conn:
        create_schema(conn)
        if existing_plaintext:
            conn.execute(
                "INSERT INTO users(email, password, role, active) VALUES (?, ?, 'staff', 1)",
                ("staff@example.com", "legacy-plaintext"),
            )
        with pytest.raises(RuntimeError, match="STAFF_DEMO_PASSWORD es obligatorio"):
            seed_data(conn, settings)


def test_gallery_and_tour_seed_is_insert_only(
    tmp_path: Path,
    staff_password: str,
):
    settings = _settings(tmp_path, staff_password)
    with sqlite3.connect(settings.sqlite_path) as conn:
        create_schema(conn)
        seed_data(conn, settings)
        conn.execute("UPDATE galleries SET status = 'closed' WHERE id = 'room-4'")
        conn.execute(
            "UPDATE tours SET name = 'Recorrido operativo personalizado' WHERE id = 'egypt-45'"
        )

        seed_data(conn, settings)

        gallery_status = conn.execute(
            "SELECT status FROM galleries WHERE id = 'room-4'"
        ).fetchone()[0]
        tour_name = conn.execute(
            "SELECT name FROM tours WHERE id = 'egypt-45'"
        ).fetchone()[0]

    assert gallery_status == "closed"
    assert tour_name == "Recorrido operativo personalizado"

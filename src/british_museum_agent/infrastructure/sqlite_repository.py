from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

import bcrypt

_REQUIRED_TABLES = {"users", "galleries", "tours", "incidents"}


class SQLiteRepository:
    def __init__(self, db_path: Path):
        self.db_path = db_path

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    def is_ready(self) -> bool:
        if not self.db_path.is_file():
            return False
        try:
            with self._connect() as conn:
                tables = {
                    row["name"]
                    for row in conn.execute(
                        "SELECT name FROM sqlite_master WHERE type = 'table'"
                    ).fetchall()
                }
                if not _REQUIRED_TABLES.issubset(tables):
                    return False
                active_staff = conn.execute(
                    "SELECT 1 FROM users WHERE role = 'staff' AND active = 1 LIMIT 1"
                ).fetchone()
                seeded_gallery = conn.execute("SELECT 1 FROM galleries LIMIT 1").fetchone()
                return active_staff is not None and seeded_gallery is not None
        except (OSError, sqlite3.Error):
            return False

    def validate_staff_credentials(self, email: str, password: str) -> bool:
        if not self.db_path.is_file():
            return False
        try:
            with self._connect() as conn:
                row = conn.execute(
                    """
                    SELECT password
                    FROM users
                    WHERE email = ? AND role = 'staff' AND active = 1
                    """,
                    (email,),
                ).fetchone()
                if row is None:
                    return False
                password_hash = str(row["password"])
                if not password_hash.startswith(("$2a$", "$2b$", "$2y$")):
                    return False
                return bcrypt.checkpw(
                    password.encode("utf-8"),
                    password_hash.encode("utf-8"),
                )
        except (ValueError, sqlite3.Error):
            return False

    def get_gallery_status(self, gallery_id: str) -> dict[str, Any] | None:
        if not self.db_path.is_file():
            return None
        try:
            with self._connect() as conn:
                row = conn.execute(
                    """
                    SELECT id, name, floor, department, status, accessibility_notes
                    FROM galleries
                    WHERE id = ?
                    """,
                    (gallery_id,),
                ).fetchone()
                return dict(row) if row else None
        except sqlite3.Error:
            return None

    def create_incident(
        self,
        *,
        gallery_id: str,
        category: str,
        description: str,
        priority: str,
        reported_by: str,
    ) -> dict[str, Any]:
        with self._connect() as conn:
            cursor = conn.execute(
                """
                INSERT INTO incidents(gallery_id, category, description, priority, reported_by)
                VALUES (?, ?, ?, ?, ?)
                """,
                (gallery_id, category, description, priority, reported_by),
            )
            incident_id = cursor.lastrowid
            row = conn.execute(
                """
                SELECT id, gallery_id, category, description, priority, reported_by, status,
                       created_at
                FROM incidents
                WHERE id = ?
                """,
                (incident_id,),
            ).fetchone()
            if row is None:
                raise sqlite3.DatabaseError("Created incident could not be read back")
            return dict(row)


    def get_incident(self, incident_id: int) -> dict[str, Any] | None:
        """Return one operational incident without exposing unrelated records."""
        if not self.db_path.is_file():
            return None
        try:
            with self._connect() as conn:
                row = conn.execute(
                    """
                    SELECT id, gallery_id, category, description, priority, reported_by, status,
                           created_at
                    FROM incidents
                    WHERE id = ?
                    """,
                    (incident_id,),
                ).fetchone()
                return dict(row) if row else None
        except sqlite3.Error:
            return None

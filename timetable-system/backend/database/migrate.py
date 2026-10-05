"""Tiny idempotent migration for SQLite databases created before rooms got
room_type/department and teachers got department_id.

Runs on every app start (models.init_db), after create_all has created any
missing tables (incl. `departments`). Written against a plain `execute(sql)`
callable so it works with SQLAlchemy and with stdlib sqlite3 alike.

- rooms.is_lab (bool)       -> rooms.room_type ('laboratory' / 'classroom')
- rooms.department_id       added (NULL)
- teachers.department (str) -> departments table + teachers.department_id
- academic_loads.room_type  added (NULL = by lesson type)

DROP COLUMN needs SQLite >= 3.35 (Python 3.11+ ships newer).
"""
from __future__ import annotations

from typing import Callable

Execute = Callable[[str], list]


def _columns(execute: Execute, table: str) -> set[str]:
    return {row[1] for row in execute(f"PRAGMA table_info({table})")}


def migrate(execute: Execute) -> list[str]:
    """Apply missing changes; returns a list of what was done (empty if up to date)."""
    done: list[str] = []

    rooms = _columns(execute, "rooms")
    if "room_type" not in rooms:
        execute("ALTER TABLE rooms ADD COLUMN room_type VARCHAR NOT NULL DEFAULT 'classroom'")
        done.append("rooms.room_type")
        if "is_lab" in rooms:
            execute("UPDATE rooms SET room_type = 'laboratory' WHERE is_lab = 1")
    if "is_lab" in rooms:
        execute("ALTER TABLE rooms DROP COLUMN is_lab")
        done.append("rooms.is_lab dropped")
    if "department_id" not in rooms:
        execute("ALTER TABLE rooms ADD COLUMN department_id INTEGER REFERENCES departments (id)")
        done.append("rooms.department_id")

    teachers = _columns(execute, "teachers")
    if "department_id" not in teachers:
        execute("ALTER TABLE teachers ADD COLUMN department_id INTEGER REFERENCES departments (id)")
        done.append("teachers.department_id")
    if "department" in teachers:
        execute(
            "INSERT OR IGNORE INTO departments (name) "
            "SELECT DISTINCT trim(department) FROM teachers "
            "WHERE department IS NOT NULL AND trim(department) <> ''"
        )
        execute(
            "UPDATE teachers SET department_id = "
            "(SELECT d.id FROM departments d WHERE d.name = trim(teachers.department)) "
            "WHERE department_id IS NULL"
        )
        execute("ALTER TABLE teachers DROP COLUMN department")
        done.append("teachers.department -> departments")

    if "room_type" not in _columns(execute, "academic_loads"):
        execute("ALTER TABLE academic_loads ADD COLUMN room_type VARCHAR")
        done.append("academic_loads.room_type")

    if "publication_key" not in _columns(execute, "student_groups"):
        execute("ALTER TABLE student_groups ADD COLUMN publication_key VARCHAR")
        done.append("student_groups.publication_key")
    from uuid import uuid4
    for row in execute("SELECT id FROM student_groups WHERE publication_key IS NULL"):
        execute(f"UPDATE student_groups SET publication_key = '{uuid4()}' WHERE id = {int(row[0])}")

    return done

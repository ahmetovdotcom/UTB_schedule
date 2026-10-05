"""SQLAlchemy models for the Smart Manual Timetable System.

Tables per the design spec (docs/superpowers/specs/2026-09-14-timetable-system-design.md)
plus `departments` (кафедры) referenced by rooms and teachers.

Room types and lesson/room fit rules live in room_rules.py.
"""
from __future__ import annotations

import os
from uuid import uuid4

from sqlalchemy import ForeignKey, UniqueConstraint
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite+aiosqlite:///./timetable.db")
engine = create_async_engine(DATABASE_URL)
async_session = async_sessionmaker(engine, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


class Department(Base):
    """Кафедра. A room's department is the one it is assigned to ("ответственная
    кафедра"); a teacher's department is where they work."""

    __tablename__ = "departments"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(unique=True)


class Building(Base):
    __tablename__ = "buildings"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str]

    rooms: Mapped[list["Room"]] = relationship(back_populates="building")


class Room(Base):
    __tablename__ = "rooms"

    id: Mapped[int] = mapped_column(primary_key=True)
    building_id: Mapped[int] = mapped_column(ForeignKey("buildings.id"))
    number: Mapped[str]
    capacity: Mapped[int]
    # lecture_hall | classroom | computer_class | laboratory | special | other
    room_type: Mapped[str] = mapped_column(default="classroom", server_default="classroom")
    department_id: Mapped[int | None] = mapped_column(ForeignKey("departments.id"))
    floor: Mapped[int]

    building: Mapped["Building"] = relationship(back_populates="rooms")
    department: Mapped["Department | None"] = relationship()


class Teacher(Base):
    __tablename__ = "teachers"

    id: Mapped[int] = mapped_column(primary_key=True)
    full_name: Mapped[str]
    department_id: Mapped[int | None] = mapped_column(ForeignKey("departments.id"))

    department: Mapped["Department | None"] = relationship()


class StudentGroup(Base):
    __tablename__ = "student_groups"

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str]
    publication_key: Mapped[str] = mapped_column(default=lambda: str(uuid4()))
    students_count: Mapped[int]
    course: Mapped[int]
    department_id: Mapped[int | None] = mapped_column(ForeignKey("departments.id"))
    department: Mapped["Department | None"] = relationship()


class Subject(Base):
    __tablename__ = "subjects"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str]


class TimeSlot(Base):
    __tablename__ = "time_slots"

    id: Mapped[int] = mapped_column(primary_key=True)
    slot_number: Mapped[int]
    start_time: Mapped[str]
    end_time: Mapped[str]


class AcademicLoadGroup(Base):
    """N:M link between academic_loads and student_groups."""

    __tablename__ = "academic_load_groups"

    academic_load_id: Mapped[int] = mapped_column(
        ForeignKey("academic_loads.id"), primary_key=True
    )
    group_id: Mapped[int] = mapped_column(
        ForeignKey("student_groups.id"), primary_key=True
    )


class AcademicLoad(Base):
    """A 'load card': subject + teacher + lesson type, taught to one or more groups."""

    __tablename__ = "academic_loads"

    id: Mapped[int] = mapped_column(primary_key=True)
    subject_id: Mapped[int] = mapped_column(ForeignKey("subjects.id"))
    teacher_id: Mapped[int] = mapped_column(ForeignKey("teachers.id"))
    lesson_type: Mapped[str]  # "lecture" | "practice" | "lab"
    total_hours_per_week: Mapped[int]
    # Room type this load needs (e.g. computer_class for a programming lab).
    # None = derive from lesson_type, see room_rules.DEFAULT_FIT.
    room_type: Mapped[str | None]

    subject: Mapped["Subject"] = relationship()
    teacher: Mapped["Teacher"] = relationship()
    groups: Mapped[list["StudentGroup"]] = relationship(
        secondary="academic_load_groups"
    )
    items: Mapped[list["ScheduleItem"]] = relationship(back_populates="academic_load")


class ScheduleItem(Base):
    """One placed lesson: one hour of an academic_load's total_hours_per_week."""

    __tablename__ = "schedule_items"
    __table_args__ = (
        UniqueConstraint(
            "room_id", "day_of_week", "time_slot_id", name="uq_room_day_slot"
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    academic_load_id: Mapped[int] = mapped_column(ForeignKey("academic_loads.id"))
    room_id: Mapped[int] = mapped_column(ForeignKey("rooms.id"))
    time_slot_id: Mapped[int] = mapped_column(ForeignKey("time_slots.id"))
    day_of_week: Mapped[int]  # 1..6, Monday..Saturday

    academic_load: Mapped["AcademicLoad"] = relationship(back_populates="items")
    room: Mapped["Room"] = relationship()
    time_slot: Mapped["TimeSlot"] = relationship()


def _create_and_migrate(sync_conn) -> None:
    from database.migrate import migrate

    Base.metadata.create_all(sync_conn)

    def execute(sql: str) -> list:
        result = sync_conn.exec_driver_sql(sql)
        return result.fetchall() if result.returns_rows else []

    # create_all doesn't add columns to existing tables — bring an older
    # timetable.db up to date without losing its data.
    migrate(execute)
    from database.group_department_migration import migrate_group_department
    migrate_group_department(execute)


async def init_db() -> None:
    async with engine.begin() as conn:
        await conn.run_sync(_create_and_migrate)


async def get_session():
    async with async_session() as session:
        yield session

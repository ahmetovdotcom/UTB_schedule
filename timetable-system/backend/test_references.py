"""Assert-based checks for reference CRUD deletes (references.py).

Uses a temporary SQLite file (not timetable.db) and checks the result from a
*fresh* session — a delete that forgets to commit looks fine inside its own
session and is silently rolled back when the request ends.
Run: python test_references.py
"""
import asyncio
import os
import tempfile

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from database.models import (
    AcademicLoad,
    AcademicLoadGroup,
    Base,
    Building,
    Room,
    ScheduleItem,
    StudentGroup,
    Subject,
    Teacher,
    TimeSlot,
)
from references import delete_load


async def count(session, model) -> int:
    return await session.scalar(select(func.count()).select_from(model))


async def test_delete_load_persists():
    """Deleting a load removes it, its group links and its placed pairs — for
    both a placed and an unplaced load."""
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    engine = create_async_engine(f"sqlite+aiosqlite:///{path}")
    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        make_session = async_sessionmaker(engine, expire_on_commit=False)

        async with make_session() as session:
            building = Building(name="B")
            session.add(building)
            await session.flush()
            room = Room(building_id=building.id, number="A", capacity=30, room_type="classroom", floor=1)
            teacher = Teacher(full_name="T", department_id=None)
            group = StudentGroup(code="G", students_count=20, course=1)
            subject = Subject(name="S")
            slot = TimeSlot(slot_number=1, start_time="08:00", end_time="08:50")
            session.add_all([room, teacher, group, subject, slot])
            await session.flush()
            placed = AcademicLoad(subject_id=subject.id, teacher_id=teacher.id,
                                  lesson_type="lecture", total_hours_per_week=2)
            unplaced = AcademicLoad(subject_id=subject.id, teacher_id=teacher.id,
                                    lesson_type="practice", total_hours_per_week=1)
            session.add_all([placed, unplaced])
            await session.flush()
            session.add_all([
                AcademicLoadGroup(academic_load_id=placed.id, group_id=group.id),
                AcademicLoadGroup(academic_load_id=unplaced.id, group_id=group.id),
                ScheduleItem(academic_load_id=placed.id, room_id=room.id,
                             time_slot_id=slot.id, day_of_week=1),
            ])
            await session.commit()
            placed_id, unplaced_id = placed.id, unplaced.id

        for load_id in (placed_id, unplaced_id):
            async with make_session() as session:  # one session per "request"
                await delete_load(load_id, session)

        async with make_session() as session:
            assert await count(session, AcademicLoad) == 0, "loads not deleted"
            assert await count(session, AcademicLoadGroup) == 0, "group links not deleted"
            assert await count(session, ScheduleItem) == 0, "placed pairs not deleted"
            assert await count(session, StudentGroup) == 1, "group must stay"
    finally:
        await engine.dispose()
        os.remove(path)


async def run_checks():
    await test_delete_load_persists()
    print("OK")


if __name__ == "__main__":
    asyncio.run(run_checks())

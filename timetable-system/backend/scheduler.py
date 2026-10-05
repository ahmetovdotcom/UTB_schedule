"""Auto-scheduler: DB wrapper around schedule_planner.plan().

Wipes schedule_items and rebuilds the whole timetable from scratch. The
actual algorithm (room type / department fit, even week, no windows) lives
in schedule_planner.py so it can be tested without a database — see the
docstring there for the scoring rules.

Unlike manual assign/validate, capacity is hard here (a stream that doesn't
fit anywhere stays in the pool). Room type and department mismatches are
soft: the planner avoids them, but if nothing better is free it still places
the hour and counts it in `with_warnings`.

The response explains every problem in words (`unplaced_loads`, `warnings`),
so the dispatcher can see which card didn't fit and why.
"""
from __future__ import annotations

from sqlalchemy import delete as sa_delete
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from database.models import AcademicLoad, Department, Room, ScheduleItem, TimeSlot
from room_rules import LESSON_TYPE_LABELS, evaluate_room
from schedule_planner import PLoad, PRoom, PSlot, plan

DAY_NAMES = {1: "Пн", 2: "Вт", 3: "Ср", 4: "Чт", 5: "Пт", 6: "Сб"}


def pairs_word(n: int) -> str:
    """1 пара, 2 пары, 5 пар, 11 пар, 21 пара."""
    if n % 10 == 1 and n % 100 != 11:
        return f"{n} пара"
    if n % 10 in (2, 3, 4) and not 12 <= n % 100 <= 14:
        return f"{n} пары"
    return f"{n} пар"


def explain_unplaced(
    load, diagnosis: dict, group_hours: dict[int, int], teacher_hours: dict[int, int]
) -> tuple[str, list[str]]:
    """(reason, details[]) in Russian for one unplaced load."""
    students = sum(g.students_count for g in load.groups)
    if diagnosis["kind"] == "capacity":
        return (
            f"Нет аудитории на {students} студ. — самая большая вмещает {diagnosis['max_capacity']}",
            [
                "Разделите поток на подгруппы (отдельные карточки нагрузки) "
                "или добавьте аудиторию нужной вместимости."
            ],
        )

    total = diagnosis["total_slots"]
    details: list[str] = []
    teacher = load.teacher.full_name
    if diagnosis["teacher_busy"]:
        details.append(f"Преподаватель {teacher} занят(а): {pairs_word(diagnosis['teacher_busy'])} из {total}")
    if teacher_hours.get(load.teacher_id, 0) > total:
        details.append(
            f"У преподавателя {teacher} {teacher_hours[load.teacher_id]} ч. нагрузки, "
            f"а в неделе всего {total} пар"
        )
    codes = {g.id: g.code for g in load.groups}
    for group_id, busy in sorted(diagnosis["group_busy"].items(), key=lambda kv: -kv[1]):
        code = codes.get(group_id, group_id)
        details.append(f"Группа {code} занята: {pairs_word(busy)} из {total}")
        if group_hours.get(group_id, 0) > total:
            details.append(
                f"У группы {code} {group_hours[group_id]} ч. нагрузки, а в неделе всего {total} пар"
            )
    if diagnosis["no_free_room"]:
        details.append(
            f"Ещё в {diagnosis['no_free_room']} случаях преподаватель и группы свободны, но все "
            f"подходящие по вместимости аудитории ({diagnosis['fitting_rooms']}) уже заняты"
        )
    if len(load.groups) > 1:
        details.append("Это поток из нескольких групп — нужна пара, когда свободны все группы сразу")
    return "Нет пары, где одновременно свободны преподаватель, все группы и подходящая аудитория", details


async def auto_generate(session: AsyncSession) -> dict:
    rooms = (await session.execute(select(Room).options(selectinload(Room.building)))).scalars().all()
    time_slots = (await session.execute(select(TimeSlot))).scalars().all()
    departments = {d.id: d.name for d in (await session.execute(select(Department))).scalars()}
    loads = (
        (
            await session.execute(
                select(AcademicLoad).options(
                    selectinload(AcademicLoad.groups),
                    selectinload(AcademicLoad.teacher),
                    selectinload(AcademicLoad.subject),
                )
            )
        )
        .scalars()
        .all()
    )

    result = plan(
        rooms=[
            PRoom(
                id=r.id,
                building_id=r.building_id,
                capacity=r.capacity,
                room_type=r.room_type,
                department_id=r.department_id,
            )
            for r in rooms
        ],
        loads=[
            PLoad(
                id=load.id,
                teacher_id=load.teacher_id,
                lesson_type=load.lesson_type,
                hours=load.total_hours_per_week,
                group_ids=tuple(g.id for g in load.groups),
                students=sum(g.students_count for g in load.groups),
                teacher_department_id=load.teacher.department_id,
                room_type=load.room_type,
            )
            for load in loads
        ],
        slots=[PSlot(id=ts.id, number=ts.slot_number) for ts in time_slots],
    )

    await session.execute(sa_delete(ScheduleItem))
    session.add_all(
        ScheduleItem(
            academic_load_id=p.academic_load_id,
            room_id=p.room_id,
            time_slot_id=p.time_slot_id,
            day_of_week=p.day_of_week,
        )
        for p in result.placements
    )
    await session.commit()

    # --- human-readable report ------------------------------------------------
    load_by_id = {load.id: load for load in loads}
    room_by_id = {r.id: r for r in rooms}
    slot_number = {ts.id: ts.slot_number for ts in time_slots}
    group_hours: dict[int, int] = {}
    teacher_hours: dict[int, int] = {}
    for load in loads:
        teacher_hours[load.teacher_id] = teacher_hours.get(load.teacher_id, 0) + load.total_hours_per_week
        for g in load.groups:
            group_hours[g.id] = group_hours.get(g.id, 0) + load.total_hours_per_week

    def load_summary(load) -> dict:
        return {
            "academic_load_id": load.id,
            "subject": load.subject.name,
            "lesson_type": LESSON_TYPE_LABELS.get(load.lesson_type, load.lesson_type),
            "teacher": load.teacher.full_name,
            "teacher_id": load.teacher_id,
            "groups": [g.code for g in load.groups],
            "group_ids": [g.id for g in load.groups],
            "students": sum(g.students_count for g in load.groups),
        }

    unplaced_loads = []
    for item in result.unplaced_loads:
        load = load_by_id[item.academic_load_id]
        reason, details = explain_unplaced(load, item.diagnosis, group_hours, teacher_hours)
        unplaced_loads.append(
            {
                **load_summary(load),
                "hours": item.hours,
                "total_hours": load.total_hours_per_week,
                "reason": reason,
                "details": details,
            }
        )

    warnings = []
    for p in result.placements:
        if not p.has_warning:
            continue
        load = load_by_id[p.academic_load_id]
        room = room_by_id[p.room_id]
        fit = evaluate_room(
            lesson_type=load.lesson_type,
            required_room_type=load.room_type,
            students_total=sum(g.students_count for g in load.groups),
            teacher_department_id=load.teacher.department_id,
            room_type=room.room_type,
            room_capacity=room.capacity,
            room_department_id=room.department_id,
            room_department_name=departments.get(room.department_id),
        )
        warnings.append(
            {
                **load_summary(load),
                "room": f"{room.building.name} · {room.number}",
                "when": f"{DAY_NAMES.get(p.day_of_week, p.day_of_week)}, {slot_number[p.time_slot_id]} пара",
                "messages": fit.warnings,
            }
        )

    return {
        "placed": len(result.placements),
        "unplaced": result.unplaced,
        "with_warnings": result.with_warnings,
        "unplaced_loads": unplaced_loads,
        "warnings": warnings,
    }

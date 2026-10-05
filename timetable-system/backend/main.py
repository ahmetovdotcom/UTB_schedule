"""FastAPI app: schemas, conflict validation, and the 4 schedule endpoints.

See docs/superpowers/specs/2026-09-14-timetable-system-design.md for the
business rules this implements.
"""
from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(Path(__file__).with_name(".env"))

from fastapi import Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from sqlalchemy import delete, func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from database.models import (
    AcademicLoad,
    AcademicLoadGroup,
    Building,
    Department,
    Room,
    ScheduleItem,
    StudentGroup,
    Subject,
    Teacher,
    TimeSlot,
    get_session,
    init_db,
)
from auth import require_user, router as auth_router
from publication import router as publication_router
from analytics import router as analytics_router
from excel_export import router as excel_export_router
from references import router as references_router
from room_rules import ROOM_TYPE_LABELS, evaluate_room, needed_types_label, type_fit_level
from scheduler import auto_generate


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    yield


app = FastAPI(
    title="Smart Manual Timetable System", lifespan=lifespan,
    dependencies=[Depends(require_user)],
    docs_url=None, redoc_url=None, openapi_url=None,
)

# Frontend runs on Vite's dev server (5173) during development.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth_router)
app.include_router(analytics_router)
app.include_router(excel_export_router)
app.include_router(publication_router)
app.include_router(references_router)


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


class ValidateRequest(BaseModel):
    academic_load_id: int
    day_of_week: int = Field(ge=1, le=6)
    time_slot_id: int
    room_id: int | None = None
    current_item_id: int | None = None


class RoomOptionsRequest(BaseModel):
    academic_load_id: int
    day_of_week: int = Field(ge=1, le=6)
    time_slot_id: int
    current_item_id: int | None = None


class AssignRequest(BaseModel):
    academic_load_id: int
    day_of_week: int = Field(ge=1, le=6)
    time_slot_id: int
    room_id: int
    current_item_id: int | None = None


class JoinStreamRequest(AssignRequest):
    target_item_id: int


class ValidateResponse(BaseModel):
    is_valid: bool
    errors: list[str]
    warnings: list[str]


class RoomValidationResponse(ValidateResponse):
    room_id: int
    stream_item_id: int | None = None
    stream_group_ids: list[int] = Field(default_factory=list)
    students_total: int | None = None


# ---------------------------------------------------------------------------
# Shared lookups
# ---------------------------------------------------------------------------


async def get_load_or_404(session: AsyncSession, load_id: int) -> AcademicLoad:
    stmt = (
        select(AcademicLoad)
        .where(AcademicLoad.id == load_id)
        .options(
            selectinload(AcademicLoad.groups),
            selectinload(AcademicLoad.teacher),
            selectinload(AcademicLoad.subject),
        )
    )
    load = (await session.execute(stmt)).scalar_one_or_none()
    if load is None:
        raise HTTPException(404, "Карточка нагрузки не найдена")
    return load


def room_label(room: Room) -> str:
    """'1 блок · 300' — numbers repeat between blocks. room.building must be loaded."""
    return f"{room.building.name} · {room.number}"


async def get_time_slot_or_404(session: AsyncSession, time_slot_id: int) -> TimeSlot:
    slot = await session.get(TimeSlot, time_slot_id)
    if slot is None:
        raise HTTPException(404, "Временной слот не найден")
    return slot


async def get_room_or_404(session: AsyncSession, room_id: int) -> Room:
    room = await session.get(Room, room_id)
    if room is None:
        raise HTTPException(404, "Аудитория не найдена")
    return room


# ---------------------------------------------------------------------------
# Validation core
# ---------------------------------------------------------------------------


async def get_items_in_slot(
    session: AsyncSession, day_of_week: int, time_slot_id: int,
    current_item_id: int | None,
):
    stmt = (
        select(ScheduleItem)
        .where(
            ScheduleItem.day_of_week == day_of_week,
            ScheduleItem.time_slot_id == time_slot_id,
        )
        .options(
            selectinload(ScheduleItem.academic_load).selectinload(AcademicLoad.groups),
            selectinload(ScheduleItem.academic_load).selectinload(AcademicLoad.teacher),
            selectinload(ScheduleItem.academic_load).selectinload(AcademicLoad.subject),
            selectinload(ScheduleItem.room).selectinload(Room.building),
        )
    )
    if current_item_id is not None:
        stmt = stmt.where(ScheduleItem.id != current_item_id)
    return (await session.execute(stmt)).scalars().all()


async def find_conflicts(
    session: AsyncSession,
    load: AcademicLoad,
    day_of_week: int,
    time_slot_id: int,
    room_id: int | None,
    current_item_id: int | None,
    *,
    items_in_slot: list[ScheduleItem] | None = None,
) -> tuple[list[str], list[str]]:
    """Check the rules from the spec. Returns (errors, warnings).

    errors:   a) room busy, b) teacher busy, c) group busy — placement is impossible.
    warnings: d) room too small, room type doesn't suit the lesson (e.g. a
              lecture in a computer class), specialised room of another
              department (see room_rules.py) — placement is allowed but flagged.
    """
    errors: list[str] = []
    warnings: list[str] = []
    students_total = sum(g.students_count for g in load.groups)

    if items_in_slot is None:
        items_in_slot = await get_items_in_slot(
            session, day_of_week, time_slot_id, current_item_id,
        )

    # b) teacher busy
    for item in items_in_slot:
        if item.academic_load.teacher_id == load.teacher_id:
            errors.append(
                f"{load.teacher.full_name} уже ведёт пару в аудитории {room_label(item.room)}"
            )
            break

    # c) any of this load's groups already in another lesson
    for item in items_in_slot:
        other_group_ids = {g.id for g in item.academic_load.groups}
        for group in load.groups:
            if group.id in other_group_ids:
                errors.append(
                    f"{group.code} уже на паре: {item.academic_load.subject.name}"
                )

    if room_id is not None:
        room = await get_room_or_404(session, room_id)
        building = await session.get(Building, room.building_id)
        label = f"{building.name} · {room.number}" if building else room.number

        # a) room busy
        for item in items_in_slot:
            if item.room_id == room_id:
                other_groups = ", ".join(g.code for g in item.academic_load.groups)
                errors.append(
                    f"Аудитория {label} занята: "
                    f"{item.academic_load.subject.name} ({other_groups})"
                )

        # d) capacity + room type + department (room_rules.py)
        department = (
            await session.get(Department, room.department_id) if room.department_id else None
        )
        fit = evaluate_room(
            lesson_type=load.lesson_type,
            required_room_type=load.room_type,
            students_total=students_total,
            teacher_department_id=load.teacher.department_id,
            room_type=room.room_type,
            room_capacity=room.capacity,
            room_department_id=room.department_id,
            room_department_name=department.name if department else None,
        )
        warnings.extend(fit.warnings)
    else:
        # No room chosen yet (cell-hover highlight): is there any free & suitable room?
        occupied_room_ids = {item.room_id for item in items_in_slot}
        all_rooms = (
            (await session.execute(select(Room).options(selectinload(Room.building))))
            .scalars()
            .all()
        )
        free_rooms = [r for r in all_rooms if r.id not in occupied_room_ids]

        if not free_rooms:
            errors.append("Нет свободных аудиторий в этот слот")
        else:
            right_type = [
                r
                for r in free_rooms
                if type_fit_level(load.lesson_type, load.room_type, r.room_type) <= 1
            ]
            suitable = [r for r in right_type if r.capacity >= students_total]
            if not suitable:
                if right_type:
                    best = max(right_type, key=lambda r: r.capacity)
                    warnings.append(
                        f"Нет подходящей аудитории на {students_total} мест, "
                        f"самая большая свободная — {room_label(best)} ({best.capacity})"
                    )
                else:
                    warnings.append(
                        "Нет подходящей свободной аудитории: нужна "
                        f"{needed_types_label(load.lesson_type, load.room_type)}"
                    )

    return errors, warnings


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@app.get("/api/initial-data")
async def get_initial_data(session: AsyncSession = Depends(get_session)):
    departments = (
        (await session.execute(select(Department).order_by(Department.name))).scalars().all()
    )
    department_names = {d.id: d.name for d in departments}
    buildings = (await session.execute(select(Building))).scalars().all()
    rooms = (
        (await session.execute(select(Room).options(selectinload(Room.building))))
        .scalars()
        .all()
    )
    teachers = (await session.execute(select(Teacher))).scalars().all()
    groups = (await session.execute(select(StudentGroup))).scalars().all()
    subjects = (await session.execute(select(Subject))).scalars().all()
    time_slots = (
        (await session.execute(select(TimeSlot).order_by(TimeSlot.slot_number)))
        .scalars()
        .all()
    )
    loads = (
        (
            await session.execute(
                select(AcademicLoad).options(selectinload(AcademicLoad.groups))
            )
        )
        .scalars()
        .all()
    )
    items = (await session.execute(select(ScheduleItem))).scalars().all()

    return {
        "room_types": [{"value": k, "label": v} for k, v in ROOM_TYPE_LABELS.items()],
        "departments": [{"id": d.id, "name": d.name} for d in departments],
        "buildings": [{"id": b.id, "name": b.name} for b in buildings],
        "rooms": [
            {
                "id": r.id,
                "building_id": r.building_id,
                "building_name": r.building.name,
                "number": r.number,
                "capacity": r.capacity,
                "room_type": r.room_type,
                "department_id": r.department_id,
                "department_name": department_names.get(r.department_id),
                "floor": r.floor,
            }
            for r in rooms
        ],
        "teachers": [
            {
                "id": t.id,
                "full_name": t.full_name,
                "department_id": t.department_id,
                "department_name": department_names.get(t.department_id),
            }
            for t in teachers
        ],
        "groups": [
            {
                "id": g.id,
                "code": g.code,
                "students_count": g.students_count,
                "course": g.course,
                "department_id": g.department_id,
                "department_name": department_names.get(g.department_id),
            }
            for g in groups
        ],
        "subjects": [{"id": s.id, "name": s.name} for s in subjects],
        "time_slots": [
            {
                "id": ts.id,
                "slot_number": ts.slot_number,
                "start_time": ts.start_time,
                "end_time": ts.end_time,
            }
            for ts in time_slots
        ],
        "loads": [
            {
                "id": load.id,
                "subject_id": load.subject_id,
                "teacher_id": load.teacher_id,
                "lesson_type": load.lesson_type,
                "total_hours_per_week": load.total_hours_per_week,
                "room_type": load.room_type,
                "group_ids": [g.id for g in load.groups],
                "students_total": sum(g.students_count for g in load.groups),
            }
            for load in loads
        ],
        "items": [
            {
                "id": item.id,
                "academic_load_id": item.academic_load_id,
                "room_id": item.room_id,
                "time_slot_id": item.time_slot_id,
                "day_of_week": item.day_of_week,
            }
            for item in items
        ],
    }


@app.post("/api/schedule/validate", response_model=ValidateResponse)
async def validate_schedule(
    req: ValidateRequest, session: AsyncSession = Depends(get_session)
):
    load = await get_load_or_404(session, req.academic_load_id)
    await get_time_slot_or_404(session, req.time_slot_id)
    if req.room_id is not None:
        await get_room_or_404(session, req.room_id)
    if req.current_item_id is not None and (
        await session.get(ScheduleItem, req.current_item_id)
    ) is None:
        raise HTTPException(404, "Пара не найдена")

    errors, warnings = await find_conflicts(
        session,
        load,
        req.day_of_week,
        req.time_slot_id,
        req.room_id,
        req.current_item_id,
    )
    if req.room_id is None and errors:
        options = await room_options(RoomOptionsRequest(**req.model_dump()), session)
        candidate = next((option for option in options if option.stream_item_id and option.is_valid), None)
        if candidate:
            return ValidateResponse(is_valid=True, errors=[], warnings=[
                "Можно объединить в поток — выберите общую аудиторию", *candidate.warnings,
            ])
    return ValidateResponse(is_valid=len(errors) == 0, errors=errors, warnings=warnings)


def can_join_stream(source: AcademicLoad, target: AcademicLoad) -> bool:
    return (
        source.id != target.id
        and all(getattr(source, key) == getattr(target, key) for key in (
            "subject_id", "teacher_id", "lesson_type", "room_type",
        ))
        and bool(source.groups) and bool(target.groups)
        and not ({g.id for g in source.groups} & {g.id for g in target.groups})
    )


def combined_load(source: AcademicLoad, target: AcademicLoad) -> AcademicLoad:
    # A single shared hour. Remaining hours stay with the original cards.
    return AcademicLoad(
        subject_id=source.subject_id, teacher_id=source.teacher_id,
        lesson_type=source.lesson_type, room_type=source.room_type,
        total_hours_per_week=1, groups=[*target.groups, *source.groups],
    )


@app.post("/api/schedule/room-options", response_model=list[RoomValidationResponse])
async def room_options(
    req: RoomOptionsRequest, session: AsyncSession = Depends(get_session),
):
    load = await get_load_or_404(session, req.academic_load_id)
    await get_time_slot_or_404(session, req.time_slot_id)
    if req.current_item_id is not None and (
        await session.get(ScheduleItem, req.current_item_id)
    ) is None:
        raise HTTPException(404, "Пара не найдена")
    items = await get_items_in_slot(
        session, req.day_of_week, req.time_slot_id, req.current_item_id,
    )
    # Keep rooms and their related objects alive in the identity map so the
    # shared validator's session.get calls require no per-room SQL queries.
    rooms = (await session.execute(
        select(Room).options(selectinload(Room.building), selectinload(Room.department))
    )).scalars().all()
    results = []
    for room in rooms:
        errors, warnings = await find_conflicts(
            session, load, req.day_of_week, req.time_slot_id, room.id,
            req.current_item_id, items_in_slot=items,
        )
        target = next((item for item in items if item.room_id == room.id), None)
        stream_item_id = None
        stream_group_ids = []
        students_total = None
        if target and can_join_stream(load, target.academic_load):
            combined = combined_load(load, target.academic_load)
            combined.teacher = load.teacher
            errors, warnings = await find_conflicts(
                session, combined, req.day_of_week, req.time_slot_id, room.id,
                req.current_item_id,
                items_in_slot=[item for item in items if item.id != target.id],
            )
            stream_item_id = target.id
            stream_group_ids = [group.id for group in combined.groups]
            students_total = sum(group.students_count for group in combined.groups)
        results.append(RoomValidationResponse(
            room_id=room.id, is_valid=not errors, errors=errors, warnings=warnings,
            stream_item_id=stream_item_id, stream_group_ids=stream_group_ids,
            students_total=students_total,
        ))
    return results


@app.post("/api/schedule/join-stream")
async def join_stream(req: JoinStreamRequest, session: AsyncSession = Depends(get_session)):
    try:
        # Reserve the source/target loads for this write transaction before
        # inspecting hours. SQLite serializes writers; other SQL backends lock
        # these rows. Repeated joins then see the already-consumed source hour.
        await session.execute(update(AcademicLoad).where(
            (AcademicLoad.id == req.academic_load_id) |
            (AcademicLoad.id.in_(select(ScheduleItem.academic_load_id).where(
                ScheduleItem.id == req.target_item_id)))
        ).values(total_hours_per_week=AcademicLoad.total_hours_per_week))
        source = await get_load_or_404(session, req.academic_load_id)
        target_item = await session.get(ScheduleItem, req.target_item_id)
        if target_item is None:
            raise HTTPException(404, "Занятие потока не найдено")
        if (target_item.day_of_week, target_item.time_slot_id, target_item.room_id) != (
            req.day_of_week, req.time_slot_id, req.room_id,
        ):
            raise HTTPException(409, "Занятие уже перемещено. Выберите аудиторию заново.")
        target = await get_load_or_404(session, target_item.academic_load_id)
        if not can_join_stream(source, target):
            raise HTTPException(409, "Для потока нужны разные группы, одинаковые предмет, преподаватель, тип занятия и требования к помещению.")
        current = None
        if req.current_item_id is not None:
            current = await session.get(ScheduleItem, req.current_item_id)
            if current is None or current.academic_load_id != source.id:
                raise HTTPException(409, "Перемещаемое занятие изменилось. Обновите расписание.")
        else:
            used = await session.scalar(select(func.count()).select_from(ScheduleItem).where(
                ScheduleItem.academic_load_id == source.id,
            ))
            if used >= source.total_hours_per_week:
                raise HTTPException(409, "Все часы этой карточки уже расставлены")
        combined = combined_load(source, target)
        # Avoid attaching the temporary combined object until validation passes.
        combined.teacher = source.teacher
        items = await get_items_in_slot(session, req.day_of_week, req.time_slot_id, req.current_item_id)
        errors, warnings = await find_conflicts(
            session, combined, req.day_of_week, req.time_slot_id, req.room_id,
            req.current_item_id, items_in_slot=[item for item in items if item.id != target_item.id],
        )
        if errors:
            raise HTTPException(409, detail={"errors": errors, "warnings": warnings})
        session.add(combined)
        await session.flush()
        target_item.academic_load_id = combined.id
        if current is not None:
            await session.delete(current)
        source.total_hours_per_week -= 1
        target.total_hours_per_week -= 1
        await session.flush()
        for original in (source, target):
            if original.total_hours_per_week == 0:
                await session.execute(delete(AcademicLoadGroup).where(
                    AcademicLoadGroup.academic_load_id == original.id,
                ))
                await session.execute(delete(AcademicLoad).where(AcademicLoad.id == original.id))
        result = {"id": target_item.id, "academic_load_id": combined.id, "warnings": warnings}
        await session.commit()
        return result
    except Exception:
        await session.rollback()
        raise


@app.post("/api/schedule/assign")
async def assign_schedule(
    req: AssignRequest, session: AsyncSession = Depends(get_session)
):
    load = await get_load_or_404(session, req.academic_load_id)
    await get_time_slot_or_404(session, req.time_slot_id)
    await get_room_or_404(session, req.room_id)

    current_item: ScheduleItem | None = None
    if req.current_item_id is not None:
        current_item = await session.get(ScheduleItem, req.current_item_id)
        if current_item is None:
            raise HTTPException(404, "Пара не найдена")
        if current_item.academic_load_id != req.academic_load_id:
            raise HTTPException(400, "Пара принадлежит другой карточке нагрузки")

    errors, warnings = await find_conflicts(
        session,
        load,
        req.day_of_week,
        req.time_slot_id,
        req.room_id,
        req.current_item_id,
    )
    if errors:
        raise HTTPException(
            409, detail={"is_valid": False, "errors": errors, "warnings": warnings}
        )

    if current_item is None:
        used_hours = await session.scalar(
            select(func.count())
            .select_from(ScheduleItem)
            .where(ScheduleItem.academic_load_id == load.id)
        )
        if used_hours >= load.total_hours_per_week:
            raise HTTPException(
                409,
                detail={
                    "is_valid": False,
                    "errors": ["Все часы уже расставлены"],
                    "warnings": [],
                },
            )
        item = ScheduleItem(
            academic_load_id=load.id,
            room_id=req.room_id,
            time_slot_id=req.time_slot_id,
            day_of_week=req.day_of_week,
        )
        session.add(item)
    else:
        current_item.room_id = req.room_id
        current_item.time_slot_id = req.time_slot_id
        current_item.day_of_week = req.day_of_week
        item = current_item

    try:
        await session.commit()
    except IntegrityError:
        await session.rollback()
        raise HTTPException(
            409,
            detail={"is_valid": False, "errors": ["Аудитория занята"], "warnings": []},
        )

    await session.refresh(item)
    return {
        "id": item.id,
        "academic_load_id": item.academic_load_id,
        "room_id": item.room_id,
        "time_slot_id": item.time_slot_id,
        "day_of_week": item.day_of_week,
        "warnings": warnings,
    }


@app.delete("/api/schedule/unassign/{item_id}", status_code=204)
async def unassign_schedule(
    item_id: int, session: AsyncSession = Depends(get_session)
):
    item = await session.get(ScheduleItem, item_id)
    if item is None:
        raise HTTPException(404, "Пара не найдена")
    await session.delete(item)
    await session.commit()


@app.post("/api/schedule/auto-generate")
async def auto_generate_schedule(session: AsyncSession = Depends(get_session)):
    """Wipe the schedule and rebuild it greedily. See scheduler.py."""
    return await auto_generate(session)

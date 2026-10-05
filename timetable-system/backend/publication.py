"""Persist a draft snapshot, then deliver it to the student API."""
import os
import re
from collections import Counter
from datetime import datetime, timezone
from uuid import uuid4
from urllib.parse import urlsplit

import httpx
from fastapi import APIRouter, Depends, HTTPException
from pydantic import ValidationError
from sqlalchemy import JSON, select, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column, selectinload

from database.models import (Base, StudentGroup, ScheduleItem, AcademicLoad, Room, Teacher, get_session)
from publication_schema import PublicationPayload

router = APIRouter(prefix="/api/publication", tags=["publication"])


class PublicationSource(Base):
    __tablename__ = "publication_source"
    id: Mapped[int] = mapped_column(primary_key=True)
    source_id: Mapped[str]


class Publication(Base):
    __tablename__ = "publications"
    __table_args__ = {"sqlite_autoincrement": True}
    id: Mapped[int] = mapped_column(primary_key=True)
    payload: Mapped[dict] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(default="pending")


def destination():
    url = os.getenv("STUDENT_PUBLISH_URL", "").strip()
    key = os.getenv("PUBLISH_SECRET_KEY", "")
    parsed = urlsplit(url)
    local = parsed.hostname in {"localhost", "127.0.0.1", "::1"}
    internal = (os.getenv("STUDENT_PUBLISH_ALLOW_INTERNAL_HTTP") == "true"
                and parsed.hostname == "student-api" and parsed.port == 8000
                and parsed.path == "/api/v1/publication")
    if (not url or len(key) < 32 or parsed.username or parsed.password or parsed.query
            or parsed.fragment or not parsed.hostname
            or (parsed.scheme != "https" and not (parsed.scheme == "http" and (local or internal)))):
        raise HTTPException(503, "Публикация не настроена: укажите адрес студенческого API и ключ в настройках сервера")
    return url, key


@router.get("")
async def status(session: AsyncSession = Depends(get_session)):
    try:
        destination()
        configured = True
    except HTTPException:
        configured = False
    last = await session.scalar(select(Publication).where(Publication.status == "confirmed")
                                .order_by(Publication.id.desc()).limit(1))
    return {"configured": configured, "published_at": last.payload["published_at"] if last else None,
            "revision": last.id if last else None}


def published_group_titles(groups):
    counts = Counter(group.code.strip() for group in groups)
    titles = {
        group.id: (f"{group.code.strip()} · {group.course} курс"
                   if counts[group.code.strip()] > 1 else group.code.strip())
        for group in groups
    }
    duplicates = [title for title, count in Counter(titles.values()).items() if count > 1]
    if duplicates:
        raise HTTPException(409, "Уточните названия групп в справочнике: даже с учётом курса повторяются "
                            + ", ".join(sorted(duplicates)))
    return titles


def publication_time(value: str, slot_number: int) -> str:
    match = re.fullmatch(r"([0-9]{1,2}):([0-9]{2})(?::00)?", value.strip())
    if match:
        hour, minute = map(int, match.groups())
        if 0 <= hour <= 23 and 0 <= minute <= 59:
            return f"{hour:02d}:{minute:02d}"
    raise HTTPException(409, f"Проверьте время у пары №{slot_number}: «{value}». Укажите часы и минуты, например 08:30.")


async def snapshot(session, teachers=None):
    catalog = {teacher.id: {"id": teacher.id, "title": teacher.full_name, "lessons": []}
               for teacher in (await session.scalars(select(Teacher).order_by(Teacher.full_name))).all()}
    groups = (await session.scalars(select(StudentGroup).order_by(StudentGroup.id))).all()
    titles = published_group_titles(groups)
    output = {group.id: {"source_id": group.publication_key, "title": titles[group.id], "lessons": []} for group in groups}
    items = (await session.scalars(select(ScheduleItem).order_by(ScheduleItem.id).options(
        selectinload(ScheduleItem.academic_load).selectinload(AcademicLoad.groups),
        selectinload(ScheduleItem.academic_load).selectinload(AcademicLoad.subject),
        selectinload(ScheduleItem.room).selectinload(Room.building),
        selectinload(ScheduleItem.time_slot)))).all()
    occupied = {}
    for item in items:
        load, room, slot = item.academic_load, item.room, item.time_slot
        if not load or not room or not slot or not load.subject or not room.building or not load.groups:
            raise HTTPException(409, "Есть пара с неполными данными. Проверьте нагрузку, группы и аудитории.")
        start = publication_time(slot.start_time, slot.slot_number)
        end = publication_time(slot.end_time, slot.slot_number)
        if end <= start:
            raise HTTPException(409, f"У пары №{slot.slot_number} окончание должно быть позже начала: {start}–{end}.")
        for kind, entity_id in (("преподавателя", load.teacher_id), ("аудитории", room.id)):
            key = (kind, entity_id, item.day_of_week)
            for other_start, other_end in occupied.get(key, []):
                if start < other_end and other_start < end:
                    raise HTTPException(409, f"Пересечение времени у {kind}. Исправьте расписание перед публикацией.")
            occupied.setdefault(key, []).append((start, end))
        if load.teacher_id not in catalog:
            raise HTTPException(409, "У пары не найден преподаватель")
        catalog[load.teacher_id]["lessons"].append({"id": item.id, "day_of_week": item.day_of_week,
            "time": f"{start}-{end}", "subject": load.subject.name,
            "room": f"{room.building.name} · {room.number}",
            "groups": [titles[group.id] for group in load.groups]})
        for group in load.groups:
            output[group.id]["lessons"].append({"day_of_week": item.day_of_week,
                "time": f"{start}-{end}", "subject": load.subject.name,
                "room": f"{room.building.name} · {room.number}"})
    if teachers is not None:
        teachers.extend(catalog.values())
    return list(output.values())


async def send_publication(url, key, payload):
    async with httpx.AsyncClient(timeout=30, follow_redirects=False) as client:
        result = await client.post(url, json=payload, headers={"Authorization": f"Bearer {key}"})
        result.raise_for_status()
        receipt = result.json()
        if (receipt.get("published") is not True or receipt.get("revision") != payload["revision"]
                or receipt.get("source_id") != payload["source_id"]):
            raise ValueError("Invalid publication receipt")
        return receipt


@router.post("")
async def publish(session: AsyncSession = Depends(get_session)):
    url, key = destination()
    # The auth dependency has read from this session. Start a fresh locked SQLite
    # transaction so all draft tables form one consistent snapshot across workers.
    await session.commit()
    await session.execute(text("BEGIN IMMEDIATE"))
    source = await session.get(PublicationSource, 1)
    if source is None:
        source = PublicationSource(id=1, source_id=str(uuid4()))
        session.add(source)
    publication = Publication(payload={}, status="pending")
    session.add(publication)
    await session.flush()
    try:
        teachers = []
        groups = await snapshot(session, teachers)
        payload = PublicationPayload(source_id=source.source_id, revision=publication.id,
            published_at=datetime.now(timezone.utc), groups=groups, teachers=teachers).model_dump(mode="json")
    except ValidationError as error:
        await session.rollback()
        detail = str(error.errors()[0]["msg"])
        raise HTTPException(409, f"Проверьте данные перед публикацией: {detail}") from error
    publication.payload = payload
    await session.commit()
    # No draft/DB lock is held while waiting for the other application.
    try:
        receipt = await send_publication(url, key, payload)
    except (httpx.HTTPError, ValueError) as error:
        publication.status = "unconfirmed"
        await session.commit()
        if isinstance(error, httpx.HTTPStatusError) and error.response.status_code == 409:
            raise HTTPException(409, "Студенческий сервер отклонил устаревшую версию или другой источник. Обновите страницу и проверьте настройки.") from error
        raise HTTPException(502, "Получение публикации не подтверждено. Проверьте связь с UTB2 и повторите публикацию.") from error
    publication.status = "confirmed"
    await session.commit()
    return receipt

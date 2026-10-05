"""Room-suitability rules shared by manual validation (main.py) and the
auto-scheduler (schedule_planner.py).

Pure Python on purpose — no SQLAlchemy/FastAPI imports — so the rules can be
tested and reused without a database.

Nothing here blocks a placement: a room that doesn't match the lesson only
produces warnings. The auto-scheduler turns the same evaluation into a score
(prefers ideal rooms, falls back to others with a warning).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

RoomType = Literal[
    "lecture_hall", "classroom", "computer_class", "laboratory", "special", "other"
]
LessonType = Literal["lecture", "practice", "lab"]

ROOM_TYPE_LABELS: dict[str, str] = {
    "lecture_hall": "Лекционная аудитория",
    "classroom": "Учебная аудитория",
    "computer_class": "Компьютерный класс",
    "laboratory": "Лаборатория",
    "special": "Спец. кабинет",
    "other": "Не для занятий",
}
ROOM_TYPES: tuple[str, ...] = tuple(ROOM_TYPE_LABELS)

LESSON_TYPE_LABELS: dict[str, str] = {
    "lecture": "Лекция",
    "practice": "Практика",
    "lab": "Лабораторная",
}

# Rooms that belong to a department in a meaningful way (equipment, profile).
# For ordinary lecture halls / classrooms the "responsible department" is just
# who looks after the room, so a foreign department there is not a warning.
SPECIALIZED_ROOM_TYPES = frozenset({"computer_class", "laboratory", "special"})

# lesson_type -> (ideal room types, acceptable without warning)
DEFAULT_FIT: dict[str, tuple[frozenset[str], frozenset[str]]] = {
    "lecture": (frozenset({"lecture_hall"}), frozenset({"classroom"})),
    "practice": (frozenset({"classroom"}), frozenset({"lecture_hall", "computer_class"})),
    # Which specialised room exactly is decided by the load's explicit
    # room_type (e.g. computer_class for programmers) or by department match.
    "lab": (frozenset({"laboratory", "computer_class", "special"}), frozenset()),
}

# Fit levels
IDEAL, ACCEPTABLE, MISMATCH, UNUSABLE = 0, 1, 2, 3


def expected_room_types(
    lesson_type: str, required_room_type: str | None
) -> tuple[frozenset[str], frozenset[str]]:
    """(ideal, acceptable) room types for a load."""
    if required_room_type:
        acceptable = frozenset({"classroom"}) if required_room_type == "lecture_hall" else frozenset()
        return frozenset({required_room_type}), acceptable
    return DEFAULT_FIT.get(lesson_type, (frozenset({"classroom"}), frozenset()))


def type_fit_level(lesson_type: str, required_room_type: str | None, room_type: str) -> int:
    if room_type == "other":
        return UNUSABLE
    ideal, acceptable = expected_room_types(lesson_type, required_room_type)
    if room_type in ideal:
        return IDEAL
    if room_type in acceptable:
        return ACCEPTABLE
    return MISMATCH


def needed_types_label(lesson_type: str, required_room_type: str | None) -> str:
    ideal, acceptable = expected_room_types(lesson_type, required_room_type)
    ordered = [t for t in ROOM_TYPES if t in ideal] + [t for t in ROOM_TYPES if t in acceptable]
    return " / ".join(ROOM_TYPE_LABELS[t] for t in ordered)


@dataclass
class RoomFit:
    type_level: int
    department_mismatch: bool  # only for specialised rooms
    seats_short: int  # 0 when everyone fits
    warnings: list[str] = field(default_factory=list)

    @property
    def is_clean(self) -> bool:
        return not self.warnings


def evaluate_room(
    *,
    lesson_type: str,
    required_room_type: str | None,
    students_total: int,
    teacher_department_id: int | None,
    room_type: str,
    room_capacity: int,
    room_department_id: int | None,
    room_department_name: str | None,
) -> RoomFit:
    warnings: list[str] = []

    seats_short = max(0, students_total - room_capacity)
    if seats_short:
        warnings.append(
            f"Вместимость {room_capacity} < {students_total} студентов — "
            f"не хватит {seats_short} мест"
        )

    level = type_fit_level(lesson_type, required_room_type, room_type)
    room_label = ROOM_TYPE_LABELS.get(room_type, room_type)
    if level == UNUSABLE:
        warnings.append(f"Помещение «{room_label}» — не предназначено для пар")
    elif level == MISMATCH:
        lesson_label = LESSON_TYPE_LABELS.get(lesson_type, lesson_type)
        warnings.append(
            f"{lesson_label} в помещении «{room_label}» — нужна: "
            f"{needed_types_label(lesson_type, required_room_type)}"
        )

    department_mismatch = (
        room_type in SPECIALIZED_ROOM_TYPES
        and room_department_id is not None
        and teacher_department_id is not None
        and room_department_id != teacher_department_id
    )
    if department_mismatch:
        warnings.append(
            f"Помещение закреплено за кафедрой «{room_department_name or room_department_id}»"
        )

    return RoomFit(
        type_level=level,
        department_mismatch=department_mismatch,
        seats_short=seats_short,
        warnings=warnings,
    )

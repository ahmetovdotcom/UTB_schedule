"""Pure-Python core of the auto-scheduler (no DB). scheduler.py feeds it ORM
data and writes the result back.

Greedy, one teaching hour at a time, then a few improvement passes that take
each hour out and re-insert it where it is cheapest (windows and lonely
single-pair days mostly disappear there). Capacity stays a hard rule here (a
stream that physically doesn't fit is left in the pool), and rooms of type
"other" are never used. Everything else is a soft score, lower is better:

Room (see room_rules.py):
  - room type: ideal 0, acceptable a bit, mismatch a lot (still possible
    if nothing better is free — then it is counted in `with_warnings`);
  - specialised room of another department: a lot; ordinary room of another
    department: a little (own department's rooms are preferred);
  - empty seats: a little (don't put 12 students into a 62-seat hall);
  - room changes between back-to-back hours of the same group.

"Human" week, per group (and lighter per teacher):
  - daily target = max(2, ceil(weekly hours / 5)); above target costs,
    above MAX_PER_DAY costs a lot (soft cap, so overloaded data still places);
  - opening an extra day when the hours already fit into fewer days costs,
    so nobody comes in for a single pair unless they have to;
  - windows (gaps) between pairs cost;
  - two non-adjacent hours of the same load on one day cost (subject is spread
    over the week; a lab may be a back-to-back double pair);
  - Saturday and late slots cost a little.
"""
from __future__ import annotations

import math
from collections import Counter
from dataclasses import dataclass, field

from room_rules import ACCEPTABLE, IDEAL, MISMATCH, SPECIALIZED_ROOM_TYPES, UNUSABLE, type_fit_level

WORKDAYS = 5
MIN_PER_DAY = 2
MAX_PER_DAY = 5
SATURDAY = 6

# Weights. Tiers are chosen so a harder rule always outweighs softer ones.
W_OVER_CAP = 300
W_ABOVE_TARGET = 12
W_EXTRA_DAY = 15
W_GAP = 8
W_SAME_LOAD_DAY = 10
W_SATURDAY = 6
W_LATE_SLOT = 1

W_TEACHER_OVER_CAP = 100
W_TEACHER_ABOVE_TARGET = 4
W_TEACHER_EXTRA_DAY = 3
W_TEACHER_GAP = 3

W_TYPE = {IDEAL: 0, ACCEPTABLE: 4, MISMATCH: 60}
W_FOREIGN_SPECIALIZED = 25
W_FOREIGN_GENERAL = 1
W_EMPTY_SEATS = 4  # multiplied by the empty share of the room (0..1)
W_ROOM_CHANGE_SAME_BUILDING = 2
W_ROOM_CHANGE_OTHER_BUILDING = 5

IMPROVE_PASSES = 4


@dataclass(frozen=True)
class PRoom:
    id: int
    building_id: int
    capacity: int
    room_type: str
    department_id: int | None = None


@dataclass(frozen=True)
class PLoad:
    id: int
    teacher_id: int
    lesson_type: str
    hours: int
    group_ids: tuple[int, ...]
    students: int
    teacher_department_id: int | None = None
    room_type: str | None = None  # required room type, None = by lesson type


@dataclass(frozen=True)
class PSlot:
    id: int
    number: int


@dataclass(frozen=True)
class Placement:
    academic_load_id: int
    room_id: int
    time_slot_id: int
    day_of_week: int
    has_warning: bool


@dataclass(frozen=True)
class UnplacedLoad:
    """Hours of one load that could not be placed, and why.

    diagnosis["kind"]:
      "capacity" — no usable room is big enough; "max_capacity" = biggest room.
      "time"     — rooms exist, but no (day, slot) where the teacher, every group
                   and at least one fitting room are free at once. Counts over all
                   "total_slots": "teacher_busy", "group_busy" {group_id: n},
                   "no_free_room" (teacher and groups free, all fitting rooms taken),
                   "fitting_rooms".
    """

    academic_load_id: int
    hours: int
    diagnosis: dict


@dataclass
class PlanResult:
    placements: list[Placement] = field(default_factory=list)
    unplaced: int = 0
    unplaced_loads: list[UnplacedLoad] = field(default_factory=list)

    @property
    def with_warnings(self) -> int:
        return sum(1 for p in self.placements if p.has_warning)


def daily_target(weekly_hours: int) -> int:
    if weekly_hours <= 0:
        return 0
    return min(weekly_hours, max(MIN_PER_DAY, math.ceil(weekly_hours / WORKDAYS)))


def gaps(slot_numbers: set[int]) -> int:
    if len(slot_numbers) < 2:
        return 0
    return (max(slot_numbers) - min(slot_numbers) + 1) - len(slot_numbers)


def room_static_score(load: PLoad, room: PRoom) -> tuple[int, bool] | None:
    """(score, placement would carry a warning) or None if the room can't be used."""
    if room.capacity < load.students:
        return None
    level = type_fit_level(load.lesson_type, load.room_type, room.room_type)
    if level == UNUSABLE:
        return None
    score = W_TYPE[level]
    warning = level == MISMATCH

    foreign = (
        room.department_id is not None
        and load.teacher_department_id is not None
        and room.department_id != load.teacher_department_id
    )
    if foreign:
        if room.room_type in SPECIALIZED_ROOM_TYPES:
            score += W_FOREIGN_SPECIALIZED
            warning = True
        else:
            score += W_FOREIGN_GENERAL

    if room.capacity > 0:
        score += round(W_EMPTY_SEATS * (room.capacity - load.students) / room.capacity)
    return score, warning


class _Tracker:
    """Per-entity (group or teacher) busy slot numbers by day."""

    def __init__(self) -> None:
        self.slots: dict[tuple[int, int], set[int]] = {}  # (entity, day) -> slot numbers
        self.day_counts: dict[int, Counter] = {}  # entity -> {day: hours}

    def day_slots(self, entity: int, day: int) -> set[int]:
        return self.slots.get((entity, day), set())

    def active_days(self, entity: int) -> int:
        return len(self.day_counts.get(entity, ()))

    def add(self, entity: int, day: int, number: int) -> None:
        self.slots.setdefault((entity, day), set()).add(number)
        self.day_counts.setdefault(entity, Counter())[day] += 1

    def remove(self, entity: int, day: int, number: int) -> None:
        self.slots[(entity, day)].discard(number)
        counts = self.day_counts[entity]
        counts[day] -= 1
        if counts[day] == 0:
            del counts[day]


def _entity_time_cost(
    tracker: _Tracker,
    entity: int,
    day: int,
    number: int,
    limits: tuple[int, int, int],
    w_cap: int,
    w_target: int,
    w_extra_day: int,
    w_gap: int,
) -> int:
    """Exact marginal cost of adding one hour (convex per-day load, extra days, windows)."""
    target, cap, days_needed = limits
    current = tracker.day_slots(entity, day)
    after = len(current) + 1
    cost = 0
    if after > target:
        cost += w_target * (after - target)
    if after > cap:
        cost += w_cap * (after - cap)
    if not current and tracker.active_days(entity) >= days_needed:
        cost += w_extra_day
    cost += w_gap * (gaps(current | {number}) - gaps(current))
    return cost


def _limits(weekly_hours: int) -> tuple[int, int, int]:
    target = daily_target(weekly_hours)
    cap = max(MAX_PER_DAY, target)
    days_needed = math.ceil(weekly_hours / target) if target else 0
    return target, cap, days_needed


class _Planner:
    def __init__(self, rooms, loads, slots, days) -> None:
        self.slots = sorted(slots, key=lambda s: s.number)
        self.days = list(days)
        self.room_by_id = {r.id: r for r in rooms}
        self.loads = loads

        group_hours: dict[int, int] = {}
        teacher_hours: dict[int, int] = {}
        for load in loads:
            for gid in load.group_ids:
                group_hours[gid] = group_hours.get(gid, 0) + load.hours
            teacher_hours[load.teacher_id] = teacher_hours.get(load.teacher_id, 0) + load.hours
        self.group_limits = {g: _limits(h) for g, h in group_hours.items()}
        self.teacher_limits = {t: _limits(h) for t, h in teacher_hours.items()}

        self.room_busy: set[tuple[int, int, int]] = set()  # (room_id, day, slot_id)
        self.group_busy: set[tuple[int, int, int]] = set()
        self.teacher_busy: set[tuple[int, int, int]] = set()
        self.groups = _Tracker()
        self.teachers = _Tracker()
        self.group_room_at: dict[tuple[int, int, int], int] = {}  # (group, day, slot_number) -> room
        self.load_day_slots: dict[tuple[int, int], set[int]] = {}  # (load, day) -> slot numbers

        # Rooms each load may use, best static score first.
        self.candidates: dict[int, list[tuple[int, bool, PRoom]]] = {}
        for load in loads:
            options = []
            for room in rooms:
                static = room_static_score(load, room)
                if static is not None:
                    options.append((static[0], static[1], room))
            options.sort(key=lambda o: (o[0], o[2].id))
            self.candidates[load.id] = options

    # -- state -------------------------------------------------------------

    def place(self, load: PLoad, day: int, slot: PSlot, room: PRoom) -> None:
        self.room_busy.add((room.id, day, slot.id))
        self.teacher_busy.add((load.teacher_id, day, slot.id))
        self.teachers.add(load.teacher_id, day, slot.number)
        self.load_day_slots.setdefault((load.id, day), set()).add(slot.number)
        for g in load.group_ids:
            self.group_busy.add((g, day, slot.id))
            self.groups.add(g, day, slot.number)
            self.group_room_at[(g, day, slot.number)] = room.id

    def unplace(self, load: PLoad, day: int, slot: PSlot, room: PRoom) -> None:
        self.room_busy.discard((room.id, day, slot.id))
        self.teacher_busy.discard((load.teacher_id, day, slot.id))
        self.teachers.remove(load.teacher_id, day, slot.number)
        self.load_day_slots[(load.id, day)].discard(slot.number)
        for g in load.group_ids:
            self.group_busy.discard((g, day, slot.id))
            self.groups.remove(g, day, slot.number)
            del self.group_room_at[(g, day, slot.number)]

    # -- scoring -----------------------------------------------------------

    def time_cost(self, load: PLoad, day: int, slot: PSlot) -> int | None:
        """Marginal cost of the (day, slot) part, or None if someone is busy."""
        if (load.teacher_id, day, slot.id) in self.teacher_busy:
            return None
        if any((g, day, slot.id) in self.group_busy for g in load.group_ids):
            return None
        cost = W_LATE_SLOT * (slot.number - 1)
        if day == SATURDAY:
            cost += W_SATURDAY
        for g in load.group_ids:
            cost += _entity_time_cost(
                self.groups, g, day, slot.number, self.group_limits[g],
                W_OVER_CAP, W_ABOVE_TARGET, W_EXTRA_DAY, W_GAP,
            )
        cost += _entity_time_cost(
            self.teachers, load.teacher_id, day, slot.number,
            self.teacher_limits[load.teacher_id],
            W_TEACHER_OVER_CAP, W_TEACHER_ABOVE_TARGET, W_TEACHER_EXTRA_DAY, W_TEACHER_GAP,
        )
        for other in self.load_day_slots.get((load.id, day), ()):
            if not (load.lesson_type == "lab" and abs(other - slot.number) == 1):
                cost += W_SAME_LOAD_DAY
        return cost

    def room_change_cost(self, load: PLoad, day: int, slot: PSlot, room: PRoom) -> int:
        cost = 0
        for g in load.group_ids:
            for neighbor in (slot.number - 1, slot.number + 1):
                used = self.group_room_at.get((g, day, neighbor))
                if used is None or used == room.id:
                    continue
                cost += (
                    W_ROOM_CHANGE_SAME_BUILDING
                    if self.room_by_id[used].building_id == room.building_id
                    else W_ROOM_CHANGE_OTHER_BUILDING
                )
        return cost

    def best_position(self, load: PLoad):
        """(cost, day, slot, room, warning) of the cheapest free position, or None."""
        options = self.candidates[load.id]
        if not options:
            return None
        min_static = options[0][0]
        best = None
        for day in self.days:
            for slot in self.slots:
                t_cost = self.time_cost(load, day, slot)
                if t_cost is None:
                    continue
                if best is not None and t_cost + min_static >= best[0]:
                    continue
                for static, warn, room in options:
                    if best is not None and t_cost + static >= best[0]:
                        break  # options are sorted: nothing cheaper further on
                    if (room.id, day, slot.id) in self.room_busy:
                        continue
                    total = t_cost + static + self.room_change_cost(load, day, slot, room)
                    if best is None or total < best[0]:
                        best = (total, day, slot, room, warn)
        return best

    def position_cost(self, load: PLoad, day: int, slot: PSlot, room: PRoom) -> int:
        static = next(s for s, _, r in self.candidates[load.id] if r.id == room.id)
        return self.time_cost(load, day, slot) + static + self.room_change_cost(load, day, slot, room)

    # -- algorithm ---------------------------------------------------------

    def run(self) -> PlanResult:
        # Most constrained first: fewest warning-free rooms, then biggest stream.
        def constraint_key(load: PLoad):
            good = sum(1 for _, warn, _ in self.candidates[load.id] if not warn)
            return (good, -load.students, -load.hours, load.id)

        # placement index -> [load, day, slot, room, warning]
        placed: list[list] = []
        unplaced: list[PLoad] = []  # one entry per hour that didn't fit
        for load in sorted(self.loads, key=constraint_key):
            for _ in range(load.hours):
                best = self.best_position(load)
                if best is None:
                    unplaced.append(load)
                    continue
                _, day, slot, room, warn = best
                self.place(load, day, slot, room)
                placed.append([load, day, slot, room, warn])

        # Improvement: take each hour out and put it back where it is cheapest.
        # Costs are exact marginals, so every accepted move lowers the total.
        for _ in range(IMPROVE_PASSES):
            moved = False
            for entry in placed:
                load, day, slot, room, _ = entry
                self.unplace(load, day, slot, room)
                current = self.position_cost(load, day, slot, room)
                best = self.best_position(load)
                if best is not None and best[0] < current:
                    _, day, slot, room, warn = best
                    entry[1:] = [day, slot, room, warn]
                    moved = True
                self.place(load, entry[1], entry[2], entry[3])
            if not moved:
                break

        # The improvement pass may have freed a slot — try the leftovers once more.
        still_unplaced: list[PLoad] = []
        for load in unplaced:
            best = self.best_position(load)
            if best is None:
                still_unplaced.append(load)
                continue
            _, day, slot, room, warn = best
            self.place(load, day, slot, room)
            placed.append([load, day, slot, room, warn])

        hours_by_load: dict[int, int] = {}
        load_by_id: dict[int, PLoad] = {}
        for load in still_unplaced:
            hours_by_load[load.id] = hours_by_load.get(load.id, 0) + 1
            load_by_id[load.id] = load

        return PlanResult(
            placements=[
                Placement(load.id, room.id, slot.id, day, warn)
                for load, day, slot, room, warn in placed
            ],
            unplaced=len(still_unplaced),
            unplaced_loads=[
                UnplacedLoad(load_id, hours, self.diagnose(load_by_id[load_id]))
                for load_id, hours in hours_by_load.items()
            ],
        )

    def diagnose(self, load: PLoad) -> dict:
        """Why no position is left for this load (see UnplacedLoad)."""
        options = self.candidates[load.id]
        if not options:
            usable = [r.capacity for r in self.room_by_id.values() if r.room_type != "other"]
            return {"kind": "capacity", "max_capacity": max(usable, default=0)}

        teacher_busy = no_free_room = 0
        group_busy: dict[int, int] = {}
        for day in self.days:
            for slot in self.slots:
                t_busy = (load.teacher_id, day, slot.id) in self.teacher_busy
                busy_groups = [g for g in load.group_ids if (g, day, slot.id) in self.group_busy]
                teacher_busy += t_busy
                for g in busy_groups:
                    group_busy[g] = group_busy.get(g, 0) + 1
                if (
                    not t_busy
                    and not busy_groups
                    and all((room.id, day, slot.id) in self.room_busy for _, _, room in options)
                ):
                    no_free_room += 1
        return {
            "kind": "time",
            "total_slots": len(self.days) * len(self.slots),
            "teacher_busy": teacher_busy,
            "group_busy": group_busy,
            "no_free_room": no_free_room,
            "fitting_rooms": len(options),
        }


def plan(
    rooms: list[PRoom],
    loads: list[PLoad],
    slots: list[PSlot],
    days: list[int] | range = range(1, 7),
) -> PlanResult:
    return _Planner(rooms, loads, slots, days).run()

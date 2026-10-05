"""Assert-based checks for the pure scheduler core and room rules (no DB).

Run: python test_planner.py
"""
from collections import Counter

from room_rules import evaluate_room
from schedule_planner import PLoad, PRoom, PSlot, plan

SLOTS = [PSlot(id=100 + n, number=n) for n in range(1, 7)]
SLOT_NUMBER = {s.id: s.number for s in SLOTS}


def test_week_is_spread_evenly():
    """10 hours for one group (5 subjects x 2h): 2 pairs Mon-Fri, no Saturday,
    no windows, and one subject never twice on the same day."""
    rooms = [PRoom(id=1, building_id=1, capacity=30, room_type="classroom")]
    loads = [
        PLoad(id=i, teacher_id=i, lesson_type="practice", hours=2, group_ids=(1,), students=20)
        for i in range(1, 6)
    ]
    result = plan(rooms, loads, SLOTS)
    assert result.unplaced == 0, result
    per_day = Counter(p.day_of_week for p in result.placements)
    assert per_day == {1: 2, 2: 2, 3: 2, 4: 2, 5: 2}, per_day
    for day in per_day:
        numbers = sorted(SLOT_NUMBER[p.time_slot_id] for p in result.placements if p.day_of_week == day)
        assert numbers[-1] - numbers[0] == len(numbers) - 1, f"window on day {day}: {numbers}"
    for load in loads:
        days = [p.day_of_week for p in result.placements if p.academic_load_id == load.id]
        assert len(set(days)) == len(days), f"load {load.id} twice on one day: {days}"


def test_heavy_group_not_crammed():
    """22 hours for one group: never more than 5 pairs a day and days differ
    by at most one pair."""
    rooms = [PRoom(id=1, building_id=1, capacity=30, room_type="classroom")]
    loads = [
        PLoad(id=i, teacher_id=i, lesson_type="practice", hours=2, group_ids=(1,), students=20)
        for i in range(1, 12)
    ]
    result = plan(rooms, loads, SLOTS)
    assert result.unplaced == 0
    per_day = Counter(p.day_of_week for p in result.placements)
    assert max(per_day.values()) <= 5, per_day
    weekdays = [per_day.get(d, 0) for d in range(1, 6)]
    assert max(weekdays) - min(weekdays) <= 1, per_day


def test_two_hours_stay_on_one_day():
    """A light group (2 hours) isn't dragged in for a single pair twice a week."""
    rooms = [PRoom(id=1, building_id=1, capacity=30, room_type="classroom")]
    loads = [
        PLoad(id=1, teacher_id=1, lesson_type="lecture", hours=1, group_ids=(1,), students=20),
        PLoad(id=2, teacher_id=2, lesson_type="lecture", hours=1, group_ids=(1,), students=20),
    ]
    result = plan(rooms, loads, SLOTS)
    assert len({p.day_of_week for p in result.placements}) == 1, result.placements


def test_programmers_lab_goes_to_computer_class():
    rooms = [
        PRoom(id=1, building_id=1, capacity=30, room_type="classroom"),
        PRoom(id=2, building_id=1, capacity=30, room_type="laboratory"),
        PRoom(id=3, building_id=1, capacity=30, room_type="computer_class"),
    ]
    load = PLoad(id=1, teacher_id=1, lesson_type="lab", hours=2, group_ids=(1,),
                 students=20, room_type="computer_class")
    result = plan(rooms, [load], SLOTS)
    assert {p.room_id for p in result.placements} == {3}, result.placements
    assert result.with_warnings == 0


def test_mismatch_is_soft():
    """No computer class at all: the lab is still placed, but flagged."""
    rooms = [PRoom(id=1, building_id=1, capacity=30, room_type="classroom")]
    load = PLoad(id=1, teacher_id=1, lesson_type="lab", hours=1, group_ids=(1,),
                 students=20, room_type="computer_class")
    result = plan(rooms, [load], SLOTS)
    assert result.unplaced == 0 and result.with_warnings == 1, result


def test_lecture_prefers_lecture_hall():
    rooms = [
        PRoom(id=1, building_id=1, capacity=62, room_type="computer_class"),
        PRoom(id=2, building_id=1, capacity=62, room_type="lecture_hall"),
        PRoom(id=3, building_id=1, capacity=60, room_type="classroom"),
    ]
    load = PLoad(id=1, teacher_id=1, lesson_type="lecture", hours=1, group_ids=(1, 2), students=55)
    result = plan(rooms, [load], SLOTS)
    assert result.placements[0].room_id == 2, result.placements


def test_own_department_lab_preferred():
    chem, phys = 10, 20
    rooms = [
        PRoom(id=1, building_id=1, capacity=20, room_type="laboratory", department_id=phys),
        PRoom(id=2, building_id=1, capacity=20, room_type="laboratory", department_id=chem),
    ]
    load = PLoad(id=1, teacher_id=1, lesson_type="lab", hours=1, group_ids=(1,),
                 students=15, teacher_department_id=chem)
    result = plan(rooms, [load], SLOTS)
    assert result.placements[0].room_id == 2 and result.with_warnings == 0, result


def test_capacity_hard_and_other_rooms_unused():
    rooms = [
        PRoom(id=1, building_id=1, capacity=10, room_type="classroom"),
        PRoom(id=2, building_id=1, capacity=80, room_type="other"),
    ]
    load = PLoad(id=1, teacher_id=1, lesson_type="practice", hours=2, group_ids=(1,), students=40)
    result = plan(rooms, [load], SLOTS)
    assert result.placements == [] and result.unplaced == 2, result


def test_no_double_booking_many_groups():
    rooms = [PRoom(id=r, building_id=1, capacity=30, room_type="classroom") for r in range(1, 4)]
    loads = []
    lid = 1
    for g in range(1, 7):
        for t in range(3):
            loads.append(PLoad(id=lid, teacher_id=t + 1, lesson_type="practice", hours=2,
                               group_ids=(g,), students=20))
            lid += 1
    result = plan(rooms, loads, SLOTS)
    assert result.unplaced == 0, result.unplaced
    by_load = {l.id: l for l in loads}
    for key in (
        lambda p: (p.room_id, p.day_of_week, p.time_slot_id),
        lambda p: (by_load[p.academic_load_id].teacher_id, p.day_of_week, p.time_slot_id),
        lambda p: (by_load[p.academic_load_id].group_ids[0], p.day_of_week, p.time_slot_id),
    ):
        assert max(Counter(key(p) for p in result.placements).values()) == 1


def test_room_rules_warnings():
    common = dict(teacher_department_id=1, room_capacity=30, room_department_id=None,
                  room_department_name=None, students_total=20)
    fit = evaluate_room(lesson_type="lecture", required_room_type=None, room_type="computer_class", **common)
    assert any("Лекция в помещении «Компьютерный класс»" in w for w in fit.warnings), fit
    fit = evaluate_room(lesson_type="lecture", required_room_type=None, room_type="classroom", **common)
    assert fit.warnings == [], fit
    fit = evaluate_room(lesson_type="lab", required_room_type="computer_class", room_type="laboratory", **common)
    assert any("Лабораторная" in w and "Компьютерный класс" in w for w in fit.warnings), fit
    fit = evaluate_room(
        lesson_type="lab", required_room_type=None, room_type="laboratory",
        teacher_department_id=1, room_capacity=18, room_department_id=2,
        room_department_name="Физика", students_total=25,
    )
    assert any("Вместимость 18 < 25" in w for w in fit.warnings), fit
    assert any("кафедрой «Физика»" in w for w in fit.warnings), fit


def test_unplaced_are_explained():
    """Too big for every room -> capacity; teacher has more hours than slots -> time."""
    slots = SLOTS[:2]
    rooms = [PRoom(id=1, building_id=1, capacity=30, room_type="classroom")]
    big = PLoad(id=1, teacher_id=1, lesson_type="practice", hours=1, group_ids=(1,), students=80)
    busy = PLoad(id=2, teacher_id=2, lesson_type="practice", hours=3, group_ids=(2,), students=20)
    result = plan(rooms, [big, busy], slots, days=[1])
    by_load = {u.academic_load_id: u for u in result.unplaced_loads}
    assert result.unplaced == 2, result
    assert by_load[1].hours == 1 and by_load[1].diagnosis == {"kind": "capacity", "max_capacity": 30}
    diag = by_load[2].diagnosis
    assert by_load[2].hours == 1 and diag["kind"] == "time", diag
    assert diag["total_slots"] == 2 and diag["teacher_busy"] == 2 and diag["group_busy"] == {2: 2}, diag


def run_checks():
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print("  ok", name)
    print("OK")


if __name__ == "__main__":
    run_checks()

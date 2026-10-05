"""A shared lesson may exceed room capacity and must reach every group."""
import asyncio

from main import AssignRequest, assign_schedule
from publication import snapshot
from test_validation import make_session, seed_minimal


async def run_checks():
    session, engine = await make_session()
    try:
        data = await seed_minimal(session)
        result = await assign_schedule(AssignRequest(
            academic_load_id=data['lab_load'].id,
            day_of_week=2,
            time_slot_id=data['slot_1'].id,
            room_id=data['small_room'].id,
        ), session)
        assert result['id']
        assert any('не хватит 15 мест' in warning for warning in result['warnings'])
        teachers = []
        groups = await snapshot(session, teachers)
        shared = [g for g in groups if any(l['day_of_week'] == 2 for l in g['lessons'])]
        assert len(shared) == 2, shared
        lessons = [l for t in teachers for l in t['lessons'] if l['id'] == result['id']]
        assert len(lessons) == 1
        print('OK: two groups share an undersized room, warning returned, publication succeeds')
    finally:
        await session.close()
        await engine.dispose()


async def check_join(*, move=False, consume_all=False):
    from sqlalchemy import select
    from database.models import AcademicLoad, ScheduleItem
    from main import JoinStreamRequest, RoomOptionsRequest, join_stream, room_options
    from fastapi import HTTPException
    session, engine = await make_session()
    try:
        data = await seed_minimal(session)
        source, target = data['clean_load'], data['placed_load']
        source.teacher_id = target.teacher_id
        if consume_all:
            source.total_hours_per_week = target.total_hours_per_week = 1
        await session.commit()
        source_id, target_id = source.id, target.id
        current_id = None
        if move:
            current = await assign_schedule(AssignRequest(
                academic_load_id=source_id, day_of_week=2,
                time_slot_id=data['slot_1'].id, room_id=data['small_room'].id,
            ), session)
            current_id = current['id']
        req = JoinStreamRequest(
            academic_load_id=source_id, target_item_id=data['placed_item'].id,
            current_item_id=current_id, day_of_week=1,
            time_slot_id=data['slot_1'].id, room_id=data['small_room'].id,
        )
        # Match production request boundaries and avoid fixture identity caches.
        session.expunge_all()
        options = await room_options(RoomOptionsRequest(**req.model_dump()), session)
        option = next(o for o in options if o.room_id == req.room_id)
        assert option.is_valid and option.stream_item_id == req.target_item_id
        assert option.students_total == 60
        from main import ValidateRequest, validate_schedule
        hover = await validate_schedule(ValidateRequest(**{
            k: v for k, v in req.model_dump().items() if k not in ('room_id', 'target_item_id')
        }), session)
        assert hover.is_valid and any('поток' in w for w in hover.warnings)
        assert any('не хватит 40 мест' in w for w in option.warnings)
        result = await join_stream(req, session)
        assert any('не хватит 40 мест' in w for w in result['warnings'])
        session.expunge_all()
        items = (await session.scalars(select(ScheduleItem))).all()
        assert len(items) == 1
        assert items[0].academic_load_id == result['academic_load_id']
        from main import get_load_or_404
        stream = await get_load_or_404(session, result['academic_load_id'])
        assert len(stream.groups) == 2 and stream.total_hours_per_week == 1
        for original_id in (source_id, target_id):
            original = await session.get(AcademicLoad, original_id)
            if consume_all:
                assert original is None
            else:
                assert original.total_hours_per_week == 1
        teachers = []
        groups = await snapshot(session, teachers)
        assert len([g for g in groups if g['lessons']]) == 2
        assert len([l for t in teachers for l in t['lessons']]) == 1
        # A repeated request must not consume a second hour or duplicate the stream.
        try:
            await join_stream(req, session)
        except HTTPException as exc:
            assert exc.status_code in (404, 409)
        else:
            raise AssertionError('Repeated join accepted')
        assert len((await session.scalars(select(ScheduleItem))).all()) == 1
        print(f'OK: join move={move}, consume_all={consume_all}; hours and publication preserved')
    finally:
        await session.close()
        await engine.dispose()


async def check_rejections():
    from main import JoinStreamRequest, join_stream
    from fastapi import HTTPException
    from sqlalchemy import select
    from database.models import ScheduleItem
    for failure in ('teacher', 'same_groups', 'moved_target', 'group_conflict', 'no_hours'):
        session, engine = await make_session()
        try:
            data = await seed_minimal(session)
            source, target = data['clean_load'], data['placed_load']
            source_id = source.id
            if failure != 'teacher':
                source.teacher_id = target.teacher_id
            if failure == 'same_groups':
                source_id = target.id
            if failure == 'no_hours':
                source.total_hours_per_week = 0
            if failure == 'group_conflict':
                # A separate lesson for the source's group in the target slot.
                from database.models import AcademicLoad, AcademicLoadGroup
                from main import get_load_or_404
                source_with_groups = await get_load_or_404(session, source.id)
                other = AcademicLoad(subject_id=source.subject_id, teacher_id=data['lab_load'].teacher_id,
                                     lesson_type='lecture', total_hours_per_week=1)
                session.add(other)
                await session.flush()
                session.add(AcademicLoadGroup(academic_load_id=other.id, group_id=source_with_groups.groups[0].id))
                session.add(ScheduleItem(academic_load_id=other.id, day_of_week=1,
                                        time_slot_id=data['slot_1'].id, room_id=data['lab_room'].id))
            await session.commit()
            req = JoinStreamRequest(academic_load_id=source_id, target_item_id=data['placed_item'].id,
                                    day_of_week=2 if failure == 'moved_target' else 1,
                                    time_slot_id=data['slot_1'].id, room_id=data['small_room'].id)
            before = len((await session.scalars(select(ScheduleItem))).all())
            try:
                await join_stream(req, session)
            except HTTPException as exc:
                assert exc.status_code == 409, exc
            else:
                raise AssertionError(f'Accepted {failure}')
            assert len((await session.scalars(select(ScheduleItem))).all()) == before
            print(f'OK: rejects {failure} without changing schedule')
        finally:
            await session.close()
            await engine.dispose()


if __name__ == '__main__':
    asyncio.run(run_checks())
    for move in (False, True):
        for consume_all in (False, True):
            asyncio.run(check_join(move=move, consume_all=consume_all))
    asyncio.run(check_rejections())

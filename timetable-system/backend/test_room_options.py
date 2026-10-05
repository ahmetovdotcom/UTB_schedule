"""Batch parity and bounded SQL checks on an isolated in-memory database."""
import asyncio

from fastapi import HTTPException
from sqlalchemy import event

from database.models import Room
from main import RoomOptionsRequest, ValidateRequest, room_options, validate_schedule
from test_validation import make_session, seed_minimal


async def run_checks():
    session, engine = await make_session()
    data = await seed_minimal(session)
    load_ids = [data[key].id for key in ('lab_load', 'clean_load', 'placed_load')]
    slot_id = data['slot_1'].id
    item_id = data['placed_item'].id
    building_id = data['small_room'].building_id
    department_id = data['pc_room'].department_id
    for load_id in load_ids:
        for current_item_id in (None, item_id):
            req = RoomOptionsRequest(
                academic_load_id=load_id, day_of_week=1,
                time_slot_id=slot_id, current_item_id=current_item_id,
            )
            results = await room_options(req, session)
            assert len(results) == 3
            for result in results:
                single = await validate_schedule(ValidateRequest(
                    **req.model_dump(), room_id=result.room_id,
                ), session)
                assert result.model_dump(include={'is_valid', 'errors', 'warnings'}) == single.model_dump()

    req = RoomOptionsRequest(academic_load_id=load_ids[0], day_of_week=1, time_slot_id=slot_id)
    async def count_queries():
        count = 0
        def executed(*args):
            nonlocal count
            count += 1
        session.expunge_all()
        event.listen(engine.sync_engine, 'before_cursor_execute', executed)
        try:
            results = await room_options(req, session)
            return count, len(results)
        finally:
            event.remove(engine.sync_engine, 'before_cursor_execute', executed)

    small_count, _ = await count_queries()
    session.add_all([
        Room(building_id=building_id, department_id=department_id, number=f'Test-{i}',
             capacity=30, room_type='classroom', floor=1)
        for i in range(100)
    ])
    await session.commit()
    large_count, size = await count_queries()
    assert size == 103
    assert large_count == small_count, (small_count, large_count)

    for override in ({'academic_load_id': -1}, {'time_slot_id': -1}, {'current_item_id': -1}):
        try:
            await room_options(RoomOptionsRequest(**(req.model_dump() | override)), session)
        except HTTPException as exc:
            assert exc.status_code == 404
        else:
            raise AssertionError(f'Missing 404 for {override}')
    await session.close()
    await engine.dispose()
    print(f'OK: batch matches individual validation; 3 and 103 rooms both use {small_count} SQL queries')


if __name__ == '__main__':
    asyncio.run(run_checks())

"""Integration on isolated databases. Set UTB_PROJECT_DIR if UTB2 is not a sibling."""
import asyncio
from copy import deepcopy
import os
from pathlib import Path
import sys
import tempfile
from unittest.mock import patch

import httpx
from sqlalchemy import delete, event, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

root = Path(os.getenv('UTB_PROJECT_DIR', str(Path(__file__).resolve().parents[2] / 'UTB2')))
sys.path.insert(0, str(root / 'backend'))
KEY = 'test-only-publication-secret-key-123456'
with patch.dict(os.environ, {'POSTGRES_USER': 'test', 'POSTGRES_PASSWORD': 'test',
    'POSTGRES_HOST': 'localhost', 'POSTGRES_PORT': '5432', 'POSTGRES_DB': 'test', 'PUBLISH_SECRET_KEY': KEY}):
    from app.main import app as student_app
    from app.core.database import Base as StudentBase, get_db
    from app.models.models import Group, Schedule, User as LegacyUser

import publication
from auth import require_user
from main import app as staff_app
from database.models import (Base, StudentGroup, AcademicLoad, AcademicLoadGroup, ScheduleItem,
                             Subject, Teacher, Room, Building, TimeSlot, get_session)


async def run():
    assert Path(__file__).with_name('publication_schema.py').read_bytes() == (root / 'backend/app/schemas/publication.py').read_bytes()
    with tempfile.TemporaryDirectory() as directory:
        engines = [create_async_engine(f'sqlite+aiosqlite:///{directory}/staff.db'),
                   create_async_engine(os.getenv('STUDENT_TEST_DATABASE_URL', f'sqlite+aiosqlite:///{directory}/student.db'))]
        @event.listens_for(engines[1].sync_engine, 'connect')
        def enable_fks(connection, _):
            if engines[1].dialect.name == 'sqlite':
                connection.execute('PRAGMA foreign_keys=ON')
        for engine, base in zip(engines, (Base, StudentBase)):
            async with engine.begin() as connection:
                await connection.run_sync(base.metadata.create_all)
        staff_sessions, student_sessions = [async_sessionmaker(e, expire_on_commit=False) for e in engines]
        async def staff_db():
            async with staff_sessions() as s:
                yield s
        async def student_db():
            async with student_sessions() as s:
                yield s
        staff_app.dependency_overrides[get_session] = staff_db
        staff_app.dependency_overrides[require_user] = lambda: None
        student_app.dependency_overrides[get_db] = student_db
        try:
            async with student_sessions() as s:
                s.add_all([Group(id=80, title='A'), Group(id=81, title='Legacy')])
                await s.flush()
                s.add(LegacyUser(telegram_id=123, group_id=81, is_approved=True))
                s.add(Schedule(group_id=81, day_of_week=1, time='09:00-10:00', subject='Old', room='1'))
                await s.commit()
            async with staff_sessions() as s:
                a = StudentGroup(code='A', students_count=20, course=1)
                b = StudentGroup(code='B', students_count=20, course=1)
                building, subject = Building(name='Корпус 1'), Subject(name='Математика')
                teacher = Teacher(full_name='Teacher')
                slot = TimeSlot(slot_number=1, start_time='8:30', end_time='9:20')
                s.add_all([a, b, building, subject, teacher, slot])
                await s.flush()
                room = Room(building_id=building.id, number='101', capacity=50, floor=1)
                load = AcademicLoad(subject_id=subject.id, teacher_id=teacher.id, lesson_type='lecture', total_hours_per_week=1, groups=[a, b])
                s.add_all([room, load])
                await s.flush()
                s.add(ScheduleItem(academic_load_id=load.id, room_id=room.id, time_slot_id=slot.id, day_of_week=1))
                await s.commit()
                aid, bid = a.id, b.id
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=student_app), base_url='http://student') as students, \
                       httpx.AsyncClient(transport=httpx.ASGITransport(app=staff_app), base_url='http://staff') as staff:
                assert (await students.get('/api/v1/groups')).json() == []
                assert (await students.get('/api/v1/publication')).json() == {'published': False}
                assert (await students.get('/api/v1/teachers')).json() == []
                for method, path in [('POST','/groups'), ('DELETE','/groups/80'), ('PUT','/schedules/80/day/1'),
                    ('DELETE','/schedules/80/day/1'), ('POST','/users'), ('GET','/users'), ('PATCH','/users/123/approve')]:
                    assert (await students.request(method, '/api/v1'+path, json={})).status_code in (404,405)
                assert (await students.post('/api/v1/publication', json={})).status_code == 401
                real_send, real_client = publication.send_publication, httpx.AsyncClient
                def local_client(**kwargs):
                    return real_client(transport=httpx.ASGITransport(app=student_app), **kwargs)
                async def send(url,key,payload):
                    with patch.object(publication.httpx, 'AsyncClient', local_client):
                        return await real_send(url,key,payload)
                headers = {'Authorization': f'Bearer {KEY}'}
                with patch.dict(os.environ, {'STUDENT_PUBLISH_URL':'http://localhost/api/v1/publication', 'PUBLISH_SECRET_KEY':KEY}), patch.object(publication,'send_publication',send):
                    result = await staff.post('/api/publication')
                    assert result.status_code == 200, result.text
                    assert result.json()['groups_count'] == 2 and result.json()['lessons_count'] == 2
                    teachers = (await students.get('/api/v1/teachers')).json()
                    assert len(teachers) == 1
                    tid = teachers[0]['id']
                    teacher_lessons = (await students.get(f'/api/v1/teachers/{tid}/schedule?day=1')).json()
                    assert len(teacher_lessons) == 1  # A combined lecture appears only once.
                    assert set(teacher_lessons[0]['groups']) == {'A', 'B'}
                    assert teacher_lessons[0]['time'] == '08:30-09:20'
                    assert (await students.get(f'/api/v1/teachers/{tid}/schedule?day=2')).json() == []
                    assert (await students.get(f'/api/v1/teachers/{tid}/schedule?day=8')).status_code == 422
                    assert (await students.get('/api/v1/teachers/99999/schedule?day=1')).status_code == 404
                    assert (await students.post('/api/v1/teachers', json={})).status_code == 405

                    groups = (await students.get('/api/v1/groups')).json()
                    assert groups[0] == {'id':80, 'title':'A'}
                    old_bid = groups[1]['id']
                    lesson = (await students.get('/api/v1/schedules/80?day=1')).json()[0]
                    assert lesson['time'] == '08:30-09:20' and lesson['room'] == 'Корпус 1 · 101'
                    png = await students.get('/api/v1/schedules/80/wallpaper.png?day=1')
                    assert png.status_code == 200 and png.content.startswith(b'\x89PNG')
                    # A conflict in the draft must not reach the receiving application.
                    async with staff_sessions() as s:
                        extra_room = Room(building_id=1,number='102',capacity=50,floor=1)
                        s.add(extra_room); await s.flush()
                        conflicting = ScheduleItem(academic_load_id=1,room_id=extra_room.id,time_slot_id=1,day_of_week=1)
                        s.add(conflicting); await s.commit()
                        conflict_id = conflicting.id
                    assert (await staff.post('/api/publication')).status_code == 409
                    async with staff_sessions() as s:
                        await s.execute(delete(ScheduleItem).where(ScheduleItem.id == conflict_id))
                        await s.commit()
                    async with student_sessions() as s:
                        assert (await s.get(LegacyUser,123)).group_id is None
                        assert await s.get(Group,81) is None
                    async with staff_sessions() as s:
                        first = deepcopy((await s.scalar(select(publication.Publication))).payload)
                        (await s.get(StudentGroup,aid)).code = 'Renamed'
                        (await s.get(Subject,1)).name = 'Физика'
                        await s.commit()
                    assert (await students.get('/api/v1/groups')).json()[0]['title'] == 'A'
                    assert (await students.post('/api/v1/publication',json=first,headers=headers)).status_code == 200
                    assert (await students.post('/api/v1/publication',json=first,headers={'Authorization':'Bearer wrong'})).status_code == 401
                    other = deepcopy(first); other['source_id'] = '00000000-0000-4000-8000-000000000001'
                    assert (await students.post('/api/v1/publication',json=other,headers=headers)).status_code == 409
                    bad = deepcopy(first); bad['groups'][0]['title'] = 'Different same revision'
                    assert (await students.post('/api/v1/publication',json=bad,headers=headers)).status_code == 409
                    result = await staff.post('/api/publication')
                    assert result.status_code == 200, result.text
                    assert {g['title']:g['id'] for g in (await students.get('/api/v1/groups')).json()}['Renamed'] == 80
                    assert (await students.get('/api/v1/schedules/80/wallpaper.png?day=1')).content != png.content
                    updated_teacher_lessons = (await students.get(f'/api/v1/teachers/{tid}/schedule?day=1')).json()
                    assert updated_teacher_lessons[0]['subject'] == 'Физика'
                    assert set(updated_teacher_lessons[0]['groups']) == {'Renamed', 'B'}
                    assert (await students.post('/api/v1/publication',json=first,headers=headers)).status_code == 409
                    malformed=deepcopy(first); malformed['revision']=100; malformed['groups'].append(malformed['groups'][0])
                    assert (await students.post('/api/v1/publication',json=malformed,headers=headers)).status_code == 422
                    async with staff_sessions() as s:
                        await s.execute(delete(AcademicLoadGroup).where(AcademicLoadGroup.group_id==bid))
                        await s.execute(delete(StudentGroup).where(StudentGroup.id==bid))
                        s.add(StudentGroup(code='C',students_count=10,course=1))
                        await s.commit()
                    assert (await staff.post('/api/publication')).status_code == 200
                    groups = (await students.get('/api/v1/groups')).json()
                    assert {g['title'] for g in groups} == {'Renamed','C'}
                    assert old_bid not in {g['id'] for g in groups}
                    assert (await students.get(f'/api/v1/schedules/{old_bid}?day=1')).status_code == 404
                    async with staff_sessions() as s:
                        latest=await s.scalar(select(publication.Publication).order_by(publication.Publication.id.desc()))
                        failure=deepcopy(latest.payload)
                    failure['revision']+=100; failure['groups']=[]; failure['teachers']=[]
                    teacher_before_failure = (await students.get(f'/api/v1/teachers/{tid}/schedule?day=1')).json()
                    async def failing_commit(self):
                        raise RuntimeError('Simulated commit failure')
                    try:
                        with patch.object(AsyncSession,'commit',failing_commit):
                            await students.post('/api/v1/publication',json=failure,headers=headers)
                    except RuntimeError:
                        pass
                    else:
                        raise AssertionError('Expected failure')
                    assert (await students.get('/api/v1/groups')).json() == groups
                    assert (await students.get(f'/api/v1/teachers/{tid}/schedule?day=1')).json() == teacher_before_failure
                    async def disconnected(*args):
                        raise httpx.ConnectError('offline')
                    with patch.object(publication,'send_publication',disconnected):
                        assert (await staff.post('/api/publication')).status_code == 502
                    assert (await students.get('/api/v1/groups')).json() == groups
                    async with staff_sessions() as s:
                        await s.execute(delete(ScheduleItem)); await s.execute(delete(AcademicLoadGroup)); await s.execute(delete(StudentGroup))
                        await s.commit()
                    assert (await staff.post('/api/publication')).status_code == 200
                    assert (await students.get('/api/v1/groups')).json() == []
                    assert (await students.get(f'/api/v1/teachers/{tid}/schedule?day=1')).json() == []
                    async with staff_sessions() as s:
                        await s.execute(delete(AcademicLoad)); await s.execute(delete(Teacher)); await s.commit()
                    assert (await staff.post('/api/publication')).status_code == 200
                    assert (await students.get('/api/v1/teachers')).json() == []
                    assert (await students.get(f'/api/v1/teachers/{tid}/schedule?day=1')).status_code == 404
                    async with student_sessions() as s:
                        assert (await s.scalars(select(Schedule))).all() == []
                    # Concurrent out-of-order deliveries never roll the receiver backwards.
                    async with staff_sessions() as s:
                        current = await s.scalar(select(publication.Publication).order_by(publication.Publication.id.desc()))
                        newer = deepcopy(current.payload)
                    older = deepcopy(newer)
                    older['revision'] += 10; newer['revision'] += 11
                    deliveries = await asyncio.gather(*[
                        students.post('/api/v1/publication',json=value,headers=headers) for value in (newer,older)])
                    assert all(result.status_code in (200,409) for result in deliveries)
                    assert (await students.get('/api/v1/publication')).json()['revision'] == newer['revision']
                with patch.dict(os.environ,{'STUDENT_PUBLISH_URL':'','PUBLISH_SECRET_KEY':''}):
                    assert (await staff.post('/api/publication')).status_code == 503
            print('OK: full publication, stable IDs, drafts, removals, replay, validation, rollback, failure, wallpapers, read-only students')
        finally:
            staff_app.dependency_overrides.clear(); student_app.dependency_overrides.clear()
            for engine in engines:
                await engine.dispose()

if __name__ == '__main__':
    asyncio.run(run())

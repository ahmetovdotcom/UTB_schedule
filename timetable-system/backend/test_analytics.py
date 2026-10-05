"""Analytics ingestion, aggregate semantics and owner-only access on isolated DBs."""
import asyncio
import time
from datetime import datetime
from tempfile import TemporaryDirectory
from uuid import uuid4
from unittest.mock import patch

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from test_publication import student_app, staff_app, StudentBase, Base, get_db, get_session, KEY
from app.models.models import Group, PublishedTeacher
from app.routers.analytics import UsageEvent, LOCAL_TZ
from auth import AdminGrant, User, require_user
import analytics

async def run():
    with TemporaryDirectory() as directory:
        engines = [create_async_engine(f'sqlite+aiosqlite:///{directory}/{name}.db') for name in ['staff', 'students']]
        for engine, base in zip(engines, [Base, StudentBase]):
            async with engine.begin() as conn:
                await conn.run_sync(base.metadata.create_all)
        staff_sessions, student_sessions = [async_sessionmaker(engine, expire_on_commit=False) for engine in engines]
        async def staff_db():
            async with staff_sessions() as db: yield db
        async def student_db():
            async with student_sessions() as db: yield db
        staff_app.dependency_overrides[get_session] = staff_db
        student_app.dependency_overrides[get_db] = student_db
        headers = {'Authorization': f'Bearer {KEY}'}
        try:
            async with student_sessions() as db:
                db.add(Group(id=1, title='Группа А'))
                db.add(PublishedTeacher(id=2, title='Преподаватель Б', lessons=[]))
                await db.commit()
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=student_app), base_url='http://students') as client:
                assert (await client.get('/api/v1/analytics/summary')).status_code == 401
                assert (await client.get('/api/v1/analytics/summary?days=181', headers=headers)).status_code == 422
                empty = (await client.get('/api/v1/analytics/summary?days=7', headers=headers)).json()
                assert empty['totals']['visitors'] == 0 and len(empty['daily']) == 7
                visitor, visit = str(uuid4()), str(uuid4())
                event = dict(id=str(uuid4()), visitor=visitor, visit=visit, kind='open', mode='student', device='phone', browser='Safari')
                for _ in range(2):
                    assert (await client.post('/api/v1/analytics/events', json=event)).status_code == 204
                invalid = dict(event, id=str(uuid4()), kind='schedule', target_id=999)
                assert (await client.post('/api/v1/analytics/events', json=invalid)).status_code == 404
                assert (await client.post('/api/v1/analytics/events', json=dict(event, referrer='https://host/path?secret'))).status_code == 422
                for target, mode in [(1, 'student'), (2, 'teacher')]:
                    record = dict(event, id=str(uuid4()), kind='schedule', mode=mode, target_id=target)
                    assert (await client.post('/api/v1/analytics/events', json=record)).status_code == 204
                assert (await client.post('/api/v1/analytics/events', json=dict(event, id=str(uuid4()), visit=str(uuid4())))).status_code == 204
                # Same browser, two visits; one combined teacher lesson view.
                data = (await client.get('/api/v1/analytics/summary?days=7', headers=headers)).json()
                assert data['totals'] == dict(visitors=1, visits=2, opens=2, schedule_views=2, errors=0, active=1, returning=1), data
                assert data['groups'][0]['name'] == 'Группа А' and data['groups'][0]['count'] == 1
                assert data['teachers'][0]['name'] == 'Преподаватель Б'
                assert sum(row['visits'] for row in data['daily']) == 2
                async with student_sessions() as db:
                    rows = (await db.scalars(select(UsageEvent))).all()
                    assert len(rows) == 4 and all(row.visitor != visitor and row.visit != visit for row in rows)
                    old = rows[0]
                    old.created_at = int(time.time()) - 181 * 86400
                    old.day = datetime.fromtimestamp(old.created_at, LOCAL_TZ).date().isoformat()
                    old_id = old.id
                    await db.commit()
                await client.post('/api/v1/analytics/events', json=dict(event, id=str(uuid4())))
                async with student_sessions() as db:
                    assert await db.get(UsageEvent, old_id) is None
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=staff_app), base_url='http://staff') as client:
                assert (await client.get('/api/admin/analytics')).status_code == 401
                user = User(id=1, username='owner', password_hash='not-used')
                staff_app.dependency_overrides[require_user] = lambda: user
                assert (await client.get('/api/admin/analytics')).status_code == 403
                async with staff_sessions() as db:
                    db.add(user); db.add(AdminGrant(user_id=1)); await db.commit()
                real_client = httpx.AsyncClient
                def local_client(**kwargs):
                    return real_client(transport=httpx.ASGITransport(app=student_app), **kwargs)
                with patch.object(analytics, 'destination', return_value=('http://students/api/v1/publication', KEY)), patch.object(analytics.httpx, 'AsyncClient', local_client):
                    response = await client.get('/api/admin/analytics?days=7')
                    assert response.status_code == 200, response.text
                    assert response.headers['cache-control'] == 'no-store'
                    assert response.json()['totals']['schedule_views'] == 2
                async with staff_sessions() as db:
                    await db.delete(await db.get(AdminGrant, 1)); await db.commit()
                assert (await client.get('/api/admin/analytics')).status_code == 403
            print('OK: private reporting, role grant/revoke, deduplication, sessions, aggregates, retention and input validation')
        finally:
            staff_app.dependency_overrides.clear(); student_app.dependency_overrides.clear()
            for engine in engines: await engine.dispose()

if __name__ == '__main__': asyncio.run(run())

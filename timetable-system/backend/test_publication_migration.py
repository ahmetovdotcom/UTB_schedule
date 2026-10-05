"""Backfill stable publication identities without changing existing group data."""
import asyncio
from uuid import UUID
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from database.models import Base, _create_and_migrate

async def run():
    engine = create_async_engine('sqlite+aiosqlite://')
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
        await connection.execute(text('ALTER TABLE student_groups DROP COLUMN publication_key'))
        await connection.execute(text("INSERT INTO student_groups(id,code,students_count,course) VALUES(4,'ИС-21',20,2)"))
        await connection.run_sync(_create_and_migrate)
        row = (await connection.execute(text('SELECT * FROM student_groups'))).one()
        UUID(row.publication_key)
        assert row.code == 'ИС-21' and row.students_count == 20 and row.id == 4
        await connection.run_sync(_create_and_migrate)
        assert (await connection.execute(text('SELECT publication_key FROM student_groups'))).scalar_one() == row.publication_key
    await engine.dispose()
    print('OK: legacy migration preserves groups and stable publication UUIDs')

if __name__ == '__main__':
    asyncio.run(run())

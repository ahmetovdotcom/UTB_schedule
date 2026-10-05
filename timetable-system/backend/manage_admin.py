"""Grant/revoke reporting access for an existing staff login."""
import argparse
import asyncio
from sqlalchemy import select
from auth import AdminGrant, User
from database.models import async_session, init_db

async def change(username, revoke=False):
    await init_db()
    async with async_session() as db:
        user = await db.scalar(select(User).where(User.username == username))
        if user is None:
            raise SystemExit("Пользователь не найден. Сначала создайте учётную запись.")
        grant = await db.get(AdminGrant, user.id)
        if revoke and grant:
            await db.delete(grant)
        elif not revoke and grant is None:
            db.add(AdminGrant(user_id=user.id))
        await db.commit()
    print("Доступ к статистике отозван." if revoke else "Доступ к статистике предоставлен.")

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('username')
    parser.add_argument('--revoke', action='store_true')
    args = parser.parse_args()
    asyncio.run(change(args.username, args.revoke))

"""Create an account or reset its password without public registration."""
import argparse
import asyncio
from getpass import getpass

from sqlalchemy import delete, select

from auth import LoginSession, User, hash_password
from database.models import async_session, init_db


async def save_user(username: str, password: str):
    await init_db()
    async with async_session() as session:
        user = await session.scalar(select(User).where(User.username == username))
        if user is None:
            session.add(User(username=username, password_hash=hash_password(password)))
        else:
            user.password_hash = hash_password(password)
            await session.execute(delete(LoginSession).where(LoginSession.user_id == user.id))
        await session.commit()


def main():
    parser = argparse.ArgumentParser(description="Создать пользователя или сменить пароль (завершает его сеансы)")
    parser.add_argument("username", help="Логин")
    args = parser.parse_args()
    username = args.username.strip()
    if not 1 <= len(username) <= 100:
        parser.error("Логин должен содержать от 1 до 100 символов")
    password = getpass("Пароль (не менее 12 символов): ")
    if not 12 <= len(password) <= 1024:
        parser.error("Пароль должен содержать от 12 до 1024 символов")
    if password != getpass("Повторите пароль: "):
        parser.error("Пароли не совпадают")
    asyncio.run(save_user(username, password))
    print(f"Учётная запись {username} сохранена.")


if __name__ == "__main__":
    main()

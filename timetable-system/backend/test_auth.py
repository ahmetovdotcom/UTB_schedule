"""HTTP auth checks on an isolated in-memory database. Run: python test_auth.py."""
import asyncio
import re
import time
from unittest.mock import AsyncMock, patch

import manage_users

from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import auth
from auth import COOKIE_NAME, LoginLimit, LoginSession, User, hash_password
from database.models import Base, get_session
from main import app


async def test_auth():
    engine = create_async_engine("sqlite+aiosqlite://")
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)

    async def get_test_session():
        async with sessions() as session:
            yield session

    app.dependency_overrides[get_session] = get_test_session
    password = "test-password-12345"
    credentials = {"username": "admin", "password": password}
    async with sessions() as session:
        session.add(User(username="admin", password_hash=hash_password(password)))
        await session.commit()
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="https://testserver",
                               headers={"X-Timetable-Request": "1"}) as client:
            for route_path, methods in app.openapi()["paths"].items():
                if route_path.startswith("/api/") and route_path != "/api/auth/login":
                    path = re.sub(r"\{[^}]+\}", "1", route_path)
                    for method in methods:
                        result = await client.request(method, path)
                        assert result.status_code == 401, (method, path, result.text)
            assert (await client.get("/docs")).status_code == 404
            assert (await client.post("/api/auth/login", json=credentials,
                                     headers={"X-Timetable-Request": ""})).status_code == 403
            for username in ["admin", "unknown"]:
                result = await client.post("/api/auth/login", json={"username": username, "password": "wrong"})
                assert result.status_code == 401
                assert result.json()["detail"] == "Неверный логин или пароль"
                assert COOKIE_NAME not in client.cookies
            result = await client.post("/api/auth/login", json=credentials)
            assert result.status_code == 200, result.text
            cookie = result.headers["set-cookie"].lower()
            assert "httponly" in cookie and "samesite=strict" in cookie and "path=/api" in cookie
            assert ("secure" in cookie) == auth.COOKIE_SECURE
            first_token = client.cookies.get(COOKIE_NAME)
            assert (await client.get("/api/auth/me")).json() == {"username": "admin", "is_admin": False}
            assert (await client.get("/api/initial-data")).status_code == 200
            async with sessions() as session:
                user = await session.scalar(select(User))
                stored_session = await session.scalar(select(LoginSession))
                assert password not in user.password_hash
                assert stored_session.token_hash != first_token
            assert (await client.post("/api/auth/login", json=credentials)).status_code == 200
            async with AsyncClient(transport=ASGITransport(app=app), base_url="https://testserver",
                                   cookies={COOKIE_NAME: first_token}) as other:
                assert (await other.get("/api/auth/me")).status_code == 401
            active_token = client.cookies.get(COOKIE_NAME)
            assert (await client.post("/api/auth/logout", headers={"X-Timetable-Request": ""})).status_code == 403
            preflight = await client.options("/api/auth/logout", headers={
                "Origin": "https://evil.example", "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "X-Timetable-Request"})
            assert "access-control-allow-origin" not in preflight.headers
            assert (await client.post("/api/auth/logout")).status_code == 204
            assert COOKIE_NAME not in client.cookies
            assert (await client.get("/api/initial-data")).status_code == 401
            client.cookies.set(COOKIE_NAME, active_token)
            assert (await client.get("/api/auth/me")).status_code == 401
            client.cookies.clear()
            assert (await client.post("/api/auth/login", json=credentials)).status_code == 200
            async with sessions() as session:
                stored_session = await session.scalar(select(LoginSession))
                stored_session.expires_at = int(time.time()) - 1
                await session.commit()
            assert (await client.get("/api/auth/me")).status_code == 401
            client.cookies.clear()
            client.cookies.set(COOKIE_NAME, "forged-token")
            assert (await client.get("/api/initial-data")).status_code == 401
            client.cookies.clear()
            async with sessions() as session:
                await session.execute(delete(LoginLimit))
                await session.commit()
            for _ in range(10):
                assert (await client.post("/api/auth/login", json={**credentials, "password": "wrong"})).status_code == 401
            limited = await client.post("/api/auth/login", json=credentials)
            assert limited.status_code == 429 and limited.headers["retry-after"] == "900"
            async with sessions() as session:
                for limit in (await session.scalars(select(LoginLimit))).all():
                    limit.expires_at = int(time.time()) - 1
                await session.commit()
            assert (await client.post("/api/auth/login", json=credentials)).status_code == 200
            # The CLI password reset invalidates active sessions without touching schedule data.
            with patch.object(manage_users, "init_db", new=AsyncMock()), patch.object(manage_users, "async_session", sessions):
                await manage_users.save_user("admin", "replacement-password-123")
            assert (await client.get("/api/auth/me")).status_code == 401
            assert (await client.post("/api/auth/login", json=credentials)).status_code == 401
            assert (await client.post("/api/auth/login", json={**credentials, "password": "replacement-password-123"})).status_code == 200
        print("OK: protected routes, login, cookies, CSRF, rotation, logout, expiry, throttling, password reset")
    finally:
        app.dependency_overrides.clear()
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(test_auth())

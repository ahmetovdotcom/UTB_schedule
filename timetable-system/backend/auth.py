"""Password authentication with revocable, database-backed browser sessions."""
from __future__ import annotations

import hashlib
import hmac
import os
import secrets
import time

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy import delete, select
from sqlalchemy.dialects.sqlite import insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column
from starlette.concurrency import run_in_threadpool

from database.models import Base, get_session

COOKIE_NAME = "timetable_session"
SESSION_SECONDS = 12 * 60 * 60
LOGIN_WINDOW = 15 * 60
COOKIE_SECURE = os.getenv("AUTH_COOKIE_SECURE", "true").lower() != "false"


class User(Base):
    __tablename__ = "auth_users"
    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(unique=True)
    password_hash: Mapped[str]


class AdminGrant(Base):
    __tablename__ = "auth_admin_grants"
    user_id: Mapped[int] = mapped_column(primary_key=True)


class LoginSession(Base):
    __tablename__ = "auth_sessions"
    token_hash: Mapped[str] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(index=True)
    expires_at: Mapped[int]


class LoginLimit(Base):
    __tablename__ = "auth_login_limits"
    key: Mapped[str] = mapped_column(primary_key=True)
    attempts: Mapped[int]
    expires_at: Mapped[int]


def hash_password(password: str, salt: str | None = None) -> str:
    salt = salt or secrets.token_hex(16)
    digest = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), n=32768,
                            r=8, p=3, maxmem=64 * 1024 * 1024).hex()
    return f"scrypt${salt}${digest}"


def verify_password(password: str, stored: str) -> bool:
    return hmac.compare_digest(hash_password(password, stored.split("$")[1]), stored)


DUMMY_HASH = hash_password("unused-random-password")


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


async def require_user(request: Request, session: AsyncSession = Depends(get_session)):
    # A non-simple header + restricted CORS prevents cross-origin form/JS writes,
    # including login CSRF. SameSite=Strict is an additional cookie safeguard.
    if request.method not in {"GET", "HEAD", "OPTIONS"}:
        if request.headers.get("X-Timetable-Request") != "1":
            raise HTTPException(403, "Недопустимый источник запроса")
    if request.url.path == "/api/auth/login" and request.method == "POST":
        return None
    token = request.cookies.get(COOKIE_NAME, "")
    login = await session.get(LoginSession, token_hash(token)) if token else None
    user = await session.get(User, login.user_id) if login and login.expires_at > time.time() else None
    if user is None:
        raise HTTPException(401, "Войдите в систему")
    return user


router = APIRouter(prefix="/api/auth", tags=["auth"])


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=100)
    password: str = Field(min_length=1, max_length=1024)


@router.post("/login")
async def login(data: LoginRequest, request: Request, response: Response,
                session: AsyncSession = Depends(get_session)):
    username = data.username.strip()
    now = int(time.time())
    await session.execute(delete(LoginLimit).where(LoginLimit.expires_at <= now))
    # Atomic counters persist across workers/restarts. Do not trust forwarded IPs.
    keys = [("user:" + username, 10), ("ip:" + (request.client.host if request.client else "unknown"), 100)]
    limited = False
    for raw_key, maximum in keys:
        key = token_hash(raw_key)
        stmt = insert(LoginLimit).values(key=key, attempts=1, expires_at=now + LOGIN_WINDOW)
        await session.execute(stmt.on_conflict_do_update(
            index_elements=[LoginLimit.key], set_={"attempts": LoginLimit.attempts + 1}))
        count = await session.scalar(select(LoginLimit.attempts).where(LoginLimit.key == key))
        limited = limited or count > maximum
    await session.commit()
    if limited:
        raise HTTPException(429, "Слишком много попыток входа. Попробуйте через 15 минут.",
                            headers={"Retry-After": str(LOGIN_WINDOW)})
    user = await session.scalar(select(User).where(User.username == username))
    valid = await run_in_threadpool(verify_password, data.password, user.password_hash if user else DUMMY_HASH)
    if not valid or user is None:
        raise HTTPException(401, "Неверный логин или пароль")
    await session.execute(delete(LoginSession).where(LoginSession.expires_at <= now))
    old_token = request.cookies.get(COOKIE_NAME)
    if old_token:
        await session.execute(delete(LoginSession).where(LoginSession.token_hash == token_hash(old_token)))
    token = secrets.token_urlsafe(32)
    session.add(LoginSession(token_hash=token_hash(token), user_id=user.id, expires_at=now + SESSION_SECONDS))
    await session.commit()
    response.set_cookie(COOKIE_NAME, token, max_age=SESSION_SECONDS, httponly=True,
                        secure=COOKIE_SECURE, samesite="strict", path="/api")
    response.headers["Cache-Control"] = "no-store"
    return {"username": user.username, "is_admin": await session.get(AdminGrant, user.id) is not None}


@router.get("/me")
async def me(response: Response, user: User = Depends(require_user), session: AsyncSession = Depends(get_session)):
    response.headers["Cache-Control"] = "no-store"
    return {"username": user.username, "is_admin": await session.get(AdminGrant, user.id) is not None}


@router.post("/logout", status_code=204)
async def logout(request: Request, response: Response, session: AsyncSession = Depends(get_session)):
    await session.execute(delete(LoginSession).where(
        LoginSession.token_hash == token_hash(request.cookies.get(COOKIE_NAME, ""))))
    await session.commit()
    response.delete_cookie(COOKIE_NAME, path="/api", secure=COOKIE_SECURE,
                           httponly=True, samesite="strict")

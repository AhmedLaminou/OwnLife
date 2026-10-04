"""Account creation (first run), login, logout, sessions, password change."""

from __future__ import annotations

import re
from datetime import date, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel, Field
from sqlalchemy import delete, func, select

from app.config import get_settings
from app.db import utcnow
from app.deps import DB, CurrentUser, get_current_session
from app.models import AuthSession, Profile, User
from app.security import (
    SESSION_COOKIE,
    hash_password,
    hash_token,
    login_throttle,
    new_session_token,
    verify_password,
)
from app.seed.defaults import seed_new_user
from app.serializers import iso, user_out
from app.services.timeutil import local_today, tz_of

router = APIRouter(prefix="/api/auth", tags=["auth"])

_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
# Verifying against this when the email is unknown keeps the response time the
# same either way, so timing does not reveal which emails have an account.
_DUMMY_HASH = hash_password("not-a-real-password-just-for-timing")


class SetupIn(BaseModel):
    email: str = Field(max_length=255)
    display_name: str = Field(min_length=1, max_length=120)
    password: str = Field(min_length=10, max_length=256)
    birth_date: date | None = None
    timezone: str = "UTC"


class LoginIn(BaseModel):
    email: str = Field(max_length=255)
    password: str = Field(max_length=256)


class ChangePasswordIn(BaseModel):
    current_password: str = Field(max_length=256)
    new_password: str = Field(min_length=10, max_length=256)


def _client_key(request: Request) -> str:
    return request.client.host if request.client else "local"


def _start_session(db, user: User, request: Request) -> str:
    token, token_hash = new_session_token()
    db.add(
        AuthSession(
            user_id=user.id,
            token_hash=token_hash,
            expires_at=utcnow() + timedelta(days=get_settings().session_days),
            user_agent=(request.headers.get("user-agent") or "")[:255],
            ip=_client_key(request)[:64],
        )
    )
    return token


def _set_cookie(response: Response, token: str) -> None:
    s = get_settings()
    response.set_cookie(
        SESSION_COOKIE,
        token,
        max_age=s.session_days * 86400,
        httponly=True,
        samesite="lax",
        secure=s.cookie_secure,
        path="/",
    )


@router.get("/status")
def auth_status(request: Request, db: DB) -> dict:
    needs_setup = (db.scalar(select(func.count()).select_from(User)) or 0) == 0
    user = None
    token = request.cookies.get(SESSION_COOKIE)
    if token:
        session = db.scalar(select(AuthSession).where(AuthSession.token_hash == hash_token(token)))
        if session and session.expires_at > utcnow():
            user = db.get(User, session.user_id)
    return {
        "needs_setup": needs_setup,
        "authenticated": user is not None and user.is_active,
        "user": user_out(user) if user else None,
        "registration_open": get_settings().allow_registration,
    }


@router.post("/setup")
def setup(body: SetupIn, request: Request, response: Response, db: DB) -> dict:
    has_users = (db.scalar(select(func.count()).select_from(User)) or 0) > 0
    if has_users and not get_settings().allow_registration:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "An account already exists. Log in instead.")
    email = body.email.strip().lower()
    if not _EMAIL.match(email):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "That email address does not look valid.")
    if db.scalar(select(User).where(User.email == email)):
        raise HTTPException(status.HTTP_409_CONFLICT, "An account with this email already exists.")
    tz = tz_of(body.timezone)
    user = User(email=email, display_name=body.display_name.strip(), password_hash=hash_password(body.password))
    db.add(user)
    db.flush()
    db.add(Profile(user_id=user.id, birth_date=body.birth_date, timezone=tz.key))
    seed_new_user(db, user, local_today(tz))
    token = _start_session(db, user, request)
    db.commit()
    _set_cookie(response, token)
    return {"user": user_out(user)}


@router.post("/login")
def login(body: LoginIn, request: Request, response: Response, db: DB) -> dict:
    key = _client_key(request)
    if not login_throttle.allowed(key):
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "Too many failed attempts. Wait five minutes.")
    user = db.scalar(select(User).where(User.email == body.email.strip().lower()))
    ok = verify_password(body.password, user.password_hash if user else _DUMMY_HASH)
    if not user or not ok or not user.is_active:
        login_throttle.record_failure(key)
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Wrong email or password.")
    login_throttle.reset(key)
    token = _start_session(db, user, request)
    db.commit()
    _set_cookie(response, token)
    return {"user": user_out(user)}


@router.post("/logout")
def logout(request: Request, response: Response, db: DB) -> dict:
    token = request.cookies.get(SESSION_COOKIE)
    if token:
        db.execute(delete(AuthSession).where(AuthSession.token_hash == hash_token(token)))
        db.commit()
    response.delete_cookie(SESSION_COOKIE, path="/")
    return {"ok": True}


@router.get("/me")
def me(user: CurrentUser) -> dict:
    return {"user": user_out(user)}


@router.post("/change-password")
def change_password(
    body: ChangePasswordIn,
    user: CurrentUser,
    db: DB,
    current: Annotated[AuthSession, Depends(get_current_session)],
) -> dict:
    if not verify_password(body.current_password, user.password_hash):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "The current password is wrong.")
    user.password_hash = hash_password(body.new_password)
    # Log out every other device; keep this one.
    db.execute(delete(AuthSession).where(AuthSession.user_id == user.id, AuthSession.id != current.id))
    db.commit()
    return {"ok": True}


@router.get("/sessions")
def sessions(
    user: CurrentUser, db: DB, current: Annotated[AuthSession, Depends(get_current_session)]
) -> list[dict]:
    rows = db.scalars(
        select(AuthSession).where(AuthSession.user_id == user.id).order_by(AuthSession.last_seen_at.desc())
    ).all()
    return [
        {
            "id": s.id,
            "created_at": iso(s.created_at),
            "last_seen_at": iso(s.last_seen_at),
            "expires_at": iso(s.expires_at),
            "user_agent": s.user_agent,
            "ip": s.ip,
            "current": s.id == current.id,
        }
        for s in rows
    ]


@router.delete("/sessions/{session_id}")
def revoke_session(session_id: int, user: CurrentUser, db: DB) -> dict:
    db.execute(delete(AuthSession).where(AuthSession.id == session_id, AuthSession.user_id == user.id))
    db.commit()
    return {"ok": True}

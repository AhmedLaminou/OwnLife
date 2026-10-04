"""FastAPI dependencies: the database session and the logged-in user."""

from __future__ import annotations

from datetime import timedelta
from typing import Annotated

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import get_db, utcnow
from app.models import AuthSession, Profile, User
from app.security import SESSION_COOKIE, hash_token

DB = Annotated[Session, Depends(get_db)]


def _unauthorized() -> HTTPException:
    return HTTPException(status.HTTP_401_UNAUTHORIZED, "Not logged in")


def get_current_session(request: Request, db: DB) -> AuthSession:
    token = request.cookies.get(SESSION_COOKIE)
    if not token:
        raise _unauthorized()
    session = db.scalar(select(AuthSession).where(AuthSession.token_hash == hash_token(token)))
    now = utcnow()
    if session is None or session.expires_at <= now:
        raise _unauthorized()
    # Sliding expiry: a session in regular use never times out.
    if now - session.last_seen_at > timedelta(hours=1):
        session.last_seen_at = now
        session.expires_at = now + timedelta(days=get_settings().session_days)
        db.commit()
    return session


def get_current_user(db: DB, session: Annotated[AuthSession, Depends(get_current_session)]) -> User:
    user = db.get(User, session.user_id)
    if user is None or not user.is_active:
        raise _unauthorized()
    return user


CurrentUser = Annotated[User, Depends(get_current_user)]


def get_profile(db: DB, user: CurrentUser) -> Profile:
    profile = db.get(Profile, user.id)
    if profile is None:  # created with the account; this only guards old rows
        profile = Profile(user_id=user.id)
        db.add(profile)
        db.commit()
    return profile


CurrentProfile = Annotated[Profile, Depends(get_profile)]

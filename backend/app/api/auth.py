import asyncio
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from secrets import token_urlsafe
from typing import Annotated
from uuid import UUID

import sqlalchemy as sa
from fastapi import APIRouter, Cookie, Depends, HTTPException, Response
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import get_services, get_settings
from app.auth import verify_password
from app.core.config import Settings
from app.db.identity import AuthSession, Role, User, UserRole
from app.infrastructure.services import Services

router = APIRouter(prefix="/api/v1/auth", tags=["authentication"])
COOKIE = "evorag_session"
SESSION_TTL = timedelta(hours=12)
TOKEN_TTL = timedelta(hours=1)

Runtime = Annotated[Services, Depends(get_services)]
Configuration = Annotated[Settings, Depends(get_settings)]
SessionCookie = Annotated[str | None, Cookie(alias=COOKIE)]
BearerCredentials = Annotated[
    HTTPAuthorizationCredentials | None, Depends(HTTPBearer(auto_error=False))
]


class Credentials(BaseModel):
    email: str = Field(min_length=3, max_length=320)
    password: str = Field(min_length=1, max_length=256)


class Principal(BaseModel):
    id: UUID
    email: str
    display_name: str
    roles: list[str]


class SessionToken(BaseModel):
    access_token: str


async def principal(session: AsyncSession, user: User) -> Principal:
    roles = await session.scalars(
        sa.select(Role.key)
        .join(UserRole, UserRole.role_id == Role.id)
        .where(UserRole.user_id == user.id)
        .order_by(Role.key)
    )
    return Principal(
        id=user.id, email=user.email, display_name=user.display_name, roles=list(roles)
    )


def digest(token: str) -> str:
    return sha256(token.encode()).hexdigest()


def session_token(bearer: BearerCredentials, cookie: SessionCookie = None) -> str | None:
    return bearer.credentials if bearer else cookie


Session = Annotated[str | None, Depends(session_token)]


async def current_user(services: Runtime, token: Session) -> Principal:
    if token is not None:
        async with services.sessions() as session:
            user = await session.scalar(
                sa.select(User)
                .join(AuthSession, AuthSession.user_id == User.id)
                .where(
                    AuthSession.token_hash == digest(token),
                    AuthSession.expires_at > sa.func.now(),
                    User.enabled.is_(True),
                )
            )
            if user is not None:
                return await principal(session, user)
    raise HTTPException(401, "Sign in to continue")


Actor = Annotated[Principal, Depends(current_user)]


async def create_session(
    credentials: Credentials, services: Services, lifetime: timedelta
) -> tuple[Principal, str]:
    async with services.sessions.begin() as session:
        user = await session.scalar(
            sa.select(User).where(sa.func.lower(User.email) == credentials.email.strip().casefold())
        )
        valid = await asyncio.to_thread(
            verify_password, credentials.password, user.password_hash if user else None
        )
        if user is None or not user.enabled or not valid:
            raise HTTPException(401, "Invalid email or password")
        token = token_urlsafe(32)
        session.add(
            AuthSession(
                user_id=user.id,
                token_hash=digest(token),
                expires_at=datetime.now(UTC) + lifetime,
            )
        )
        result = await principal(session, user)
    return result, token


@router.post("/login")
async def login(
    credentials: Credentials, response: Response, services: Runtime, settings: Configuration
) -> Principal:
    result, token = await create_session(credentials, services, SESSION_TTL)
    response.headers["Cache-Control"] = "no-store"
    response.set_cookie(
        COOKIE,
        token,
        httponly=True,
        secure=settings.session_cookie_secure,
        samesite="strict",
        max_age=int(SESSION_TTL.total_seconds()),
    )
    return result


@router.post("/token")
async def issue_token(
    credentials: Credentials, response: Response, services: Runtime
) -> SessionToken:
    _, token = await create_session(credentials, services, TOKEN_TTL)
    response.headers["Cache-Control"] = "no-store"
    return SessionToken(access_token=token)


@router.get("/me")
async def me(actor: Actor) -> Principal:
    return actor


@router.post("/logout", status_code=204)
async def logout(
    response: Response, services: Runtime, settings: Configuration, token: Session
) -> None:
    if token is not None:
        async with services.sessions.begin() as session:
            await session.execute(
                sa.delete(AuthSession).where(AuthSession.token_hash == digest(token))
            )
    response.delete_cookie(
        COOKIE, httponly=True, samesite="strict", secure=settings.session_cookie_secure
    )

"""Supabase online verification; no local signing secrets or custom cryptography."""

import base64
import json
import time
from dataclasses import dataclass
from typing import Annotated
from uuid import UUID

import httpx
from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.exc import IntegrityError

from app.db.session import SessionDependency
from app.models import RevokedSession, User

bearer = HTTPBearer(auto_error=False)


def unauthorized() -> HTTPException:
    return HTTPException(401, headers={"WWW-Authenticate": "Bearer", "Cache-Control": "no-store"})


@dataclass(frozen=True)
class Identity:
    user_id: UUID
    session_id: UUID
    email: str


async def verify_identity(
    request: Request,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
) -> Identity:
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise unauthorized()
    settings = request.app.state.settings
    if not settings.supabase_url or not settings.supabase_publishable_key:
        raise HTTPException(503)
    token = credentials.credentials
    if len(token) > 16384:
        raise unauthorized()
    issuer = str(settings.supabase_url).rstrip("/") + "/auth/v1"
    try:
        # Auth server verifies signature and expiry (including legacy signing keys).
        async with httpx.AsyncClient(timeout=10, follow_redirects=False) as client:
            response = await client.get(
                issuer + "/user",
                headers={
                    "Authorization": f"Bearer {token}",
                    "apikey": settings.supabase_publishable_key.get_secret_value(),
                },
            )
    except httpx.RequestError:
        raise HTTPException(503) from None
    if response.status_code in (401, 403):
        raise unauthorized()
    if response.status_code != 200:
        raise HTTPException(503)
    try:
        user = response.json()
        # Decode ONLY after successful provider validation; never trust this alone.
        parts = token.split(".")
        if len(parts) != 3:
            raise ValueError
        claims = json.loads(base64.urlsafe_b64decode(parts[1] + "=" * (-len(parts[1]) % 4)))
        audience = claims.get("aud")
        audiences = audience if isinstance(audience, list) else [audience]
        if (
            claims.get("iss") != issuer
            or settings.supabase_audience not in audiences
            or not isinstance(claims.get("exp"), (int, float))
            or claims["exp"] <= time.time()
            or claims.get("sub") != user["id"]
            or claims.get("nbf", 0) > time.time()
        ):
            raise ValueError
        identity = Identity(UUID(user["id"]), UUID(claims["session_id"]), user["email"])
        if not identity.email or len(identity.email) > 320:
            raise ValueError
    except (ValueError, TypeError, KeyError):
        raise unauthorized() from None
    if not user.get("email_confirmed_at") or claims.get("role") != "authenticated":
        raise HTTPException(403)
    return identity


def active_identity(
    identity: Annotated[Identity, Depends(verify_identity)],
    session: SessionDependency,
) -> Identity:
    if session.get(RevokedSession, identity.session_id):
        raise unauthorized()
    return identity


IdentityDependency = Annotated[Identity, Depends(active_identity)]


def current_user(identity: IdentityDependency, session: SessionDependency) -> User:
    user = session.get(User, identity.user_id)
    if user is None:
        user = User(id=identity.user_id, email=identity.email)
        session.add(user)
        try:
            session.commit()
        except IntegrityError:
            # Two first requests may provision the same identity concurrently.
            session.rollback()
            user = session.get(User, identity.user_id)
            if user is None:
                raise
    if user.email != identity.email:
        user.email = identity.email
        session.commit()
    return user


UserDependency = Annotated[User, Depends(current_user)]

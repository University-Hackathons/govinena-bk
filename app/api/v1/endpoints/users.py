from typing import Annotated

from fastapi import APIRouter, Depends, Response
from sqlalchemy.exc import IntegrityError

from app.core.auth import Identity, UserDependency, verify_identity
from app.db.session import SessionDependency
from app.models import RevokedSession
from app.schemas.user import UserResponse

router = APIRouter(tags=["users"])


@router.get("/users/me", response_model=UserResponse)
def me(user: UserDependency, response: Response):
    response.headers["Cache-Control"] = "no-store"
    return user


@router.post("/auth/logout", status_code=204)
def logout(
    identity: Annotated[Identity, Depends(verify_identity)],
    session: SessionDependency,
):
    # Idempotent; this session stays revoked across API workers and token refresh.
    if session.get(RevokedSession, identity.session_id) is None:
        session.add(RevokedSession(id=identity.session_id))
        try:
            session.commit()
        except IntegrityError:
            session.rollback()
            if session.get(RevokedSession, identity.session_id) is None:
                raise
    return Response(status_code=204, headers={"Cache-Control": "no-store"})

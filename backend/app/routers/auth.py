"""Shared password login and opaque browser sessions."""
from __future__ import annotations

import re

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, Field

from app.auth import (
    SESSION_COOKIE,
    SESSION_TTL_SECONDS,
    account_password_hash,
    account_username,
    bearer_token_matches,
    complete_login_attempt,
    create_initial_account,
    create_session,
    hash_password,
    password_matches,
    reserve_login_attempt,
    revoke_session,
    session_matches,
    token_required,
    update_account_password,
)

router = APIRouter()
_USERNAME = re.compile(r"^[a-z0-9][a-z0-9_.-]{2,31}$")
# Keep invalid-user and wrong-password login paths similarly expensive.
_DUMMY_HASH = hash_password("invalid-login-timing-only")


class SetupCredentials(BaseModel):
    username: str = Field(min_length=3, max_length=32)
    password: str = Field(min_length=12, max_length=256)


class LoginCredentials(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=256)


class NewPassword(BaseModel):
    password: str = Field(min_length=12, max_length=256)


def _normalized_username(value: str) -> str:
    return value.strip().lower()


def _set_session_cookie(response: Response, request: Request, session_id: str) -> None:
    response.set_cookie(
        key=SESSION_COOKIE,
        value=session_id,
        max_age=SESSION_TTL_SECONDS,
        path="/api",
        secure=request.url.scheme.lower() == "https",
        httponly=True,
        samesite="lax",
    )
    response.headers["Cache-Control"] = "no-store"


def _clear_session_cookie(response: Response, request: Request) -> None:
    response.delete_cookie(
        key=SESSION_COOKIE,
        path="/api",
        secure=request.url.scheme.lower() == "https",
        httponly=True,
        samesite="lax",
    )
    response.headers["Cache-Control"] = "no-store"


@router.get("/status")
def auth_status(request: Request, response: Response):
    """Public capability/status check; never returns a credential or session ID."""
    if not token_required():
        response.headers["Cache-Control"] = "no-store"
        return {"required": False}

    token_authenticated = bearer_token_matches(request.headers)
    session_authenticated = session_matches(request.cookies.get(SESSION_COOKIE))
    configured = account_username() is not None
    response.headers["Cache-Control"] = "no-store"
    return {
        "required": True,
        "password_configured": configured,
        "authenticated": token_authenticated or session_authenticated,
        "token_authenticated": token_authenticated,
        "session_authenticated": session_authenticated,
    }


@router.post("/setup", status_code=201)
def setup_shared_account(payload: SetupCredentials, request: Request, response: Response):
    """Create the initial account only from an already-valid bearer token."""
    if not token_required():
        raise HTTPException(status_code=409, detail="Autentificarea de rețea nu este activă.")
    if not bearer_token_matches(request.headers):
        raise HTTPException(status_code=401, detail="Configurarea inițială cere tokenul existent.")

    username = _normalized_username(payload.username)
    if not _USERNAME.fullmatch(username):
        raise HTTPException(
            status_code=422,
            detail="Numele trebuie să aibă 3–32 caractere: litere, cifre, punct, _ sau -.",
        )
    if account_username() is not None:
        raise HTTPException(status_code=409, detail="Contul comun este deja configurat.")

    encoded_hash = hash_password(payload.password)
    if not create_initial_account(username, encoded_hash):
        raise HTTPException(status_code=409, detail="Contul comun este deja configurat.")
    response.headers["Cache-Control"] = "no-store"
    return {"configured": True, "username": username}


@router.post("/login")
def login(payload: LoginCredentials, request: Request, response: Response):
    """Authenticate with the shared password and issue an HttpOnly session."""
    remote_host = request.client.host if request.client else "unknown"
    attempt_id = reserve_login_attempt(remote_host)
    if attempt_id is None:
        raise HTTPException(
            status_code=429,
            detail="Prea multe încercări. Încearcă din nou peste câteva minute.",
            headers={"Retry-After": "900"},
        )

    username = _normalized_username(payload.username)
    encoded_hash = account_password_hash(username)
    if encoded_hash is None:
        password_matches(payload.password, _DUMMY_HASH)
        raise HTTPException(status_code=401, detail="Nume de utilizator sau parolă incorectă.")
    if not password_matches(payload.password, encoded_hash):
        raise HTTPException(status_code=401, detail="Nume de utilizator sau parolă incorectă.")

    complete_login_attempt(attempt_id)
    session_id, _expires_at = create_session(username)
    _set_session_cookie(response, request, session_id)
    return {"authenticated": True, "username": username}


@router.post("/logout", status_code=204)
def logout(request: Request, response: Response):
    """Revoke the current server-side session and clear its browser cookie."""
    revoke_session(request.cookies.get(SESSION_COOKIE))
    _clear_session_cookie(response, request)
    response.status_code = 204
    return response


@router.post("/password")
def change_shared_password(payload: NewPassword, request: Request, response: Response):
    """Explicit password reset requiring the existing bearer credential."""
    if not token_required() or not bearer_token_matches(request.headers):
        raise HTTPException(status_code=403, detail="Schimbarea parolei cere tokenul existent.")
    if account_username() is None:
        raise HTTPException(status_code=409, detail="Contul comun nu este configurat.")
    if not update_account_password(hash_password(payload.password)):
        raise HTTPException(status_code=409, detail="Contul comun nu este configurat.")
    response.headers["Cache-Control"] = "no-store"
    return {"updated": True}

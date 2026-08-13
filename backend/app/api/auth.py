"""Auth for private dashboard: session cookie (web) + HTTP Basic (API)."""

from __future__ import annotations

import secrets
from urllib.parse import quote

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPBasic, HTTPBasicCredentials

from app.config import get_settings

security = HTTPBasic(auto_error=False)


def _valid_credentials(username: str, password: str) -> bool:
    settings = get_settings()
    user_ok = secrets.compare_digest(
        username.encode("utf-8"),
        settings.dashboard_username.encode("utf-8"),
    )
    pass_ok = secrets.compare_digest(
        password.encode("utf-8"),
        settings.dashboard_password.get_secret_value().encode("utf-8"),
    )
    return bool(user_ok and pass_ok)


def login_user(request: Request, username: str) -> None:
    request.session.clear()
    request.session["user"] = username


def logout_user(request: Request) -> None:
    request.session.clear()


def require_user(
    request: Request,
    credentials: HTTPBasicCredentials | None = Depends(security),
) -> str:
    """API auth: session cookie or HTTP Basic."""
    session_user = request.session.get("user")
    if isinstance(session_user, str) and session_user:
        return session_user

    if credentials and _valid_credentials(credentials.username, credentials.password):
        return credentials.username

    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid credentials",
        headers={"WWW-Authenticate": "Basic"},
    )


def require_web_user(
    request: Request,
    credentials: HTTPBasicCredentials | None = Depends(security),
) -> str:
    """Browser auth: prefer session; accept Basic; otherwise redirect to /login."""
    session_user = request.session.get("user")
    if isinstance(session_user, str) and session_user:
        return session_user

    if credentials and _valid_credentials(credentials.username, credentials.password):
        login_user(request, credentials.username)
        return credentials.username

    next_path = request.url.path
    if request.url.query:
        next_path = f"{next_path}?{request.url.query}"
    raise HTTPException(
        status_code=status.HTTP_303_SEE_OTHER,
        headers={"Location": f"/login?next={quote(next_path, safe='')}"},
    )

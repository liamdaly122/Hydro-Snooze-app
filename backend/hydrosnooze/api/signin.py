"""Signing in and out, over HTTP. The rules are in access.py.

    GET    /api/auth                      whether to show the sign-in, asked first
    POST   /api/auth/login                the password, for a cookie
    POST   /api/auth/logout               this device
    POST   /api/auth/logout-everywhere    every device, this one included

The first three are the only routes under /api that answer without being
signed in, because they are how you get signed in. Everything else is checked
in main.py before it reaches a route.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, Field

from ..access import COOKIE, SESSION_LASTS, SignInRefused, Verdict
from ..service import Service

router = APIRouter(prefix="/api")

#: The routes the check in main.py lets through without a session.
OPEN = frozenset({"/api/auth", "/api/auth/login", "/api/auth/logout"})


class SignInBody(BaseModel):
    password: str = Field(min_length=1, max_length=200)


def _service(request: Request) -> Service:
    return request.app.state.service


def client_host(request: Request) -> str | None:
    return request.client.host if request.client else None


def verdict_for(request: Request) -> Verdict:
    return _service(request).access.admit(
        client_host=client_host(request),
        authorization=request.headers.get("authorization"),
        cookie=request.cookies.get(COOKIE),
    )


@router.get("/auth")
async def get_auth(request: Request) -> dict[str, object]:
    return _service(request).access.status(verdict_for(request))


@router.post("/auth/login")
async def post_login(request: Request, body: SignInBody, response: Response) -> dict[str, object]:
    access = _service(request).access
    try:
        token = access.sign_in(
            body.password,
            client_host=client_host(request),
            user_agent=request.headers.get("user-agent", ""),
        )
    except SignInRefused as exc:
        raise HTTPException(exc.status, exc.reason) from exc

    via = access.via(client_host(request))
    response.set_cookie(
        COOKIE,
        token,
        max_age=int(SESSION_LASTS.total_seconds()),
        httponly=True,
        # Lax, not Strict. Signing in to Withings comes back to this site from
        # theirs, and a Strict cookie is left behind on exactly that trip, so
        # the callback would arrive signed out and the connection would fail.
        # Lax still keeps it off every request another site starts in the
        # background, which is the attack worth stopping.
        samesite="lax",
        # Only through Tailscale, which is always https. The home network is
        # plain http, and a Secure cookie there would never be sent back.
        secure=via == "tailscale",
        path="/",
    )
    return {"required": True, "signed_in": True, "via": via, "refused": None}


@router.post("/auth/logout")
async def post_logout(request: Request, response: Response) -> dict[str, object]:
    access = _service(request).access
    access.sign_out(request.cookies.get(COOKIE))
    response.delete_cookie(COOKIE, path="/")
    via = access.via(client_host(request))
    return {"required": access.required, "signed_in": not access.required, "via": via, "refused": None}


@router.post("/auth/logout-everywhere")
async def post_logout_everywhere(request: Request, response: Response) -> dict[str, object]:
    """Every device, this one included. For a phone that has gone missing."""
    access = _service(request).access
    if not access.required:
        raise HTTPException(409, "There is no password set, so nothing is signed in.")
    access.sign_out_everywhere()
    response.delete_cookie(COOKIE, path="/")
    return {"required": True, "signed_in": False, "via": access.via(client_host(request)), "refused": None}

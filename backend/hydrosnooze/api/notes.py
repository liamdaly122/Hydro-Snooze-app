"""How a night felt, over HTTP. See notes.py.

    GET  /api/notes?date=YYYY-MM-DD   one morning's note, and the choices
    PUT  /api/notes/YYYY-MM-DD        change what was sent, leave the rest

Keyed by the wake morning like every other night. Answering in the evening is
allowed as far as the coming morning, for a tag already known then: a drink
with dinner is a fact by nine o'clock.
"""

from __future__ import annotations

from datetime import date, timedelta

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field

from ..notes import BY_KEY, FELT, TAGS, NightNote, note_json
from ..service import Service

router = APIRouter(prefix="/api")

DAY = r"^\d{4}-\d{2}-\d{2}$"


class NotePatch(BaseModel):
    """Only what is sent changes. A null rating or felt clears it."""

    rating: int | None = Field(default=None, ge=1, le=5)
    felt: str | None = None
    tags: list[str] | None = None


def _service(request: Request) -> Service:
    return request.app.state.service


def _morning(service: Service, wake_on: str) -> str:
    try:
        on = date.fromisoformat(wake_on)
    except ValueError as exc:
        raise HTTPException(422, f"{wake_on} is not a date") from exc
    if on > service.clock.now().date() + timedelta(days=1):
        raise HTTPException(422, "That morning has not come yet.")
    return on.isoformat()


@router.get("/notes")
async def get_note(request: Request, date: str = Query(pattern=DAY)) -> dict[str, object]:
    service = _service(request)
    wake_on = _morning(service, date)
    return note_json(service.db.night_note(wake_on), wake_on)


@router.put("/notes/{wake_on}")
async def put_note(request: Request, wake_on: str, patch: NotePatch) -> dict[str, object]:
    service = _service(request)
    wake_on = _morning(service, wake_on)
    was = service.db.night_note(wake_on) or NightNote(wake_on)
    sent = patch.model_fields_set

    felt = was.felt
    if "felt" in sent:
        if patch.felt is not None and patch.felt not in FELT:
            raise HTTPException(422, f"felt must be one of {', '.join(FELT)}")
        felt = patch.felt

    tags = was.tags
    if "tags" in sent:
        given = patch.tags or []
        unknown = [t for t in given if t not in BY_KEY]
        if unknown:
            raise HTTPException(422, f"No such tag: {', '.join(unknown)}")
        # In the list's own order, once each, whatever order they were tapped in.
        tags = tuple(t.key for t in TAGS if t.key in given)

    note = NightNote(
        wake_on=wake_on,
        rating=patch.rating if "rating" in sent else was.rating,
        felt=felt,
        tags=tags,
    )
    service.db.save_night_note(note, service.clock.now())
    return note_json(note, wake_on)
